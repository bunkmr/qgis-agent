# -*- coding: utf-8 -*-
"""
API 文档检索器 — 在执行 PyQGIS 代码前检索相关 API 签名。

支持两种检索模式:
1. 关键词检索: 从用户输入和工具调用中提取 API 关键词，FTS5 搜索
2. 定向检索: 针对特定类名搜索所有方法
"""

import re
import json
from typing import Optional

from .doc_store import DocStore


# ── API 关键词提取 ──

# 常见的 PyQGIS/GDAL API 关键词模式
_API_KEYWORD_PATTERNS = [
    # QGIS 核心类
    r"Qgs\w+",                     # QgsVectorLayer, QgsGeometry...
    # GDAL/OGR
    r"gdal\.\w+", r"ogr\.\w+", r"osgeo\.\w+",
    # Processing 算法
    r"native:\w+", r"gdal:\w+", r"qgis:\w+", r"saga:\w+", r"grass7:\w+",
    # 常见操作动词
    r"\b(buffer|clip|intersect|dissolve|merge|reproject|simplify)\b",
    # QGIS 方法调用
    r"\.(addFeature|setGeometry|setAttributes|fields|featureCount|"
    r"getFeatures|selectByExpression|setRenderer|setLabeling|"
    r"materialize|transform|asPoint|asWkt|asJson|"
    r"setExtent|setCrs|commitChanges|startEditing|"
    r"processing\.run)\b",
    # 参数关键词
    r"\b(INPUT|OUTPUT|DISTANCE|FIELD|CRS|LAYER|EXTENT)\b",
]

# 中文到英文关键词的映射（用于从用户输入中提取）
_CN_TO_EN_KEYWORDS = {
    "缓冲区": "buffer",
    "裁剪": "clip",
    "相交": "intersection",
    "合并": "merge dissolve",
    "投影": "reproject transform CRS",
    "简化": "simplify",
    "字段计算": "field calculator",
    "筛选": "selectByExpression filter",
    "标注": "labeling setLabeling",
    "样式": "renderer setRenderer symbol",
    "导出": "export write save",
    "导入": "import load read",
    "坐标系": "CRS QgsCoordinateReferenceSystem",
    "几何": "geometry QgsGeometry",
    "要素": "feature QgsFeature",
    "属性表": "fields attributes featureCount",
    "渲染": "render QgsMapRenderer",
    "选择": "select selection",
    "编辑": "edit editing startEditing commitChanges",
    "创建图层": "QgsVectorLayer addMapLayer",
    "删除": "remove delete",
    "空间查询": "spatial index QgsSpatialIndex QgsFeatureRequest",
    "拓扑": "topology isValid isGeosValid",
    "栅格": "raster QgsRasterLayer",
    "等高线": "contour gdal:contour",
    "网格": "grid mesh",
}


class APIDocRetriever:
    """本地 PyQGIS API 文档检索器。

    使用 SQLite FTS5 全文搜索，零额外依赖。
    """

    def __init__(self, store: DocStore = None):
        self.store = store or DocStore()

    # ── 主要检索接口 ──

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """根据查询文本检索相关 API 文档。

        Args:
            query: 自然语言查询或 API 关键词
            top_k: 返回结果数量

        Returns:
            [{"class_name": "QgsGeometry", "full_signature": "...", ...}, ...]
        """
        # 提取关键词
        keywords = self._extract_api_keywords(query)
        if not keywords:
            keywords = query.split()

        # FTS5 检索
        results = self.store.search_fts(" ".join(keywords), top_k)

        # 如果结果太少，用原始 query 再搜一次
        if len(results) < 2 and len(query) > 3:
            extra = self.store.search_fts(query, top_k)
            seen_ids = {r["id"] for r in results}
            for r in extra:
                if r["id"] not in seen_ids:
                    results.append(r)
                    seen_ids.add(r["id"])

        return results[:top_k]

    def search_for_tool_call(self, tool_name: str, tool_args: dict) -> list[dict]:
        """针对工具调用参数的定向检索。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数字典

        Returns:
            相关 API 文档列表
        """
        queries = []

        if tool_name == "execute_pyqgis":
            code = tool_args.get("code", "")
            if code:
                # 从代码中提取 API 类名和方法调用
                api_names = self._extract_api_names_from_code(code)
                if api_names:
                    queries.extend(api_names)

        elif tool_name == "execute_processing":
            algo = tool_args.get("algorithm", "")
            if algo:
                queries.append(algo)
            # 从参数中提取 INPUT 类型关键词
            params = tool_args.get("parameters", {})
            for k, v in params.items():
                if isinstance(v, str) and len(v) < 50:
                    queries.append(v)

        if not queries:
            return []

        # 合并所有查询的结果
        all_results = []
        seen = set()
        for q in queries[:3]:  # 最多 3 个查询
            results = self.search(q, top_k=3)
            for r in results:
                if r["id"] not in seen:
                    all_results.append(r)
                    seen.add(r["id"])

        return all_results[:5]

    def search_from_user_intent(self, user_input: str, top_k: int = 5) -> list[dict]:
        """从用户原始输入中提取意图并检索相关 API。

        先做中英文关键词映射，再检索。
        """
        # 扩展查询：添加中英文关键词映射
        expanded_queries = [user_input]
        for cn, en in _CN_TO_EN_KEYWORDS.items():
            if cn in user_input:
                expanded_queries.append(en)

        combined = " ".join(expanded_queries)
        return self.search(combined, top_k)

    # ── 格式化输出 ──

    def format_as_context(self, results: list[dict], max_chars: int = 3000) -> str:
        """将检索结果格式化为 LLM 可读的上下文文本。

        Args:
            results: search() 返回的结果列表
            max_chars: 最大字符数

        Returns:
            格式化的 API 文档片段
        """
        if not results:
            return ""

        lines = ["## 相关 PyQGIS API 文档（检索结果）\n"]
        char_count = 0

        for i, r in enumerate(results, 1):
            sig = r.get("full_signature", "")
            desc = r.get("description", "")
            example = r.get("example_code", "")
            params_str = r.get("parameters", "[]")
            deprecated = r.get("deprecated", 0)

            # 解析参数
            try:
                params = json.loads(params_str) if isinstance(params_str, str) else params_str
            except (json.JSONDecodeError, TypeError):
                params = []

            entry = f"### {i}. `{sig}`\n"
            if deprecated:
                entry += "⚠️ **已废弃**\n"
            if desc:
                entry += f"{desc}\n"
            if params:
                param_lines = []
                for p in params[:5]:
                    pname = p.get("name", "?")
                    ptype = p.get("type", "")
                    pdesc = p.get("description", "")
                    param_lines.append(f"  - `{pname}`: {ptype}" + (f" — {pdesc}" if pdesc else ""))
                entry += "\n".join(param_lines) + "\n"
            if example:
                entry += f"\n```python\n{example}\n```\n"

            if char_count + len(entry) > max_chars:
                break

            lines.append(entry)
            char_count += len(entry)

        return "\n".join(lines)

    # ── 关键词提取 ──

    def _extract_api_keywords(self, text: str) -> list[str]:
        """从文本中提取 PyQGIS API 关键词"""
        keywords = set()
        for pattern in _API_KEYWORD_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            keywords.update(matches)
        return list(keywords)[:10]

    def _extract_api_names_from_code(self, code: str) -> list[str]:
        """从 PyQGIS 代码中提取 API 类名和方法名"""
        names = set()

        # 提取 Qgs* 类名
        class_matches = re.findall(r"(Qgs\w+)", code)
        names.update(class_matches)

        # 提取方法调用: obj.method_name(
        method_matches = re.findall(r"\.(\w+)\s*\(", code)
        # 过滤常见 Python 内置方法和通用操作
        common_methods = {
            "append", "extend", "insert", "remove", "pop", "get", "set",
            "items", "keys", "values", "update", "copy", "clear",
            "len", "range", "print", "str", "int", "float", "list", "dict",
            "open", "close", "read", "write", "format", "join", "split",
            "startswith", "endswith", "replace", "strip", "lower", "upper",
            "addWidget", "setLayout", "show", "exec_", "setWindowTitle",
        }
        for m in method_matches:
            if m not in common_methods:
                names.add(m)

        # 提取 processing 算法
        algo_matches = re.findall(r"processing\.run\(['\"]([^'\"]+)['\"]", code)
        names.update(algo_matches)

        return list(names)[:8]


# ── 全局单例（由 qgis_agent.py 初始化） ──

_retriever_instance: Optional[APIDocRetriever] = None


def get_retriever() -> APIDocRetriever:
    """获取全局检索器实例（自动初始化）"""
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = APIDocRetriever()
    return _retriever_instance


def init_retriever(store: DocStore = None) -> APIDocRetriever:
    """初始化全局检索器"""
    global _retriever_instance
    _retriever_instance = APIDocRetriever(store)
    return _retriever_instance
