# -*- coding: utf-8 -*-
"""
QGIS 工具集 —— 融合自 qgis_mcp 的命令处理逻辑。
为 LLM 提供直接操作 QGIS 的能力，无需 Socket 通信。
Inspired by SpatialAnalysisAgent's SmartDebugger.
"""

import os
import io
import sys
import ast
import re
import json
import builtins
import traceback
from qgis.core import (
    Qgis, QgsProject, QgsApplication, QgsVectorLayer, QgsRasterLayer,
    QgsMapLayer, QgsCoordinateReferenceSystem, QgsMapSettings,
    QgsMapRendererParallelJob,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat
)
from qgis.PyQt.QtCore import QSize, QObject
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface

# Import SmartDebugger
from .smart_debugger import SmartDebugger


def _get_layer_type(layer):
    """获取图层类型字符串"""
    if layer.type() == QgsMapLayer.LayerType.VectorLayer:
        gtype = layer.geometryType()
        geom_names = {0: "Point", 1: "Line", 2: "Polygon", 3: "NoGeometry", 4: "Unknown"}
        return f"vector_{geom_names.get(gtype, 'Unknown')}"
    elif layer.type() == QgsMapLayer.LayerType.RasterLayer:
        return "raster"
    elif layer.type() == QgsMapLayer.LayerType.MeshLayer:
        return "mesh"
    elif layer.type() == QgsMapLayer.LayerType.VectorTileLayer:
        return "vector_tile"
    elif layer.type() == QgsMapLayer.LayerType.PluginLayer:
        return "plugin"
    else:
        return f"type_{layer.type()}"


# ──────────────────────────────────────────────
# 不可信数据净化（防提示词注入）
# ──────────────────────────────────────────────

# 控制字符（含 \x00 截断符与各类不可见分隔符），可被用于伪造/隐藏注入指令
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# get_layer_features 回喂 LLM 的结果总量上限（字符）
_MAX_FEATURE_RESULT_CHARS = 4000


def _sanitize_untrusted(value, max_chars=200):
    """净化来自外部数据源（shp/gpkg/工程文件）的文本后再回喂 LLM。

    图层名、字段名、属性值都可能被恶意构造用于提示词注入。这里只做
    「控制字符剥离 + 长度截断」，不改写语义内容；污点围栏由 processor.py
    统一包裹，此处不重复添加。
    """
    try:
        text = str(value)
    except Exception as e:
        logger.debug("不可信数据转字符串失败: %s", e, exc_info=True)
        return ""
    text = _CONTROL_CHARS_RE.sub("", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "...(已截断)"
    return text


def _dump_len(obj) -> int:
    """估算对象序列化后的字符数，失败时返回 0（不抛异常）"""
    try:
        return len(json.dumps(obj, ensure_ascii=False))
    except Exception as e:
        logger.debug("结果序列化失败，跳过长度估算: %s", e, exc_info=True)
        return 0


def _truncate_result(result: dict, max_chars: int = _MAX_FEATURE_RESULT_CHARS) -> dict:
    """对回喂 LLM 的结果做总量上限截断，超出部分提示已截断字符数"""
    total = _dump_len(result)
    if total <= max_chars:
        return result

    features = result.get("features")
    if isinstance(features, list):
        # 先丢弃末尾要素，尽量保留完整的头部数据
        while features and _dump_len(result) > max_chars:
            features.pop()
    if _dump_len(result) > max_chars:
        # 单条要素（如超长 WKT）仍超出上限：只保留元信息
        result["features"] = []
    result["truncated"] = f"...(已截断 {max(0, total - max_chars)} 字符)"
    return result


# ──────────────────────────────────────────────
# 工具函数（供 LLM function calling 使用）
# ──────────────────────────────────────────────

def get_qgis_info():
    """获取 QGIS 基本信息：版本、项目路径、图层列表等"""
    project = QgsProject.instance()
    layers_info = []
    for layer_id, layer in project.mapLayers().items():
        info = {
            "id": layer_id,
            # 图层名来自数据源/工程文件，属不可信输入，净化后再回喂 LLM
            "name": _sanitize_untrusted(layer.name(), 120),
            "type": _get_layer_type(layer),
            "visible": project.layerTreeRoot().findLayer(layer_id).isVisible() if project.layerTreeRoot().findLayer(layer_id) else False
        }
        if layer.type() == QgsMapLayer.LayerType.VectorLayer:
            info["feature_count"] = layer.featureCount()
        layers_info.append(info)

    return {
        "qgis_version": Qgis.QGIS_VERSION,
        "project_file": project.fileName() or "(未保存)",
        "crs": project.crs().authid(),
        "layer_count": len(layers_info),
        "layers": layers_info,
    }


def get_layer_features(layer_id_or_name: str, limit: int = 10):
    """获取矢量图层的要素数据（属性表 + 几何 WKT）"""
    project = QgsProject.instance()

    # 支持通过名称或 ID 查找图层
    layer = project.mapLayer(layer_id_or_name)
    if not layer:
        for lid, lyr in project.mapLayers().items():
            if lyr.name() == layer_id_or_name:
                layer = lyr
                break

    if not layer:
        return {"error": f"未找到图层: {layer_id_or_name}"}
    if layer.type() != QgsMapLayer.LayerType.VectorLayer:
        return {"error": f"图层 {layer.name()} 不是矢量图层"}

    features = []
    for i, feature in enumerate(layer.getFeatures()):
        if i >= limit:
            break
        attrs = {}
        for field in layer.fields():
            val = feature.attribute(field.name())
            # 字段名与字段值均来自数据源，属不可信输入，净化后再回喂 LLM
            attrs[_sanitize_untrusted(field.name(), 120)] = _sanitize_untrusted(val) if val is not None else None

        geom = None
        if feature.hasGeometry():
            geom = {
                "type": _sanitize_untrusted(
                    feature.geometry().typeName() if hasattr(feature.geometry(), 'typeName') else feature.geometry().type(),
                    60
                ),
                "wkt": _sanitize_untrusted(feature.geometry().asWkt(precision=4), 500),
            }

        features.append({"id": feature.id(), "attributes": attrs, "geometry": geom})

    fields = [{"name": _sanitize_untrusted(f.name(), 120), "type": _sanitize_untrusted(f.typeName(), 60)}
              for f in layer.fields()]

    result = {
        "layer_id": layer.id(),
        "layer_name": _sanitize_untrusted(layer.name(), 120),
        "feature_count": layer.featureCount(),
        "fields": fields,
        "features": features,
    }
    # 结果总量上限，避免超长属性表把注入指令整段灌进上下文
    return _truncate_result(result)


def add_vector_layer(path: str, name: str = None, provider: str = "ogr"):
    """添加矢量图层到当前项目"""
    if not name:
        name = os.path.basename(path)

    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}

    layer = QgsVectorLayer(path, name, provider)
    if not layer.isValid():
        return {"error": f"无法加载矢量图层: {path}"}

    # 优化：临时禁用地图渲染，添加图层后再启用
    canvas = iface.mapCanvas()
    canvas.setRenderFlag(False)

    try:
        QgsProject.instance().addMapLayer(layer)
    finally:
        # 重新启用地图渲染
        canvas.setRenderFlag(True)
        # 使用singleShot延迟刷新，确保渲染标志已设置
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, canvas.refresh)

    return {
        "id": layer.id(),
        "name": layer.name(),
        "type": _get_layer_type(layer),
        "feature_count": layer.featureCount(),
    }


def add_raster_layer(path: str, name: str = None, provider: str = "gdal"):
    """添加栅格图层到当前项目"""
    if not name:
        name = os.path.basename(path)

    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}

    layer = QgsRasterLayer(path, name, provider)
    if not layer.isValid():
        return {"error": f"无法加载栅格图层: {path}"}

    # 优化：临时禁用地图渲染，添加图层后再启用
    canvas = iface.mapCanvas()
    canvas.setRenderFlag(False)

    try:
        QgsProject.instance().addMapLayer(layer)
    finally:
        # 重新启用地图渲染
        canvas.setRenderFlag(True)
        # 使用singleShot延迟刷新
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, canvas.refresh)

    return {
        "id": layer.id(),
        "name": layer.name(),
        "type": "raster",
        "width": layer.width(),
        "height": layer.height(),
    }


def remove_layer(layer_id_or_name: str):
    """从项目中移除图层"""
    project = QgsProject.instance()

    layer = project.mapLayer(layer_id_or_name)
    if not layer:
        for lid, lyr in project.mapLayers().items():
            if lyr.name() == layer_id_or_name:
                layer = lyr
                break

    if not layer:
        return {"error": f"未找到图层: {layer_id_or_name}"}

    removed_name = layer.name()
    removed_id = layer.id()

    # 优化：临时禁用地图渲染，移除图层后再启用
    canvas = iface.mapCanvas()
    canvas.setRenderFlag(False)

    try:
        project.removeMapLayer(removed_id)
    finally:
        # 重新启用地图渲染
        canvas.setRenderFlag(True)
        # 使用singleShot延迟刷新
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, canvas.refresh)

    return {"removed": removed_name, "id": removed_id}


def zoom_to_layer(layer_id_or_name: str):
    """缩放到指定图层的范围"""
    project = QgsProject.instance()

    layer = project.mapLayer(layer_id_or_name)
    if not layer:
        for lid, lyr in project.mapLayers().items():
            if lyr.name() == layer_id_or_name:
                layer = lyr
                break

    if not layer:
        return {"error": f"未找到图层: {layer_id_or_name}"}

    # 优化：临时禁用地图渲染，缩放后再启用
    canvas = iface.mapCanvas()
    canvas.setRenderFlag(False)

    try:
        iface.setActiveLayer(layer)
        iface.zoomToActiveLayer()
    finally:
        # 重新启用地图渲染
        canvas.setRenderFlag(True)
        # 使用singleShot延迟刷新
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, canvas.refresh)

    return {"zoomed_to": layer.name()}


def execute_processing(algorithm: str, parameters: dict):
    """执行 QGIS Processing 算法"""
    try:
        import processing
        result = processing.run(algorithm, parameters)
        # 将结果值转为可序列化的字符串
        serialized = {}
        for k, v in result.items():
            try:
                serialized[k] = str(v)
            except Exception:
                serialized[k] = type(v).__name__
        return {"algorithm": algorithm, "result": serialized}
    except Exception as e:
        # Use SmartDebugger for intelligent error analysis
        debugger = SmartDebugger()
        code_snippet = f"processing.run('{algorithm}', {json.dumps(parameters, indent=2)})"
        error_analysis = debugger.analyze_error(str(e), code_snippet, "processing")
        suggestions = debugger.generate_debug_suggestions(str(e), code_snippet, "processing")

        # Record the failed attempt
        debugger.record_fix_attempt(str(e), "initial_execution", False)

        return {
            "error": f"Processing 执行失败: {str(e)}",
            "debug_analysis": {
                "error_category": error_analysis.get("error_category"),
                "confidence": error_analysis.get("confidence", 0.0),
                "suggestions": suggestions,
                "fallback_strategies": [s["description"] for s in error_analysis.get("fallback_strategies", [])]
            }
        }


# ──────────────────────────────────────────────
# PyQGIS 代码安全扫描（AST 静态检查）
# ──────────────────────────────────────────────

# 可直接触达文件系统/进程/网络的模块
_UNSAFE_MODULES = {"os", "subprocess", "shutil", "socket", "ctypes", "importlib", "pty", "commands", "urllib"}
# 允许导入的常用安全模块
_SAFE_MODULES = {
    "math", "json", "datetime", "re", "collections", "itertools", "functools",
    "operator", "statistics", "string", "time", "random", "copy", "decimal",
}
# 允许导入的模块前缀（QGIS 生态）
_SAFE_MODULE_PREFIXES = ("qgis", "osgeo", "processing", "PyQt5", "PyQt6", "sip")
# 危险调用：属性形式（如 os.system / shutil.rmtree / os.remove）
_UNSAFE_ATTR_CALLS = {"eval", "exec", "compile", "__import__", "input", "system", "popen", "rmtree", "remove"}
# 危险调用：裸名形式（如 eval(...) / exec(...)）
_UNSAFE_NAME_CALLS = {"eval", "exec", "compile", "__import__", "input", "system", "popen", "rmtree"}
# 从 exec namespace 中移除的内建能力
_REMOVED_BUILTINS = ("eval", "exec", "compile", "__import__")


def _is_module_allowed(root: str) -> bool:
    """判断导入的根模块名是否在允许范围内"""
    if not root:
        return True
    if root in _UNSAFE_MODULES:
        return False
    if root in _SAFE_MODULES:
        return True
    return any(root.startswith(prefix) for prefix in _SAFE_MODULE_PREFIXES)


def _called_name(node) -> str:
    """取调用目标名称：Name 取 id，Attribute 取 attr，其余返回空串"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _scan_code_safety(code: str):
    """对即将执行的代码做 AST 静态扫描。

    返回 None 表示未发现风险（放行）；返回字符串表示中文拒绝理由。
    解析失败时同样返回 None（交由 exec 阶段报错），不在此处抛异常。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.debug("代码语法解析失败，跳过安全扫描: %s", e, exc_info=True)
        return None
    except Exception as e:
        logger.debug("代码安全扫描异常，放行交由 exec 处理: %s", e, exc_info=True)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if not _is_module_allowed(root):
                    return f"禁止导入模块 '{alias.name}'，该模块可触达系统/进程/网络"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if not _is_module_allowed(root):
                return f"禁止导入模块 '{node.module}'，该模块可触达系统/进程/网络"
        elif isinstance(node, ast.Call):
            name = _called_name(node.func)
            if isinstance(node.func, ast.Attribute) and name in _UNSAFE_ATTR_CALLS:
                return f"禁止调用 '{name}'，该调用可执行任意代码或破坏文件系统"
            if isinstance(node.func, ast.Name) and name in _UNSAFE_NAME_CALLS:
                return f"禁止调用 '{name}'，该调用可执行任意代码或破坏文件系统"
        elif isinstance(node, ast.Attribute):
            # __class__ / __globals__ / __subclasses__ 等可绕过运行限制
            if node.attr.startswith("__"):
                return f"禁止访问双下划线属性 '{node.attr}'，该用法可绕过运行限制"
    return None


def execute_pyqgis(code: str):
    """在 QGIS 环境中直接执行 PyQGIS 代码，并捕获输出"""
    # 执行前先做 AST 静态扫描：命中黑名单直接拒绝，不再进入确认流程
    reject_reason = _scan_code_safety(code)
    if reject_reason:
        return {
            "error": f"代码安全检查未通过：{reject_reason}",
            "executed": False,
            "hint": "受限运行环境已移除 eval/exec/compile/__import__ 等内建，文件读写能力同样受限，请改用 QGIS API 完成该操作。",
        }

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    try:
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        # 预导入常用 QGIS 类型，确保 LLM 生成的代码能直接使用
        from qgis.core import (  # noqa: F401
            QgsPoint, QgsPointXY, QgsGeometry, QgsFeature, QgsField,
            QgsFields, QgsWkbTypes, QgsCoordinateTransform,
            QgsProcessingFeedback, QgsFeatureSink, QgsFeatureRequest,
            QgsDistanceArea, QgsUnitTypes,
            QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
            QgsSingleSymbolRenderer, QgsCategorizedSymbolRenderer,
            QgsGraduatedSymbolRenderer, QgsSymbol, QgsRendererCategory,
            QgsRendererRange
        )
        from qgis.PyQt.QtGui import QColor
        namespace = {
            "iface": iface,
            "QgsProject": QgsProject,
            "QgsApplication": QgsApplication,
            "QgsVectorLayer": QgsVectorLayer,
            "QgsRasterLayer": QgsRasterLayer,
            "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
            "Qgis": Qgis,
            "QgsPoint": QgsPoint,
            "QgsPointXY": QgsPointXY,
            "QgsGeometry": QgsGeometry,
            "QgsFeature": QgsFeature,
            "QgsField": QgsField,
            "QgsFields": QgsFields,
            "QgsWkbTypes": QgsWkbTypes,
            "QgsCoordinateTransform": QgsCoordinateTransform,
            "QgsFeatureRequest": QgsFeatureRequest,
            "QgsDistanceArea": QgsDistanceArea,
            "QgsUnitTypes": QgsUnitTypes,
            # 渲染/符号相关
            "QgsFillSymbol": QgsFillSymbol,
            "QgsLineSymbol": QgsLineSymbol,
            "QgsMarkerSymbol": QgsMarkerSymbol,
            "QgsSingleSymbolRenderer": QgsSingleSymbolRenderer,
            "QgsCategorizedSymbolRenderer": QgsCategorizedSymbolRenderer,
            "QgsGraduatedSymbolRenderer": QgsGraduatedSymbolRenderer,
            "QgsSymbol": QgsSymbol,
            "QgsRendererCategory": QgsRendererCategory,
            "QgsRendererRange": QgsRendererRange,
            "QColor": QColor,
            # 标注相关类型
            "QgsPalLayerSettings": QgsPalLayerSettings,
            "QgsVectorLayerSimpleLabeling": QgsVectorLayerSimpleLabeling,
            "QgsTextFormat": QgsTextFormat,
            # 受限内建：移除可执行任意代码/动态导入的入口
            "__builtins__": {
                k: v for k, v in builtins.__dict__.items()
                if k not in _REMOVED_BUILTINS
            },
        }
        # nosec B102 - 非沙箱：代码在用户逐次确认后以 QGIS 进程权限运行，已做 AST 危险调用扫描
        exec(code, namespace)

        sys.stdout = original_stdout
        sys.stderr = original_stderr

        return {
            "executed": True,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }
    except Exception as e:
        sys.stdout = original_stdout
        sys.stderr = original_stderr

        # Use SmartDebugger for intelligent error analysis
        debugger = SmartDebugger()
        error_analysis = debugger.analyze_error(str(e), code, "pyqgis")
        suggestions = debugger.generate_debug_suggestions(str(e), code, "pyqgis")

        # Record the failed attempt
        debugger.record_fix_attempt(str(e), "initial_execution", False)

        return {
            "executed": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
            "debug_analysis": {
                "error_category": error_analysis.get("error_category"),
                "confidence": error_analysis.get("confidence", 0.0),
                "suggestions": suggestions,
                "fallback_strategies": [s["description"] for s in error_analysis.get("fallback_strategies", [])]
            }
        }


def save_project(path: str = None):
    """保存当前 QGIS 项目"""
    project = QgsProject.instance()
    if not path and not project.fileName():
        return {"error": "请指定保存路径"}

    save_path = path if path else project.fileName()
    if project.write(save_path):
        return {"saved": save_path}
    else:
        return {"error": f"保存失败: {save_path}"}


def load_project(path: str):
    """加载 QGIS 项目"""
    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}

    project = QgsProject.instance()

    # 优化：临时禁用地图渲染，加载项目后再启用
    canvas = iface.mapCanvas()
    canvas.setRenderFlag(False)

    try:
        if project.read(path):
            return {"loaded": path, "layer_count": len(project.mapLayers())}
        else:
            return {"error": f"加载失败: {path}"}
    finally:
        # 重新启用地图渲染
        canvas.setRenderFlag(True)
        # 使用singleShot延迟刷新
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, canvas.refresh)


def set_layer_labeling(
    layer_id_or_name: str,
    field_name: str,
    enabled: bool = True,
    font_size: float = 10.0,
    color: str = "#000000",
    buffer_enabled: bool = True,
    buffer_color: str = "#FFFFFF",
    buffer_size: float = 1.0,
    placement: str = "around_point",
):
    """设置矢量图层的标注。

    Args:
        layer_id_or_name: 图层名称或ID
        field_name: 用于标注的字段名
        enabled: 是否启用标注
        font_size: 字体大小（磅）
        color: 文字颜色（如 #000000）
        buffer_enabled: 是否启用文字缓冲（描边）
        buffer_color: 缓冲颜色（如 #FFFFFF）
        buffer_size: 缓冲大小
        placement: 标注放置方式: around_point, over_point, line, horizontal
    """
    from qgis.PyQt.QtGui import QColor

    project = QgsProject.instance()
    layer = project.mapLayer(layer_id_or_name)
    if not layer:
        for lid, lyr in project.mapLayers().items():
            if lyr.name() == layer_id_or_name:
                layer = lyr
                break

    if not layer:
        return {"error": f"未找到图层: {layer_id_or_name}"}
    if layer.type() != QgsMapLayer.LayerType.VectorLayer:
        return {"error": f"图层 {layer.name()} 不是矢量图层，无法设置标注"}

    # 检查字段是否存在
    field_names = [f.name() for f in layer.fields()]
    if field_name not in field_names:
        return {"error": f"字段 '{field_name}' 不存在。可用字段: {field_names}"}

    if not enabled:
        layer.setLabelsEnabled(False)
        layer.triggerRepaint()
        return {
            "layer": layer.name(),
            "labeling_enabled": False,
            "message": f"已关闭图层 '{layer.name()}' 的标注",
        }

    # 构建标注设置
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.enabled = True

    # 文字格式
    text_format = QgsTextFormat()
    text_format.setSize(font_size)
    text_format.setColor(QColor(color))

    if buffer_enabled:
        text_format.setBufferEnabled(True)
        text_format.setBufferColor(QColor(buffer_color))
        text_format.setBufferSize(buffer_size)

    settings.setFormat(text_format)

    # 放置方式 — 兼容 QGIS 3.x 各版本
    placement_map = {
        "around_point": 0,   # QgsPalLayerSettings.Placement.AroundPoint
        "over_point": 1,     # QgsPalLayerSettings.PredefinedPointPosition.OverPoint
        "line": 2,           # QgsPalLayerSettings.Position.Line
        "curved": 3,         # QgsPalLayerSettings.Position.Curved
        "horizontal": 4,     # QgsPalLayerSettings.Position.Horizontal
    }
    placement_val = placement_map.get(placement, 0)

    # 新版 QGIS (3.30+) 使用 placementSettings，旧版使用 placement 属性
    if hasattr(settings, 'placementSettings'):
        # 新版 QGIS 使用 QgsLabelPlacementSettings
        from qgis.core import QgsLabelPlacementSettings
        ps = QgsLabelPlacementSettings()
        # 尝试设置 placement 类型
        for attr in ['placement', 'predefinedPositionOrder', 'placementFlags']:
            if hasattr(ps, attr):
                try:
                    setattr(ps, attr, placement_val)
                except Exception as _e:
                    logger.debug("ignored exception", exc_info=True)
        settings.placementSettings = ps
    else:
        # 旧版 QGIS 直接设置 placement
        try:
            settings.placement = placement_val
        except TypeError:
            # 尝试用枚举值
            try:
                placement_enum = {
                    0: QgsPalLayerSettings.Placement.AroundPoint,
                    1: QgsPalLayerSettings.PredefinedPointPosition.OverPoint,
                    2: QgsPalLayerSettings.Position.Line,
                    3: QgsPalLayerSettings.Position.Curved,
                    4: QgsPalLayerSettings.Position.Horizontal,
                }.get(placement_val, QgsPalLayerSettings.Placement.AroundPoint)
                settings.placement = placement_enum
            except Exception as _e:
                logger.debug("ignored exception", exc_info=True)

    # 应用标注
    labeling = QgsVectorLayerSimpleLabeling(settings)
    try:
        layer.setLabeling(labeling)
    except TypeError:
        # 某些 QGIS 版本的 setLabeling 需要特定类型，尝试 setLabelsEnabled + 直接设置
        layer.setLabelsEnabled(True)
        # 尝试用 setLabeling 的其他重载
        try:
            if hasattr(layer, 'setLabeling'):
                # 直接传 QgsPalLayerSettings（某些版本接受）
                layer.setLabeling(settings)
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()

    return {
        "layer": layer.name(),
        "labeling_enabled": True,
        "field": field_name,
        "font_size": font_size,
        "color": color,
        "placement": placement,
        "message": f"已为图层 '{layer.name()}' 设置标注，字段: {field_name}",
    }


def render_map(output_path: str, width: int = 800, height: int = 600):
    """将当前地图视图渲染为图片"""
    # 输出目标已存在时会被覆盖，先取得用户确认
    try:
        if output_path and os.path.exists(output_path) and not _skip_all_confirms:
            preview = json.dumps(
                {"output_path": _sanitize_untrusted(output_path, 300), "width": width, "height": height},
                ensure_ascii=False, indent=2,
            )
            if not _request_confirmation("render_map", f"将覆盖已存在的文件：\n{preview}"):
                return {"error": "用户取消了 render_map 操作。"}
    except Exception as e:
        logger.debug("render_map 覆盖确认检查失败: %s", e, exc_info=True)
        return {"error": "确认通道未就绪，已拒绝覆盖已存在的文件。"}

    try:
        ms = QgsMapSettings()
        layers = list(QgsProject.instance().mapLayers().values())
        ms.setLayers(layers)
        ms.setExtent(iface.mapCanvas().extent())
        ms.setOutputSize(QSize(width, height))
        ms.setBackgroundColor(QColor(255, 255, 255))
        ms.setOutputDpi(96)

        render = QgsMapRendererParallelJob(ms)
        render.start()
        render.waitForFinished()

        img = render.renderedImage()
        if img.save(output_path):
            return {"rendered": True, "path": output_path, "width": width, "height": height}
        else:
            return {"error": f"保存图片失败: {output_path}"}
    except Exception as e:
        return {"error": f"渲染失败: {str(e)}"}


# ──────────────────────────────────────────────
# 主线程调度器（解决 QGIS API 线程安全问题）
# ──────────────────────────────────────────────

from qgis.PyQt.QtCore import pyqtSignal, pyqtSlot, QMutex, QWaitCondition, QThread  # noqa: E402
import logging
logger = logging.getLogger(__name__)

# 全局代码确认回调（由 qgis_agent.py 设置）
_code_confirm_callback = None

# 全局"跳过所有代码确认"开关（由 UI 开关控制）
_skip_all_confirms = False

# 需要确认才能执行的危险工具列表
_DANGEROUS_TOOLS = {
    "execute_pyqgis",
    "execute_processing",
    "remove_layer",
    "load_project",
    "save_project",
}


def set_code_confirm_callback(callback):
    """设置代码执行确认回调。
    callback(tool_name, code_preview) -> bool (True=确认, False=取消)
    """
    global _code_confirm_callback
    _code_confirm_callback = callback


def set_skip_all_confirms(skip: bool):
    """设置是否跳过所有代码执行确认。
    True=直接执行不弹窗, False=每次弹窗确认（默认）
    """
    global _skip_all_confirms
    _skip_all_confirms = skip


def get_skip_all_confirms() -> bool:
    """获取当前跳过确认开关状态"""
    return _skip_all_confirms


class _MainThreadBridge(QObject):
    """驻留在主线程的桥接器。

    工作线程通过发射 execute_request 信号来触发主线程执行工具，
    主线程执行完毕后通过 QWaitCondition 唤醒等待的工作线程。

    相比 QMetaObject.invokeMethod，信号/槽方式对参数类型没有限制，
    可以安全传递 Python dict/function 等任意对象。
    """
    execute_request = pyqtSignal(object, str, object, object)  # (func, tool_name, args, result_holder)
    confirm_request = pyqtSignal(str, str, object)  # (tool_name, code_preview, confirm_holder)

    _instance = None
    _mutex = QMutex()

    @classmethod
    def get(cls):
        """获取单例。必须在主线程中首次调用。"""
        if cls._instance is None:
            cls._mutex.lock()
            try:
                if cls._instance is None:
                    cls._instance = _MainThreadBridge()
            finally:
                cls._mutex.unlock()
        return cls._instance

    @pyqtSlot(object, str, object, object)
    def _on_execute(self, func, tool_name, arguments, result_holder):
        """在主线程中执行工具（由信号触发）"""
        try:
            result_holder["result"] = _execute_tool(func, tool_name, arguments)
        except Exception as e:
            result_holder["error"] = {"error": str(e), "traceback": traceback.format_exc()}
        result_holder["done"] = True

        # 唤醒等待的工作线程（先锁 mutex 保证内存可见性）
        if "wait_cond" in result_holder and "mutex" in result_holder:
            result_holder["mutex"].lock()
            result_holder["wait_cond"].wakeAll()
            result_holder["mutex"].unlock()

    @pyqtSlot(str, str, object)
    def _on_confirm(self, tool_name, code_preview, confirm_holder):
        """在主线程中弹出确认对话框"""
        if _code_confirm_callback:
            confirmed = _code_confirm_callback(tool_name, code_preview)
            confirm_holder["confirmed"] = confirmed
        else:
            # 确认通道未就绪时拒绝执行，避免危险工具静默运行
            confirm_holder["confirmed"] = False
        confirm_holder["done"] = True

        # 唤醒等待的工作线程
        if "wait_cond" in confirm_holder and "mutex" in confirm_holder:
            confirm_holder["mutex"].lock()
            confirm_holder["wait_cond"].wakeAll()
            confirm_holder["mutex"].unlock()


def _init_main_thread_bridge():
    """在主线程中初始化桥接器。由插件入口 qgis_agent.py 调用。"""
    bridge = _MainThreadBridge.get()
    # 连接信号到槽（自动跨线程安全）
    bridge.execute_request.connect(bridge._on_execute)
    bridge.confirm_request.connect(bridge._on_confirm)
    return bridge


# ──────────────────────────────────────────────
# RAG API 文档检索工具
# ──────────────────────────────────────────────

def search_pyqgis_api(query: str):
    """检索 PyQGIS API 文档，返回精确的方法签名和用法。

    在编写 execute_pyqgis 代码之前使用此工具查询 API，
    可以避免参数名/类型错误。
    """
    try:
        from .rag import get_retriever
        retriever = get_retriever()
        results = retriever.search(query, top_k=5)
        if not results:
            return {"query": query, "results": [], "hint": "未找到匹配的 API 文档。请尝试更具体的关键词，如 'buffer geometry' 或 'QgsVectorLayer fields'。"}

        formatted = retriever.format_as_context(results)
        return {
            "query": query,
            "count": len(results),
            "results": [
                {
                    "signature": r.get("full_signature", ""),
                    "description": r.get("description", "")[:200],
                    "class": r.get("class_name", ""),
                }
                for r in results
            ],
            "formatted": formatted,
        }
    except Exception as e:
        return {"error": f"API 文档检索失败: {str(e)}", "hint": "请确认已初始化 RAG 索引（首次使用需在 QGIS 中运行 rag_init）"}


# ──────────────────────────────────────────────
# 长期记忆工具
# ──────────────────────────────────────────────

# MEMORY.md 文件路径（与插件目录同级的 qgis_agent 数据目录）
_memory_dir = None


def _get_memory_path():
    """获取 MEMORY.md 的绝对路径"""
    global _memory_dir
    if _memory_dir is None:
        # 存放在 QGIS profile 下的 qgis_agent 插件目录
        from qgis.core import QgsApplication
        profile_path = QgsApplication.qgisSettingsDirPath()
        _memory_dir = os.path.join(profile_path, "python", "plugins", "qgis_agent")
    return os.path.join(_memory_dir, "MEMORY.md")


def save_memory(content: str, category: str = "") -> dict:
    """将内容追加保存到长期记忆文件"""
    import datetime
    try:
        memory_path = _get_memory_path()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        category_tag = f" | {category}" if category else ""

        # 读取现有内容，避免重复写入相同内容
        existing = ""
        if os.path.exists(memory_path):
            try:
                with open(memory_path, "r", encoding="utf-8") as f:
                    existing = f.read()
            except Exception as _e:
                logger.debug("ignored exception", exc_info=True)

        # 简单去重：如果内容已存在，跳过
        if content.strip() in existing:
            return {"status": "skipped", "message": "该内容已存在于记忆中，跳过保存。"}

        entry = f"\n## {timestamp}{category_tag}\n\n{content.strip()}\n"

        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(entry)

        return {"status": "saved", "path": memory_path, "message": "记忆已保存。"}
    except Exception as e:
        return {"error": f"保存记忆失败: {str(e)}"}


def load_memory() -> dict:
    """读取长期记忆文件内容"""
    try:
        memory_path = _get_memory_path()
        if not os.path.exists(memory_path):
            return {"status": "empty", "content": "", "message": "暂无长期记忆。"}
        with open(memory_path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return {"status": "empty", "content": "", "message": "长期记忆文件为空。"}
        # 截断过长内容
        if len(content) > 8000:
            content = content[:8000] + "\n\n...(记忆内容过长，已截断)"
        return {"status": "ok", "content": content, "length": len(content)}
    except Exception as e:
        return {"error": f"读取记忆失败: {str(e)}"}


# ──────────────────────────────────────────────
# 工具注册表（用于 LLM function calling）
# ──────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "save_memory",
        "description": "将重要信息保存到长期记忆中（追加到 MEMORY.md 文件）。用于记住用户偏好、常用路径、项目配置、重要结论等跨对话信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记忆的内容（Markdown 格式）"},
                "category": {"type": "string", "description": "记忆分类标签，如 '用户偏好'、'项目配置'、'数据路径'、'重要结论'"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "load_memory",
        "description": "读取长期记忆文件（MEMORY.md）的全部内容，查看之前保存的所有重要信息。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_qgis_info",
        "description": "获取 QGIS 当前状态信息：版本、项目路径、坐标系、所有图层列表（含名称、类型、要素数量、可见性）",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_layer_features",
        "description": "获取指定矢量图层的属性表和几何数据。支持按图层名称或ID查找。返回前N条要素的字段值和WKT几何。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id_or_name": {"type": "string", "description": "图层名称或ID"},
                "limit": {"type": "integer", "description": "返回要素数量上限，默认10"},
            },
            "required": ["layer_id_or_name"],
        },
    },
    {
        "name": "add_vector_layer",
        "description": "添加矢量图层（Shapefile、GeoJSON、GPKG等）到当前QGIS项目",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "图层文件的绝对路径"},
                "name": {"type": "string", "description": "图层显示名称，不指定则使用文件名"},
                "provider": {"type": "string", "description": "数据源类型，默认 ogr"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "add_raster_layer",
        "description": "添加栅格图层（GeoTIFF、IMG等）到当前QGIS项目",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "栅格文件的绝对路径"},
                "name": {"type": "string", "description": "图层显示名称"},
                "provider": {"type": "string", "description": "数据源类型，默认 gdal"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "remove_layer",
        "description": "从项目中移除指定图层",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id_or_name": {"type": "string", "description": "图层名称或ID"},
            },
            "required": ["layer_id_or_name"],
        },
    },
    {
        "name": "zoom_to_layer",
        "description": "将地图视图缩放到指定图层的范围",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id_or_name": {"type": "string", "description": "图层名称或ID"},
            },
            "required": ["layer_id_or_name"],
        },
    },
    {
        "name": "execute_processing",
        "description": "执行 QGIS Processing Toolbox 中的处理算法。常用算法示例：native:buffer(缓冲区)、native:clip(裁剪)、native:intersection(相交)、qgis:exporttospreadsheet(导出表格)、gdal:contour(等高线)、native:fieldcalculator(字段计算器)",
        "parameters": {
            "type": "object",
            "properties": {
                "algorithm": {"type": "string", "description": "算法ID，如 native:buffer"},
                "parameters": {"type": "object", "description": "算法参数字典，如 {'INPUT': 'layer_id', 'DISTANCE': 100, 'OUTPUT': 'memory:'}"},
            },
            "required": ["algorithm", "parameters"],
        },
    },
    {
        "name": "search_pyqgis_api",
        "description": "检索 PyQGIS/GDAL/Processing API 文档，获取准确的方法签名和参数信息。在编写 execute_pyqgis 代码之前应优先使用此工具查询相关 API，避免参数名/类型错误。支持中英文关键词搜索。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，如 'buffer geometry', 'QgsVectorLayer addFeature', 'processing run dissolve', '字段计算'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "execute_pyqgis",
        "description": "在 QGIS Python 环境中直接执行 PyQGIS 代码。可用于复杂操作或处理算法无法完成的定制任务。会捕获 print() 输出和错误信息。执行前建议先使用 search_pyqgis_api 查询 API 文档。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 PyQGIS Python 代码"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "save_project",
        "description": "保存当前 QGIS 项目文件",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "保存路径，不指定则保存到当前路径"},
            },
            "required": [],
        },
    },
    {
        "name": "load_project",
        "description": "加载 QGIS 项目文件",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "项目文件(.qgz/.qgs)的绝对路径"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "set_layer_labeling",
        "description": "设置矢量图层的标注（Labeling）。可以为点、线、面图层启用标注，指定标注字段、字体大小、颜色、缓冲等。注意：设置标注请使用此工具，不要通过 execute_pyqgis 代码方式设置，以避免 API 兼容性问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id_or_name": {"type": "string", "description": "图层名称或ID"},
                "field_name": {"type": "string", "description": "用于标注的字段名"},
                "enabled": {"type": "boolean", "description": "是否启用标注，默认 true"},
                "font_size": {"type": "number", "description": "字体大小（磅），默认 10"},
                "color": {"type": "string", "description": "文字颜色，如 #000000，默认黑色"},
                "buffer_enabled": {"type": "boolean", "description": "是否启用文字缓冲（描边），默认 true"},
                "buffer_color": {"type": "string", "description": "缓冲颜色，如 #FFFFFF，默认白色"},
                "buffer_size": {"type": "number", "description": "缓冲大小，默认 1.0"},
                "placement": {"type": "string", "description": "标注放置方式: around_point(点周围), over_point(点上方), line(沿线), horizontal(水平)"},
            },
            "required": ["layer_id_or_name", "field_name"],
        },
    },
    {
        "name": "render_map",
        "description": "将当前地图画布渲染为PNG图片文件",
        "parameters": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "输出图片的绝对路径(.png)"},
                "width": {"type": "integer", "description": "图片宽度(像素)，默认800"},
                "height": {"type": "integer", "description": "图片高度(像素)，默认600"},
            },
            "required": ["output_path"],
        },
    },
]

# 工具名 → 函数映射
TOOL_MAP = {
    "save_memory": save_memory,
    "load_memory": load_memory,
    "search_pyqgis_api": search_pyqgis_api,
    "get_qgis_info": get_qgis_info,
    "get_layer_features": get_layer_features,
    "add_vector_layer": add_vector_layer,
    "add_raster_layer": add_raster_layer,
    "remove_layer": remove_layer,
    "zoom_to_layer": zoom_to_layer,
    "execute_processing": execute_processing,
    "execute_pyqgis": execute_pyqgis,
    "set_layer_labeling": set_layer_labeling,
    "save_project": save_project,
    "load_project": load_project,
    "render_map": render_map,
}


def _build_confirm_preview(tool_name: str, arguments: dict) -> str:
    """构造确认弹窗展示的内容（危险提示 + 参数/代码预览）"""
    if tool_name == "execute_pyqgis":
        # 明确告知执行权限，避免用户误以为代码运行在隔离沙箱中
        return "此代码将在本机以你的 QGIS 进程权限运行。\n" + str(arguments.get("code", ""))
    if tool_name == "execute_processing":
        body = f"algorithm: {arguments.get('algorithm', '')}\n"
        body += f"parameters: {json.dumps(arguments.get('parameters', {}), indent=2, ensure_ascii=False)}"
        return "此算法将在本机以你的 QGIS 进程权限运行。\n" + body
    if tool_name == "remove_layer":
        return "将从当前工程中移除图层：\n" + _sanitize_untrusted(arguments.get("layer_id_or_name", ""), 200)
    if tool_name == "load_project":
        return "加载工程会丢弃当前未保存的修改：\n" + _sanitize_untrusted(arguments.get("path", ""), 300)
    if tool_name == "save_project":
        target = arguments.get("path") or "(当前工程路径)"
        return "保存工程，可能覆盖已存在文件：\n" + _sanitize_untrusted(target, 300)
    return json.dumps(arguments, ensure_ascii=False, indent=2)


def _request_confirmation(tool_name: str, code_preview: str) -> bool:
    """在主线程弹出确认对话框，返回用户是否确认。

    确认通道未就绪（无回调/桥接器未初始化）时一律返回 False，
    避免危险工具在无人确认的情况下静默执行。
    """
    if _code_confirm_callback is None:
        logger.debug("确认回调未注册，拒绝执行危险工具: %s", tool_name)
        return False

    # 确认回调必须在主线程中调用（会弹对话框）
    current_thread = QThread.currentThread()
    try:
        app = QApplication.instance()
        main_thread = app.thread() if app else None
    except Exception as e:
        logger.debug("获取主线程失败，按主线程处理: %s", e, exc_info=True)
        main_thread = None

    if main_thread is not None and current_thread != main_thread:
        # 工作线程中，需要通过信号/槽调度确认
        bridge = _MainThreadBridge._instance
        if bridge is None:
            return False

        wait_cond = QWaitCondition()
        mutex = QMutex()
        confirm_holder = {"confirmed": False, "done": False, "wait_cond": wait_cond, "mutex": mutex}

        bridge.confirm_request.emit(tool_name, code_preview, confirm_holder)

        mutex.lock()
        timeout_sec = 60
        if not confirm_holder["done"]:
            wait_cond.wait(mutex, timeout_sec * 1000)
        mutex.unlock()

        return bool(confirm_holder.get("confirmed", False))

    # 已在主线程，直接调用确认
    return bool(_code_confirm_callback(tool_name, code_preview))


def call_tool(tool_name: str, arguments: dict) -> dict:
    """调用指定工具并返回结果。

    关键：QGIS API 不是线程安全的，所有工具必须在主线程中执行。
    如果当前不在主线程，通过信号/槽 + QWaitCondition 调度到主线程同步执行。

    危险工具（execute_pyqgis / execute_processing / remove_layer /
    load_project / save_project）在执行前会通过 _code_confirm_callback
    弹出确认对话框；render_map 在覆盖已存在文件时同样会确认。
    确认通道不可用时拒绝执行，不放行。
    """
    func = TOOL_MAP.get(tool_name)
    if not func:
        return {"error": f"未知工具: {tool_name}"}

    # ── 危险工具确认（确认通道不可用即拒绝，不放行）──
    if tool_name in _DANGEROUS_TOOLS and not _skip_all_confirms:
        if _code_confirm_callback is None:
            return {"error": f"确认通道未就绪，已拒绝执行 {tool_name}。"}
        if not _request_confirmation(tool_name, _build_confirm_preview(tool_name, arguments)):
            return {"error": f"用户取消了 {tool_name} 操作。"}

    # 检查当前是否在主线程
    current_thread = QThread.currentThread()
    try:
        app = QApplication.instance()
        main_thread = app.thread() if app else None
    except Exception:
        main_thread = None

    if main_thread is None or current_thread == main_thread:
        # 已在主线程，直接执行
        return _execute_tool(func, tool_name, arguments)

    # ── 在工作线程中，通过信号/槽调度到主线程同步执行 ──
    bridge = _MainThreadBridge._instance  # 使用 _instance 而非 get() 避免在工作线程创建
    if bridge is None:
        return {"error": "QGIS Agent 插件未初始化，请先打开插件面板。"}

    wait_cond = QWaitCondition()
    mutex = QMutex()
    result_holder = {
        "result": None,
        "done": False,
        "error": None,
        "wait_cond": wait_cond,
        "mutex": mutex,
    }

    # 发射信号到主线程桥接器（Qt 自动处理跨线程信号投递）
    bridge.execute_request.emit(func, tool_name, arguments, result_holder)

    # 使用 QWaitCondition 等待主线程完成（阻塞工作线程，不阻塞主线程事件循环）
    mutex.lock()
    timeout_sec = 30
    if not result_holder["done"]:
        wait_cond.wait(mutex, timeout_sec * 1000)
    mutex.unlock()

    if not result_holder["done"]:
        return {"error": f"工具 {tool_name} 执行超时（30秒）"}

    if result_holder["error"]:
        return result_holder["error"]
    return result_holder["result"]


def _execute_tool(func, tool_name, arguments):
    """实际执行工具函数"""
    try:
        result = func(**arguments)
        return result
    except Exception as e:
        return {"error": f"工具执行异常: {str(e)}", "traceback": traceback.format_exc()}
