# -*- coding: utf-8 -*-
"""
Cookbook 自我进化模块 — 成功案例自动归档与检索。

核心功能:
1. 任务完成后自动归档成功案例
2. 质量评分（成功度 × 复杂度）
3. 执行前检索相似案例，提供参考
4. 越用越聪明 — 案例库随时间增长
"""

import json
from typing import Optional

from .doc_store import DocStore


class Cookbook:
    """自我进化案例库。

    自动归档成功的任务执行记录，并支持在执行前检索相似案例。
    """

    def __init__(self, store: DocStore = None):
        self.store = store or DocStore()

    # ── 归档 ──

    def archive(
        self,
        user_input: str,
        tools_used: list[str],
        code_snippet: str = "",
        success_rating: int = 5,
        complexity_rating: int = 3,
        task_summary: str = "",
    ) -> int:
        """归档一个成功案例到 Cookbook。

        Args:
            user_input: 用户的原始输入
            tools_used: 使用的工具列表，如 ["get_qgis_info", "execute_pyqgis"]
            code_snippet: 执行的 PyQGIS 代码（如有）
            success_rating: 成功度评分 1-5（5=完美成功）
            complexity_rating: 复杂度评分 1-5（1=单步操作，5=多步骤复杂任务）
            task_summary: 任务摘要（可选，不传则自动生成）

        Returns:
            插入的 rowid
        """
        if not task_summary:
            task_summary = self._generate_summary(user_input, tools_used, code_snippet)

        # 计算质量评分
        quality_score = success_rating * complexity_rating

        entry = {
            "task_summary": task_summary,
            "user_input": user_input,
            "tools_used": tools_used,
            "code_snippet": code_snippet,
            "success_rating": success_rating,
            "complexity_rating": complexity_rating,
            "quality_score": quality_score,
        }

        return self.store.insert_cookbook_entry(entry)

    def archive_from_agent_result(
        self,
        user_input: str,
        tool_calls_log: list[dict],
        final_response: str,
        success: bool = True,
    ) -> Optional[int]:
        """从 Agent 执行结果自动归档。

        Args:
            user_input: 用户原始输入
            tool_calls_log: 工具调用日志（agent_chat 中的 all_tool_calls_log）
            final_response: LLM 最终回复
            success: 是否成功

        Returns:
            插入的 rowid，如果不够格归档则返回 None
        """
        tools_used = [tc.get("tool", "unknown") for tc in tool_calls_log]

        # 提取代码
        code_snippet = ""
        for tc in tool_calls_log:
            if tc.get("tool") == "execute_pyqgis":
                code_snippet = tc.get("args", {}).get("code", "")
                break

        # 计算复杂度
        complexity = min(len(tools_used), 5)
        if len(tools_used) == 0:
            complexity = 1
        elif any(t == "execute_pyqgis" for t in tools_used):
            complexity = min(complexity + 1, 5)

        # 成功度
        success_rating = 5 if success else 2

        # 只归档有一定价值的案例（至少用了工具或生成了代码）
        if not tools_used and not code_snippet:
            return None

        return self.archive(
            user_input=user_input,
            tools_used=tools_used,
            code_snippet=code_snippet,
            success_rating=success_rating,
            complexity_rating=complexity,
        )

    # ── 检索 ──

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """搜索相似的 Cookbook 案例。

        返回质量评分最高的匹配案例。
        """
        return self.store.search_cookbook(query, top_k)

    def search_for_task(self, user_input: str, top_k: int = 3) -> list[dict]:
        """针对用户当前任务搜索相关案例。

        提取中文关键词并扩展查询。
        """
        # 扩展关键词
        expanded = self._expand_query(user_input)
        results = self.store.search_cookbook(expanded, top_k)
        if len(results) < top_k:
            # 用原始输入再搜
            extra = self.store.search_cookbook(user_input, top_k)
            seen = {r["id"] for r in results}
            for r in extra:
                if r["id"] not in seen:
                    results.append(r)
                    seen.add(r["id"])
        return results[:top_k]

    def format_as_context(self, results: list[dict], max_chars: int = 2000) -> str:
        """将 Cookbook 案例格式化为 LLM 可读的上下文。

        Args:
            results: search() 或 search_for_task() 返回的案例列表
            max_chars: 最大字符数

        Returns:
            格式化的案例参考文本
        """
        if not results:
            return ""

        lines = ["## 历史成功案例（Cookbook 参考）\n"]
        lines.append("以下是你过去成功执行过的类似任务，可以参考其方法：\n")
        char_count = 0

        for i, entry in enumerate(results, 1):
            summary = entry.get("task_summary", "")
            code = entry.get("code_snippet", "")
            tools = entry.get("tools_used", "[]")

            try:
                tools_list = json.loads(tools) if isinstance(tools, str) else tools
            except (json.JSONDecodeError, TypeError):
                tools_list = []

            block = f"### 案例 {i}: {summary}\n"
            block += f"用户需求: {entry.get('user_input', '')}\n"
            if tools_list:
                block += f"使用工具: {', '.join(tools_list)}\n"
            if code:
                block += f"\n```python\n{code}\n```\n"

            if char_count + len(block) > max_chars:
                break

            lines.append(block)
            char_count += len(block)

        return "\n".join(lines)

    # ── 统计 ──

    def get_stats(self) -> dict:
        """获取 Cookbook 统计信息"""
        return self.store.get_cookbook_stats()

    # ── 内部方法 ──

    def _generate_summary(
        self, user_input: str, tools_used: list[str], code_snippet: str = ""
    ) -> str:
        """从用户输入和工具调用生成任务摘要"""
        # 截断过长的输入
        summary = user_input[:100]
        if len(user_input) > 100:
            summary += "..."

        # 添加工具信息
        if tools_used:
            tool_names = [t for t in tools_used if t not in ("save_memory", "load_memory")]
            if tool_names:
                summary += f" [{', '.join(tool_names[:3])}]"

        return summary

    def _expand_query(self, user_input: str) -> str:
        """扩展查询关键词"""
        # 提取中文地理操作关键词
        keywords = []
        cn_keywords = {
            "缓冲区": "buffer",
            "裁剪": "clip intersect",
            "相交": "intersection",
            "合并": "merge dissolve",
            "投影": "reproject CRS",
            "简化": "simplify",
            "标注": "labeling",
            "样式": "renderer symbol",
            "导出": "export save",
            "选择": "select filter",
            "编辑": "edit",
            "渲染": "render",
            "字段计算": "field calculator",
            "空间查询": "spatial query",
        }
        for cn, en in cn_keywords.items():
            if cn in user_input:
                keywords.append(en)

        if keywords:
            return user_input + " " + " ".join(keywords)
        return user_input
