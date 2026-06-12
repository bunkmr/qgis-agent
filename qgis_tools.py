# -*- coding: utf-8 -*-
"""
QGIS 工具集 —— 融合自 qgis_mcp 的命令处理逻辑。
为 LLM 提供直接操作 QGIS 的能力，无需 Socket 通信。
Inspired by SpatialAnalysisAgent's SmartDebugger.
"""

import os
import io
import sys
import json
import traceback
from qgis.core import (
    Qgis, QgsProject, QgsApplication, QgsVectorLayer, QgsRasterLayer,
    QgsMapLayer, QgsCoordinateReferenceSystem, QgsMapSettings,
    QgsMapRendererParallelJob, QgsMessageLog,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QSize, QObject
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface

# Import SmartDebugger
from .smart_debugger import SmartDebugger, get_debug_suggestions


def _get_layer_type(layer):
    """获取图层类型字符串"""
    if layer.type() == QgsMapLayer.VectorLayer:
        gtype = layer.geometryType()
        geom_names = {0: "Point", 1: "Line", 2: "Polygon", 3: "NoGeometry", 4: "Unknown"}
        return f"vector_{geom_names.get(gtype, 'Unknown')}"
    elif layer.type() == QgsMapLayer.RasterLayer:
        return "raster"
    elif layer.type() == QgsMapLayer.MeshLayer:
        return "mesh"
    elif layer.type() == QgsMapLayer.VectorTileLayer:
        return "vector_tile"
    elif layer.type() == QgsMapLayer.PluginLayer:
        return "plugin"
    else:
        return f"type_{layer.type()}"


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
            "name": layer.name(),
            "type": _get_layer_type(layer),
            "visible": project.layerTreeRoot().findLayer(layer_id).isVisible() if project.layerTreeRoot().findLayer(layer_id) else False
        }
        if layer.type() == QgsMapLayer.VectorLayer:
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
    if layer.type() != QgsMapLayer.VectorLayer:
        return {"error": f"图层 {layer.name()} 不是矢量图层"}

    features = []
    for i, feature in enumerate(layer.getFeatures()):
        if i >= limit:
            break
        attrs = {}
        for field in layer.fields():
            val = feature.attribute(field.name())
            attrs[field.name()] = str(val) if val is not None else None

        geom = None
        if feature.hasGeometry():
            geom = {
                "type": feature.geometry().typeName() if hasattr(feature.geometry(), 'typeName') else str(feature.geometry().type()),
                "wkt": feature.geometry().asWkt(precision=4),
            }

        features.append({"id": feature.id(), "attributes": attrs, "geometry": geom})

    fields = [{"name": f.name(), "type": f.typeName()} for f in layer.fields()]

    return {
        "layer_id": layer.id(),
        "layer_name": layer.name(),
        "feature_count": layer.featureCount(),
        "fields": fields,
        "features": features,
    }


def add_vector_layer(path: str, name: str = None, provider: str = "ogr"):
    """添加矢量图层到当前项目"""
    if not name:
        name = os.path.basename(path)

    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}

    layer = QgsVectorLayer(path, name, provider)
    if not layer.isValid():
        return {"error": f"无法加载矢量图层: {path}"}

    QgsProject.instance().addMapLayer(layer)

    # 延迟刷新地图画布，避免频繁刷新导致卡顿
    from qgis.PyQt.QtCore import QTimer
    QTimer.singleShot(100, lambda: iface.mapCanvas().refresh())

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

    QgsProject.instance().addMapLayer(layer)

    # 延迟刷新地图画布，避免频繁刷新导致卡顿
    from qgis.PyQt.QtCore import QTimer
    QTimer.singleShot(100, lambda: iface.mapCanvas().refresh())

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
    project.removeMapLayer(removed_id)
    # 刷新地图画布，确保图层移除后立即反映在画布上
    iface.mapCanvas().refresh()
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

    iface.setActiveLayer(layer)
    iface.zoomToActiveLayer()
    # 强制刷新画布
    iface.mapCanvas().refresh()
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


def execute_pyqgis(code: str):
    """在 QGIS 环境中直接执行 PyQGIS 代码，并捕获输出"""
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    try:
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        # 预导入常用 QGIS 类型，确保 LLM 生成的代码能直接使用
        from qgis.core import (
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
        }
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
    if project.read(path):
        iface.mapCanvas().refresh()
        return {"loaded": path, "layer_count": len(project.mapLayers())}
    else:
        return {"error": f"加载失败: {path}"}


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
    if layer.type() != QgsMapLayer.VectorLayer:
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
        "around_point": 0,   # QgsPalLayerSettings.AroundPoint
        "over_point": 1,     # QgsPalLayerSettings.OverPoint
        "line": 2,           # QgsPalLayerSettings.Line
        "curved": 3,         # QgsPalLayerSettings.Curved
        "horizontal": 4,     # QgsPalLayerSettings.Horizontal
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
                except Exception:
                    pass
        settings.placementSettings = ps
    else:
        # 旧版 QGIS 直接设置 placement
        try:
            settings.placement = placement_val
        except TypeError:
            # 尝试用枚举值
            try:
                placement_enum = {
                    0: QgsPalLayerSettings.AroundPoint,
                    1: QgsPalLayerSettings.OverPoint,
                    2: QgsPalLayerSettings.Line,
                    3: QgsPalLayerSettings.Curved,
                    4: QgsPalLayerSettings.Horizontal,
                }.get(placement_val, QgsPalLayerSettings.AroundPoint)
                settings.placement = placement_enum
            except Exception:
                pass  # 保留默认值

    # 应用标注
    labeling = QgsVectorLayerSimpleLabeling(settings)
    try:
        layer.setLabeling(labeling)
    except TypeError:
        # 某些 QGIS 版本的 setLabeling 需要特定类型，尝试 setLabelsEnabled + 直接设置
        layer.setLabelsEnabled(True)
        # 尝试用 setLabeling 的其他重载
        try:
            from qgis.core import QgsAbstractVectorLayerLabeling
            if hasattr(layer, 'setLabeling'):
                # 直接传 QgsPalLayerSettings（某些版本接受）
                layer.setLabeling(settings)
        except Exception:
            pass
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

from qgis.PyQt.QtCore import QObject, pyqtSignal, pyqtSlot, QMutex, QWaitCondition, QThread

# 全局代码确认回调（由 qgis_agent.py 设置）
_code_confirm_callback = None

# 全局"跳过所有代码确认"开关（由 UI 开关控制）
_skip_all_confirms = False

# 需要确认才能执行的危险工具列表
_DANGEROUS_TOOLS = {"execute_pyqgis", "execute_processing"}


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
            confirm_holder["confirmed"] = True  # 无回调时默认确认
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
            except Exception:
                pass

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


def call_tool(tool_name: str, arguments: dict) -> dict:
    """调用指定工具并返回结果。

    关键：QGIS API 不是线程安全的，所有工具必须在主线程中执行。
    如果当前不在主线程，通过信号/槽 + QWaitCondition 调度到主线程同步执行。
    
    危险工具（execute_pyqgis, execute_processing）在执行前会通过
    _code_confirm_callback 弹出确认对话框。
    """
    func = TOOL_MAP.get(tool_name)
    if not func:
        return {"error": f"未知工具: {tool_name}"}

    # ── 危险工具确认 ──
    if tool_name in _DANGEROUS_TOOLS and not _skip_all_confirms and _code_confirm_callback is not None:
        code_preview = ""
        if tool_name == "execute_pyqgis":
            code_preview = arguments.get("code", "")
        elif tool_name == "execute_processing":
            code_preview = f"algorithm: {arguments.get('algorithm', '')}\n"
            code_preview += f"parameters: {json.dumps(arguments.get('parameters', {}), indent=2, ensure_ascii=False)}"
        
        # 确认回调必须在主线程中调用（会弹对话框）
        current_thread = QThread.currentThread()
        try:
            app = QApplication.instance()
            main_thread = app.thread() if app else None
        except Exception:
            main_thread = None
        
        if main_thread is not None and current_thread != main_thread:
            # 工作线程中，需要通过信号/槽调度确认
            bridge = _MainThreadBridge._instance
            if bridge is None:
                return {"error": "QGIS Agent 插件未初始化，请先打开插件面板。"}
            
            wait_cond = QWaitCondition()
            mutex = QMutex()
            confirm_holder = {"confirmed": False, "done": False, "wait_cond": wait_cond, "mutex": mutex}
            
            bridge.confirm_request.emit(tool_name, code_preview, confirm_holder)
            
            mutex.lock()
            timeout_sec = 60
            if not confirm_holder["done"]:
                wait_cond.wait(mutex, timeout_sec * 1000)
            mutex.unlock()
            
            if not confirm_holder.get("confirmed", False):
                return {"error": f"用户取消了 {tool_name} 操作。"}
        else:
            # 已在主线程，直接调用确认
            if not _code_confirm_callback(tool_name, code_preview):
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
