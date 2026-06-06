# -*- coding: utf-8 -*-
"""
API 文档生成器 — 从 QGIS 运行时提取 PyQGIS/GDAL API 文档。

在插件首次安装或用户触发时运行，将核心 API 的签名和文档
写入 SQLite FTS5 数据库，供后续检索使用。

数据来源:
1. Python inspect 反射 — 从 QGIS 核心类提取方法签名
2. QGIS Processing 注册表 — 提取所有可用算法
3. 硬编码的手动补充 — 常见但反射不到的 API 信息
"""

import inspect
import json
import os
from typing import Optional

from .doc_store import DocStore


# ── 核心 QGIS 类列表（需要提取文档的类） ──

_CORE_QGIS_CLASSES = [
    # 图层
    ("qgis.core", "QgsVectorLayer"),
    ("qgis.core", "QgsRasterLayer"),
    ("qgis.core", "QgsMapLayer"),
    # 几何
    ("qgis.core", "QgsGeometry"),
    ("qgis.core", "QgsPoint"),
    ("qgis.core", "QgsPointXY"),
    ("qgis.core", "QgsRectangle"),
    # 要素
    ("qgis.core", "QgsFeature"),
    ("qgis.core", "QgsFields"),
    ("qgis.core", "QgsField"),
    # 项目
    ("qgis.core", "QgsProject"),
    ("qgis.core", "QgsApplication"),
    # 坐标系
    ("qgis.core", "QgsCoordinateReferenceSystem"),
    ("qgis.core", "QgsCoordinateTransform"),
    # 渲染
    ("qgis.core", "QgsMapSettings"),
    ("qgis.core", "QgsMapRendererParallelJob"),
    # 符号
    ("qgis.core", "QgsFillSymbol"),
    ("qgis.core", "QgsLineSymbol"),
    ("qgis.core", "QgsMarkerSymbol"),
    ("qgis.core", "QgsSingleSymbolRenderer"),
    ("qgis.core", "QgsCategorizedSymbolRenderer"),
    ("qgis.core", "QgsGraduatedSymbolRenderer"),
    ("qgis.core", "QgsPalLayerSettings"),
    ("qgis.core", "QgsVectorLayerSimpleLabeling"),
    ("qgis.core", "QgsTextFormat"),
    # 空间索引
    ("qgis.core", "QgsSpatialIndex"),
    ("qgis.core", "QgsFeatureRequest"),
    # 距离/单位
    ("qgis.core", "QgsDistanceArea"),
    ("qgis.core", "QgsUnitTypes"),
    # WKB
    ("qgis.core", "QgsWkbTypes"),
    # 枚举
    ("qgis.core", "Qgis"),
    # 工具
    ("qgis.gui", "QgsMapCanvas"),
]


def _safe_import_class(module_name: str, class_name: str):
    """安全导入一个类，失败返回 None"""
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name, None)
    except Exception:
        return None


def _inspect_class(cls, class_name: str, source: str = "runtime") -> list[dict]:
    """通过 inspect 反射提取一个类的所有公开方法文档。

    Returns:
        [{"class_name": ..., "method_name": ..., "full_signature": ..., ...}, ...]
    """
    docs = []
    try:
        for name, method in inspect.getmembers(cls, inspect.ismethod):
            if name.startswith("_"):
                continue
            try:
                sig = str(inspect.signature(method))
            except (ValueError, TypeError):
                sig = "(...)"

            # 提取 docstring 第一段
            desc = ""
            if method.__doc__:
                desc = method.__doc__.strip().split("\n\n")[0][:500]

            # 提取参数信息
            params = []
            try:
                for pname, param in inspect.signature(method).parameters.items():
                    if pname == "self":
                        continue
                    param_type = ""
                    if param.annotation != inspect.Parameter.empty:
                        param_type = str(param.annotation)
                    params.append({
                        "name": pname,
                        "type": param_type,
                        "default": str(param.default) if param.default != inspect.Parameter.empty else "",
                    })
            except (ValueError, TypeError):
                pass

            docs.append({
                "class_name": class_name,
                "method_name": name,
                "full_signature": f"{class_name}.{name}{sig}",
                "description": desc,
                "parameters": params,
                "return_type": "",
                "source": source,
            })
    except Exception:
        pass
    return docs


def _extract_processing_algorithms() -> list[dict]:
    """从 QGIS Processing 注册表提取所有算法签名"""
    docs = []
    try:
        from qgis.core import QgsApplication
        registry = QgsApplication.processingRegistry()
        if registry is None:
            return docs

        for alg in registry.algorithms():
            try:
                alg_id = alg.id()
                alg_name = alg.displayName()
                params = []
                for p in alg.parameterDefinitions():
                    params.append({
                        "name": p.name(),
                        "type": p.type(),
                        "description": p.description() if hasattr(p, 'description') else "",
                    })

                docs.append({
                    "class_name": "processing",
                    "method_name": alg_id,
                    "full_signature": f"processing.run('{alg_id}', {{...}})",
                    "description": f"{alg_name} — {alg.shortDescription() if hasattr(alg, 'shortDescription') else ''}",
                    "parameters": params,
                    "return_type": "dict",
                    "source": "processing_registry",
                })
            except Exception:
                continue
    except Exception:
        pass
    return docs


# ── 手动补充的 API 文档（inspect 难以获取的） ──

_MANUAL_API_DOCS = [
    # 常用操作速查
    {
        "class_name": "QgsVectorLayer",
        "method_name": "常用操作速查",
        "full_signature": "QgsVectorLayer 常用操作",
        "description": (
            "featureCount() → int: 返回要素数量。"
            "fields() → QgsFields: 返回字段列表，遍历: for field in layer.fields()。"
            "getFeatures() → QgsFeatureIterator: 迭代要素。"
            "startEditing() / commitChanges() / rollBack(): 编辑会话管理。"
            "addFeature(feature) / updateFeature(feature) / deleteFeature(fid): 要素CRUD。"
            "selectByExpression(expr) → QgsFeatureIterator: 按表达式选择。"
            "setRenderer(renderer): 设置渲染器。"
            "setLabeling(labeling) / setLabelsEnabled(bool): 标注设置。"
            "setCrs(crs): 设置坐标系。"
            "materialize(QgsFeatureRequest): 物化过滤后的要素。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsGeometry",
        "method_name": "常用操作速查",
        "full_signature": "QgsGeometry 常用操作",
        "description": (
            "buffer(distance, segments) → QgsGeometry: 缓冲区。"
            "simplify(tolerance) → QgsGeometry: 简化。"
            "intersection(geom) → QgsGeometry: 相交。"
            "combine(geom) → QgsGeometry: 合并。"
            "difference(geom) → QgsGeometry: 差集。"
            "asPoint() → QgsPointXY: 转为点。"
            "asWkt(precision) → str: 转为WKT。"
            "asJson(precision) → str: 转为GeoJSON。"
            "get() → QgsAbstractGeometry: 获取底层几何对象。"
            "vertexAt(i) → QgsPoint: 获取第i个顶点。"
            "isGeosValid() → bool: GEOS有效性检查（注意: 不是 isValid()）。"
            "isEmpty() → bool: 是否为空。"
            "type() → QgsWkbTypes.GeometryType: 几何类型。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsFeatureRequest",
        "method_name": "常用操作速查",
        "full_signature": "QgsFeatureRequest 常用操作",
        "description": (
            "setFilterExpression(expr) / setFilterFids([...]) / setFilterRect(rect): 设置过滤。"
            "setLimit(n): 限制返回数量。"
            "NoFlags / ExactIntersect: 空间过滤标志。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsCoordinateTransform",
        "method_name": "构造和变换",
        "full_signature": "QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())",
        "description": (
            "构造函数需要三个参数: 源CRS, 目标CRS, QgsCoordinateTransformContext。"
            "通常第三个参数传 QgsProject.instance()。"
            "transform(QgsGeometry) → QgsGeometry: 变换几何。"
            "transform(point) → QgsPointXY: 变换点。"
            "transformBoundingBox(rect) → QgsRectangle: 变换范围。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsProject",
        "method_name": "常用操作速查",
        "full_signature": "QgsProject.instance() 常用操作",
        "description": (
            "mapLayers() → dict: 返回 {layer_id: QgsMapLayer}。"
            "addMapLayer(layer) / removeMapLayer(id): 图层管理。"
            "crs() → QgsCoordinateReferenceSystem: 项目坐标系。"
            "fileName() → str: 项目文件路径。"
            "write(path) → bool / read(path) → bool: 保存/加载项目。"
            "layerTreeRoot() → QgsLayerTreeGroup: 图层树。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsPalLayerSettings",
        "method_name": "标注设置速查",
        "full_signature": "QgsPalLayerSettings 标注配置",
        "description": (
            "fieldName = '字段名': 设置标注字段。"
            "setFormat(QgsTextFormat): 设置文本格式。"
            "placementSettings: QGIS 3.30+ 使用 QgsLabelPlacementSettings。"
            "placement: 旧版QGIS直接设置整数值 (0=AroundPoint, 1=OverPoint, 2=Line, 3=Curved, 4=Horizontal)。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsMapCanvas",
        "method_name": "地图画布操作",
        "full_signature": "iface.mapCanvas() 常用操作",
        "description": (
            "extent() → QgsRectangle: 当前视图范围。"
            "setExtent(rect) / zoomToFullExtent() / refresh(): 视图控制。"
            "scale() → float: 当前比例尺分母。"
            "setDestinationCrs(crs): 设置目标坐标系。"
        ),
        "source": "manual",
    },
    {
        "class_name": "processing",
        "method_name": "常用算法速查",
        "full_signature": "processing.run() 常用算法",
        "description": (
            "native:buffer — 缓冲区: INPUT, DISTANCE, SEGMENTS, OUTPUT。"
            "native:clip — 裁剪: INPUT, OVERLAY, OUTPUT。"
            "native:intersection — 相交: INPUT, OVERLAY, OUTPUT。"
            "native:dissolve — 融合: INPUT, FIELD, OUTPUT。"
            "native:fieldcalculator — 字段计算: INPUT, FIELD_NAME, FIELD_TYPE, FORMULA, OUTPUT。"
            "native:reprojectlayer — 重投影: INPUT, TARGET_CRS, OUTPUT。"
            "native:selectbyexpression — 表达式选择: INPUT, EXPRESSION。"
            "native:extractbyexpression — 表达式提取: INPUT, EXPRESSION, OUTPUT。"
            "native:fixgeometries — 修复几何: INPUT, OUTPUT。"
            "native:exporttospreadsheet — 导出表格: LAYERS, OUTPUT。"
            "gdal:contour — 等高线: INPUT, BAND, INTERVAL, OUTPUT。"
            "gdal:cliprasterbyextent — 栅格裁剪: INPUT, PROJWIN, OUTPUT。"
            "gdal:warpreproject — 栅格重投影: INPUT, SOURCE_CRS, TARGET_CRS, OUTPUT。"
            "qgis:exporttospreadsheet — 导出表格: LAYERS, OUTPUT。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsRendererCategory",
        "method_name": "分类渲染",
        "full_signature": "QgsRendererCategory(value, symbol, label)",
        "description": (
            "构造函数: QgsRendererCategory(value, QgsFillSymbol(), label)。"
            "setValue(value) / value() → QVariant。"
            "setLabel(label) / label() → str。"
            "setSymbol(symbol) / symbol() → QgsSymbol。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsRendererRange",
        "method_name": "分级渲染",
        "full_signature": "QgsRendererRange(lower, upper, symbol, label)",
        "description": (
            "构造函数: QgsRendererRange(lower, upper, QgsFillSymbol(), label)。"
            "setLowerValue(v) / lowerValue() → float。"
            "setUpperValue(v) / upperValue() → float。"
            "setSymbol(s) / symbol() → QgsSymbol。"
            "setLabel(l) / label() → str。"
        ),
        "source": "manual",
    },
    {
        "class_name": "QgsSymbol",
        "method_name": "符号设置",
        "full_signature": "QgsFillSymbol / QgsLineSymbol / QgsMarkerSymbol",
        "description": (
            "setColor(QColor) / color() → QColor: 设置/获取颜色。"
            "setOpacity(opacity) / opacity() → float: 设置透明度(0-1)。"
            "QgsFillSymbol.createSimple(props) → QgsFillSymbol: 从属性创建。"
            "props示例: {'color': 'red', 'style': 'solid'}。"
        ),
        "source": "manual",
    },
]


# ── 主生成函数 ──

def generate_pyqgis_docs(
    store: DocStore = None,
    include_runtime: bool = True,
    include_processing: bool = True,
    include_manual: bool = True,
    progress_callback=None,
) -> dict:
    """生成 PyQGIS API 文档索引。

    从多个来源提取 API 文档并写入 SQLite FTS5 数据库。

    Args:
        store: DocStore 实例，不传则自动创建
        include_runtime: 是否通过 inspect 提取运行时 API
        include_processing: 是否提取 Processing 算法列表
        include_manual: 是否导入手动补充的文档
        progress_callback: 进度回调 callback(phase, count, total)

    Returns:
        {"api_count": 123, "processing_count": 45, "manual_count": 10, "total": 178}
    """
    if store is None:
        store = DocStore()

    stats = {"api_count": 0, "processing_count": 0, "manual_count": 0, "total": 0}

    # ── 1. Runtime inspect ──
    if include_runtime:
        if progress_callback:
            progress_callback("inspect", 0, len(_CORE_QGIS_CLASSES))

        all_docs = []
        for i, (mod_name, cls_name) in enumerate(_CORE_QGIS_CLASSES):
            cls = _safe_import_class(mod_name, cls_name)
            if cls is None:
                continue
            docs = _inspect_class(cls, cls_name, source="runtime")
            all_docs.extend(docs)
            if progress_callback:
                progress_callback("inspect", i + 1, len(_CORE_QGIS_CLASSES))

        store.insert_batch(all_docs)
        stats["api_count"] = len(all_docs)

    # ── 2. Processing 算法 ──
    if include_processing:
        if progress_callback:
            progress_callback("processing", 0, 1)

        algo_docs = _extract_processing_algorithms()
        store.insert_batch(algo_docs)
        stats["processing_count"] = len(algo_docs)

        if progress_callback:
            progress_callback("processing", 1, 1)

    # ── 3. 手动补充 ──
    if include_manual:
        store.insert_batch(_MANUAL_API_DOCS)
        stats["manual_count"] = len(_MANUAL_API_DOCS)

    stats["total"] = stats["api_count"] + stats["processing_count"] + stats["manual_count"]
    return stats


def rebuild_index(store: DocStore = None, progress_callback=None) -> dict:
    """重建整个 API 文档索引（清空后重新生成）"""
    if store is None:
        store = DocStore()
    store.clear_all()
    return generate_pyqgis_docs(store, progress_callback=progress_callback)
