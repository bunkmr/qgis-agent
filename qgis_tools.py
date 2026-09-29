# -*- coding: utf-8 -*-
"""
QGIS 工具集 —— 融合自 qgis_mcp 的命令处理逻辑。
为 LLM 提供直接操作 QGIS 的能力，无需 Socket 通信。
Inspired by SpatialAnalysisAgent's SmartDebugger.
"""

import contextlib
import os
import io
import sys
import ast
import re
import json
import logging
import difflib
import builtins
import tempfile
import traceback
from qgis.core import (
    Qgis, QgsProject, QgsApplication, QgsVectorLayer, QgsRasterLayer,
    QgsMapLayer, QgsCoordinateReferenceSystem, QgsMapSettings,
    QgsMapRendererParallelJob, QgsWkbTypes,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat
)
from qgis.PyQt.QtCore import QSize, QObject
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface

# Import SmartDebugger
from .smart_debugger import SmartDebugger

logger = logging.getLogger(__name__)


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
    # 支持通过名称或 ID 查找图层（名称匹配忽略大小写，见 _find_layer）
    layer = _find_layer(layer_id_or_name)
    if not layer:
        return _layer_not_found_error(layer_id_or_name)
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
    layer = _find_layer(layer_id_or_name)
    if not layer:
        return _layer_not_found_error(layer_id_or_name)

    removed_name = layer.name()
    removed_id = layer.id()

    # 优化：临时禁用地图渲染，移除图层后再启用
    canvas = iface.mapCanvas()
    canvas.setRenderFlag(False)

    try:
        QgsProject.instance().removeMapLayer(removed_id)
    finally:
        # 重新启用地图渲染
        canvas.setRenderFlag(True)
        # 使用singleShot延迟刷新
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(50, canvas.refresh)

    return {"removed": removed_name, "id": removed_id}


def zoom_to_layer(layer_id_or_name: str):
    """缩放到指定图层的范围"""
    layer = _find_layer(layer_id_or_name)
    if not layer:
        return _layer_not_found_error(layer_id_or_name)

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
        serialized = {}
        added_layers = []
        for k, v in result.items():
            # ── 图层类输出：显式加入工程并回报结构化信息 ──
            # v2.4.7 修复：OUTPUT='memory:' 的结果图层**不会自动进工程**，
            # 而旧实现只回一个 str(v)，即 "<QgsVectorLayer: 'output' (memory)>"。
            # 现场表现（用户报障）是：模型算完面积字段后去 find 图层名叫 "output"，
            # 报「未找到图层: output」，接着连续两轮空转 —— 因为那个 repr 字符串
            # 既没给名字、也没给 ID，更没说明结果在哪。工具的合理契约是
            # 「执行完能在图层列表里看到结果」，这里把它补上。
            if isinstance(v, QgsMapLayer):
                try:
                    name = v.name()
                    layer_id = v.id()
                    if QgsProject.instance().mapLayer(layer_id) is None:
                        QgsProject.instance().addMapLayer(v)
                        added_layers.append(name)
                    info = {
                        "name": name,
                        "id": layer_id,
                        "added_to_project": QgsProject.instance().mapLayer(layer_id) is not None,
                    }
                    try:
                        if hasattr(v, "featureCount"):
                            info["feature_count"] = v.featureCount()
                    except Exception as _e:
                        logger.debug("读取要素数失败（已忽略）: %s", _e)
                    serialized[k] = info
                    continue
                except Exception as _e:
                    logger.debug("图层输出处理失败，回落为字符串: %s", _e, exc_info=True)
            try:
                serialized[k] = str(v)
            except Exception:
                serialized[k] = type(v).__name__
        out = {"algorithm": algorithm, "result": serialized}
        if added_layers:
            out["hint"] = (
                "结果图层已加入当前工程：%s。可直接用该名称或 id 调用 "
                "get_layer_features / get_layer_profile 查看内容，无需重新添加。"
                % "、".join(added_layers)
            )
        return out
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
_UNSAFE_MODULES = {
    "os", "subprocess", "shutil", "socket", "ctypes", "importlib", "pty",
    "commands", "urllib", "pathlib", "io", "tempfile", "glob", "fnmatch",
    "webbrowser", "http", "ftplib", "smtplib", "telnetlib", "xmlrpc",
    "pickle", "shelve", "marshal", "code", "codeop", "pty", "resource",
    "signal", "multiprocessing", "threading", "concurrent", "asyncio",
    "ctypes", "gc", "inspect", "types", "typing_extensions",
}
# ⚠️ 宿主运行时窄口（v2.4.13）—— **只给 QGIS/PyQt 内部**放行，不是给用户代码的。
# 有些 C++ 层在**调用期**才惰性 import 个别模块，而且这个 import 命中的是
# 【当前帧的 __builtins__】—— 也就是本沙箱这份受限字典。最典型的是
# QgsProject.mapLayersByName()：PyQt/sip 在进程内**首次**调用它时做一次 `import gc`，
# 被上面的 _UNSAFE_MODULES 拒掉后，异常在 C 层被吞成
# 「<built-in method mapLayersByName ...> returned a result with an exception set」，
# 完全指不到根因；而且 cold 必失败 / warm 必成功，极难自查（Qt5/Qt6 均实测复现）。
#
# 这里**故意不动 _SAFE_MODULES**：用户代码写 `import gc` 仍会被 _scan_code_safety
# 的 Import 分支拒掉，`__builtins__['__import__']('gc')` 也被 dunder 名字检查堵死。
# 这两者缺一不可 —— gc.get_objects() 能直接枚举出 os 模块对象，**不需要任何 dunder
# 就能逃逸**，所以 gc 绝不能进用户可导入的白名单。
_HOST_RUNTIME_MODULES = frozenset({"gc"})
# 允许导入的常用安全模块
_SAFE_MODULES = {
    "math", "json", "datetime", "re", "collections", "itertools", "functools",
    "operator", "statistics", "string", "time", "random", "copy", "decimal",
    "typing", "dataclasses", "enum", "abc", "numbers", "cmath", "array",
    "bisect", "heapq", "weakref", "pprint", "textwrap", "unicodedata",
}
# 允许导入的模块前缀（QGIS 生态）
_SAFE_MODULE_PREFIXES = ("qgis", "osgeo", "processing", "PyQt5", "PyQt6", "sip")
# 危险调用：属性形式（如 os.system / shutil.rmtree / os.remove）
_UNSAFE_ATTR_CALLS = {
    "eval", "exec", "compile", "__import__", "input", "system", "popen",
    "rmtree", "remove", "unlink", "rmdir", "removedirs", "chmod", "chown",
    "chroot", "fork", "forkpty", "execv", "execve", "execl", "execle",
    "execlp", "execlpe", "spawn", "spawnl", "spawnle", "spawnlp",
    "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "startfile",
    "kill", "killpg", "abort",
}
# 危险调用：裸名形式（如 eval(...) / exec(...)）
_UNSAFE_NAME_CALLS = {
    "eval", "exec", "compile", "__import__", "input", "system", "popen",
    "rmtree", "open", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "breakpoint", "memoryview", "file", "reload",
}
# 允许出现在字符串参数中的属性名前缀（getattr/setattr 等的第二参）
# 双下划线属性一律拒绝，防沙箱逃逸
_FORBIDDEN_STRING_ATTR_PREFIX = "__"
# ⚠️ 受限命名空间的内建能力**只由下面这份白名单决定**（v2.4.7 起为唯一真源）。
# 这里原先还有一份 _REMOVED_BUILTINS「黑名单」，但它从未被任何代码引用（纯死代码），
# 且与白名单语义重复、互相矛盾 —— 尤其它把 __import__ 列为「应移除」，而白名单本身
# 也漏了 __import__，两者叠加导致 execute_pyqgis 里任何 import 都必然失败。
# 黑名单已删除，避免后来者误以为存在第二套策略。
_SAFE_BUILTINS = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "dict", "dir", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "hash", "hex", "id", "int", "isinstance",
    "issubclass", "iter", "len", "list", "map", "max", "min", "next",
    "oct", "ord", "pow", "print", "range", "repr", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip", "Exception",
    "ValueError", "TypeError", "KeyError", "IndexError", "RuntimeError",
    "StopIteration", "True", "False", "None", "NotImplemented", "Ellipsis",
    "BaseException", "ArithmeticError", "AssertionError", "AttributeError",
    "IOError", "ImportError", "LookupError", "NameError", "OSError",
    "OverflowError", "ReferenceError", "RuntimeWarning", "Warning",
    "ZeroDivisionError", "UnicodeError", "UnicodeDecodeError",
)


def _is_module_allowed(root: str) -> bool:
    """判断导入的根模块名是否在允许范围内"""
    if not root:
        return True
    if root in _UNSAFE_MODULES:
        return False
    if root in _SAFE_MODULES:
        return True
    return any(root.startswith(prefix) for prefix in _SAFE_MODULE_PREFIXES)


def _make_safe_import():
    """构造「受控 __import__」，与 _scan_code_safety 共用同一份模块白名单。

    背景（v2.4.7 修复）：受限命名空间的 ``__builtins__`` 原先是一份纯白名单
    字典，**里面没有 __import__**（它还被列在 _REMOVED_BUILTINS 里）。于是
    ``exec(code, namespace)`` 里任何 ``import`` / ``from ... import`` 都直接抛
    ``ImportError: __import__ not found`` —— 而错误信息**完全不指向根因**，
    模型看到只会以为自己写错了，于是反复重写、白烧轮次。

    更糟的是两层安全策略互相矛盾：AST 扫描层（_scan_code_safety）明确**放行**
    qgis / processing / math / json 等模块，执行层却把所有导入能力删光了。
    净效果是「execute_pyqgis 这把枪永远打不响」—— 因为 LLM 写 PyQGIS 代码的
    天然起手式就是 ``from qgis.core import ...``，那是它见过的一切文档的样子。

    现在把执行层的导入能力收回到与 AST 层**同一个判据**（_is_module_allowed）：
    白名单内可正常导入，白名单外一律 ImportError。安全边界不降级。
    """
    real_import = builtins.__import__

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level and level > 0:
            raise ImportError("禁止相对导入，请使用绝对模块路径")
        root = (name or "").split(".")[0]
        # _HOST_RUNTIME_MODULES 是给宿主（QGIS/PyQt 的 C++ 层）在**调用期**惰性
        # 导入用的窄口 —— 例如 mapLayersByName 首次调用时 PyQt 会 import gc。
        # 用户代码**写不出**这些 import：AST 层照拒（见 _scan_code_safety）。
        if not _is_module_allowed(root) and root not in _HOST_RUNTIME_MODULES:
            raise ImportError(
                f"禁止导入模块 '{name}'，该模块可触达系统/进程/网络")
        return real_import(name, globals, locals, fromlist, level)

    return _safe_import


def _called_name(node) -> str:
    """取调用目标名称：Name 取 id，Attribute 取 attr，其余返回空串"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _string_arg_starts_with_dunder(node) -> bool:
    """检查 Call 的任意字符串字面量参数是否以 __ 开头（getattr 等逃逸路径）"""
    for arg in list(node.args) + [kw.value for kw in node.keywords]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if arg.value.startswith(_FORBIDDEN_STRING_ATTR_PREFIX):
                return True
    return False


def _scan_code_safety(code: str):
    """对即将执行的代码做 AST 静态扫描。

    返回 None 表示未发现风险（放行）；返回字符串表示中文拒绝理由。
    语法解析失败时返回拒绝理由（不放行），避免绕过扫描。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"代码语法错误，无法完成安全检查: {e.msg}"
    except Exception as e:
        logger.debug("代码安全扫描异常: %s", e, exc_info=True)
        return "代码安全检查失败，已拒绝执行"

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
            # getattr/setattr/delattr 的字符串参数若以 __ 开头，等价于访问双下划线属性
            if _string_arg_starts_with_dunder(node):
                return "禁止通过字符串参数访问双下划线属性，该用法可绕过运行限制"
        elif isinstance(node, ast.Attribute):
            # __class__ / __globals__ / __subclasses__ 等可绕过运行限制
            if node.attr.startswith("__"):
                return f"禁止访问双下划线属性 '{node.attr}'，该用法可绕过运行限制"
        elif isinstance(node, ast.Name):
            # ⚠️ v2.4.13 补的洞：**名字**也要查 dunder。
            # 之前只查 Call(func=...) 与 Attribute，于是
            # ``__builtins__['__import__']('gc')`` 能整条绕过扫描 ——
            # 下标取值不是 Call(func=Attribute)，_called_name 拿到空串，
            # 所以 '__import__' 没被 _UNSAFE_NAME_CALLS 命中，
            # 而 '__builtins__' 这个名字当时没有任何一道检查在看。
            # 宿主的运行时窄口一旦开在这样的缺口旁边，等于把 gc 从「堵死」变成「可绕」。
            if node.id.startswith("__"):
                return f"禁止访问双下划线名字 '{node.id}'，该用法可绕过运行限制"
    return None


def _layout_namespace_entries() -> dict:
    """打印布局 / 出图相关的可选命名空间条目（缺类则跳过，不阻塞 execute_pyqgis）。

    v2.4.10 新增：此前 execute_pyqgis 的命名空间里**没有任何布局类**，模型要写
    「逐要素出图 / 加指北针比例尺」只能靠 import 猜，且极易写到已被移除的 API。
    这里把现行可用的布局类预置进去，并显式**不**提供 ``QgsLayoutItemNorthArrow``
    ——该类在 QGIS 3.44 / 4.x 中已不存在（实测两版均 AttributeError），
    指北针的正确做法是 ``QgsLayoutItemPicture`` + ``setNorthMode(TrueNorth)``，
    或者直接用 ``export_features_maps`` 工具。

    逐个 getattr 取值再组字典，任何一类缺失都只是少一个名字，不会让整个
    execute_pyqgis 变成不可用（可选能力不得成为主链路的失败点）。
    """
    try:
        import qgis.core as _core
    except ImportError:
        return {}
    wanted = (
        "QgsPrintLayout", "QgsLayoutItem", "QgsLayoutItemMap",
        "QgsLayoutItemPicture", "QgsLayoutItemScaleBar", "QgsLayoutItemLabel",
        "QgsLayoutPoint", "QgsLayoutSize", "QgsLayoutExporter",
        "QgsLayoutAtlas", "QgsLayoutNorthArrowHandler", "QgsRectangle",
    )
    out = {}
    for name in wanted:
        obj = getattr(_core, name, None)
        if obj is not None:
            out[name] = obj
    return out


def _sandbox_lookup_layer(name):
    """execute_pyqgis 命名空间里的 ``find_layer(name)``：找不到时抛带候选的错误。

    背景：模型在 execute_pyqgis 里习惯写
    ``QgsProject.instance().mapLayersByName("com_s")[0]`` —— 而 mapLayersByName
    **大小写敏感**，找不到时返回空列表，紧跟着的 ``[0]`` 直接 IndexError，
    报错信息里既没有真实图层名也没有「大小写」这个线索。
    给它一个容忍大小写、失败时把候选图层名列出来的入口，比在提示词里反复叮嘱可靠。
    """
    layer = _find_layer(name)
    if layer is not None:
        return layer
    info = _layer_not_found_error(name)
    raise ValueError("%s；%s" % (info.get("error", ""), info.get("hint", "")))


def _sandbox_reject_hint(reject_reason: str, code: str) -> str:
    """把「被安全扫描拒绝」翻译成「下一步该调哪个工具」。

    v2.4.12：原先无论被拒原因是什么，都只回一句「请改用 QGIS API 完成该操作」——
    而模型当时想做的正是「写文件」，QGIS API 根本给不了，于是它反复重写、
    连续 10 次撞同一堵墙，用户要的 CSV 始终没生成。
    拒绝信息必须指向**真正能做成这件事的工具**（铁律 7：跨文件约定必须对齐）。
    """
    text = ("%s\n%s" % (reject_reason, code)).lower()
    if "csv" in text or "excel" in text or "xlsx" in text:
        return ("导出表格/CSV 请改用专用工具 export_table_to_csv：把算好的表格放进 rows "
                "参数（如 rows=[{\"社区\": \"A\", \"面积km²\": 1.23}]），或传 layer 导出属性表；"
                "受限环境按安全设计禁止 import csv / open()，在这里写文件必然失败。")
    if "open" in text or "write" in text:
        return ("写文件请改用专用工具：表格 → export_table_to_csv；图片 → render_map / "
                "export_features_maps。受限环境按安全设计移除了 open()，这里永远写不出文件。")
    return ("受限运行环境已移除 eval/exec/open/getattr 等内建：文件读写与系统调用不可用。"
            "请改用 QGIS API 或既有工具（表格导出 export_table_to_csv、出图 render_map / "
            "export_features_maps、统计结果直接 print 即可）完成该操作。")


def execute_pyqgis(code: str):
    """在 QGIS 环境中直接执行 PyQGIS 代码，并捕获输出"""
    # 执行前先做 AST 静态扫描：命中黑名单直接拒绝，不再进入确认流程
    reject_reason = _scan_code_safety(code)
    if reject_reason:
        return {
            "error": f"代码安全检查未通过：{reject_reason}",
            "executed": False,
            "hint": _sandbox_reject_hint(reject_reason, code),
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
        safe_builtins = {
            name: getattr(builtins, name)
            for name in _SAFE_BUILTINS
            if hasattr(builtins, name)
        }
        # 显式保留常用字面量/异常，避免白名单遗漏导致的 NameError
        safe_builtins.setdefault("True", True)
        safe_builtins.setdefault("False", False)
        safe_builtins.setdefault("None", None)
        # 受控导入能力：与 _scan_code_safety 共用 _is_module_allowed 判据。
        # 缺了它，代码里任何 `import` 都会失败（ImportError: __import__ not found），
        # 而 LLM 写 PyQGIS 几乎必然带 import。
        safe_builtins["__import__"] = _make_safe_import()
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
            # 受限内建：白名单，排除 open/getattr/eval/exec 等危险入口
            "__builtins__": safe_builtins,
            # 容忍大小写的图层查找（见 _sandbox_lookup_layer）：
            # 模型惯用的 mapLayersByName 大小写敏感且失败时只给空列表
            "find_layer": _sandbox_lookup_layer,
        }
        # 打印布局 / 出图相关类（逐要素出图、图幅制作常用；缺类自动跳过，
        # 不提供已被移除的 QgsLayoutItemNorthArrow，见 _layout_namespace_entries）
        namespace.update(_layout_namespace_entries())
        # execute_pyqgis 按设计要执行一段 PyQGIS 代码：仅在用户逐次确认后以
        # QGIS 进程权限运行，且已做 AST 危险调用扫描 + 受限内建白名单 +
        # 模块白名单（与 AST 层同源），无 shell / 文件系统逃逸面。
        exec(code, namespace)  # nosec B102

        return {
            "executed": True,
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }
    except Exception as e:
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
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr


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

    layer = _find_layer(layer_id_or_name)
    if not layer:
        return _layer_not_found_error(layer_id_or_name)
    if layer.type() != QgsMapLayer.LayerType.VectorLayer:
        return {"error": f"图层 {layer.name()} 不是矢量图层，无法设置标注"}

    # 检查字段是否存在（字段名匹配同样忽略大小写，返回真实字段名）
    real_field = _find_field_name(layer, field_name)
    field_names = [_sanitize_untrusted(f.name(), 120) for f in layer.fields()]
    if real_field is None:
        return {
            "error": f"字段 '{field_name}' 不存在。可用字段: {field_names}",
            "hint": "字段名区分大小写，本工具已按忽略大小写查找仍未匹配；"
                    "可先调用 get_layer_profile 查看真实字段名。",
        }
    field_name = real_field

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
# 表格导出（CSV）
# ──────────────────────────────────────────────

# 禁止写入的目录前缀（macOS / Linux / Windows 并集）——写坏系统目录是真实的破坏面。
# ⚠️ 不要收录 "/var"、"/private" 这类**上层目录**：macOS 的临时目录正好在
# /var/folders/...（实体是 /private/var/folders/...），一刀切会把正常输出也拒掉
# （v2.4.12 首版就踩了：自测里所有落 /var/folders 的导出全被打回）。
# 只精确列敏感目录。
_UNSAFE_OUTPUT_DIRS = (
    "/bin", "/sbin", "/usr", "/etc", "/dev", "/boot", "/proc", "/sys",
    "/system", "/library", "/private/etc", "/private/var/db", "/private/var/root",
    "c:/windows", "c:/program files", "c:/program files (x86)", "c:/programdata",
)
# 单次导出行数硬上限（防一次把上千万行写爆内存/磁盘）
_MAX_CSV_ROWS = 1000000
# CSV 注入：字符串以这些字符开头时，Excel / LibreOffice 会当公式执行
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@")


def _desktop_dir() -> str:
    """桌面目录；不存在时退回用户主目录（macOS / Windows 通用）"""
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    return desktop if os.path.isdir(desktop) else home


def _resolve_csv_path(output_path):
    """把目标路径规整为可写的 CSV 绝对路径，返回 (path, error)。

    规则：留空 → 桌面/表格_<时间戳>.csv；相对路径 → 落桌面（用户常说「存到桌面」）；
    无扩展名 → 补 .csv；其它扩展名 → 明确拒绝（不要偷偷改名）；
    落在系统目录 → 拒绝。
    """
    name = str(output_path or "").strip()
    if not name:
        import time
        name = "表格_%s.csv" % time.strftime("%Y%m%d_%H%M%S")
    name = os.path.expanduser(name)
    if not os.path.isabs(name):
        name = os.path.join(_desktop_dir(), name)
    name = os.path.abspath(name)

    _root, ext = os.path.splitext(name)
    if not ext:
        name = name + ".csv"
    elif ext.lower() != ".csv":
        return None, ("目前只支持导出 .csv，请修改扩展名（收到: %s）"
                      % _sanitize_untrusted(ext, 20))

    normalized = name.replace("\\", "/").lower()
    for bad in _UNSAFE_OUTPUT_DIRS:
        if normalized == bad or normalized.startswith(bad + "/"):
            return None, ("拒绝写入系统目录 %s：请换到桌面或工程目录。"
                          % _sanitize_untrusted(bad, 60))

    parent = os.path.dirname(name)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            return None, "无法创建输出目录 %s: %s" % (_sanitize_untrusted(parent, 300), e)
    return name, None


def _csv_cell(value):
    """把任意值转成 CSV 单元格内容。

    数值原样写出（负号是正常数据）；文本走不可信数据净化，并对以 = + - @ 开头的
    字符串加前导单引号 —— 否则 Excel / LibreOffice 会把 ``=cmd|...`` 之类当公式执行
    （属性值来自外部数据源，属不可信输入）。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return value
    text = _sanitize_untrusted(value, 4000)
    if text[:1] in _CSV_INJECTION_PREFIXES:
        return "'" + text
    return text


def _rows_to_matrix(rows, columns=None):
    """把 rows 规整成 (header, matrix)，返回 (header, matrix, error)。

    支持 list[dict]（表头取键的并集，保持首次出现顺序）与 list[list]
    （首行全为字符串时视为表头，否则自动生成 列1..列N）。
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        return None, None, "rows 必须是非空数组。"
    first = rows[0]

    if isinstance(first, dict):
        header = [str(c) for c in columns] if columns else []
        if not header:
            for row in rows:
                if isinstance(row, dict):
                    for key in row.keys():
                        key = str(key)
                        if key not in header:
                            header.append(key)
        matrix = []
        for row in rows:
            row = row if isinstance(row, dict) else {}
            matrix.append([_csv_cell(row.get(col)) for col in header])
        return header, matrix, None

    if columns:
        header = [str(c) for c in columns]
        body = list(rows)
    elif all(isinstance(cell, str) for cell in first):
        header = [str(c) for c in first]
        body = list(rows[1:])
    else:
        header = ["列%d" % (i + 1) for i in range(len(first))]
        body = list(rows)

    matrix = []
    for row in body:
        if isinstance(row, dict):
            row = [row.get(col) for col in header]
        elif not isinstance(row, (list, tuple)):
            row = [row]
        matrix.append([_csv_cell(cell) for cell in row])
    return header, matrix, None


def export_table_to_csv(rows=None, layer=None, output_path=None, columns=None,
                        limit=0, only_selected=False):
    """把一份表格导出为 CSV 文件。

    ⚠️ 这是「把统计结果 / 属性表存成 CSV」的**唯一正确入口**：execute_pyqgis 的
    受限环境按安全设计**禁止导入 csv、禁止调用 open()**（现场：模型写
    ``import csv`` + ``open()`` 被安全扫描连续拒绝 10 次，用户要的 CSV 一直没生成，
    最后只能建议用户自己手动导出）。高频操作必须固化成工具，而不是指望模型在沙箱里
    裸写文件 IO。

    两种用法：
      1) rows：直接传算好的表格（统计结果最常用）——
         ``rows=[{"社区": "A", "面积km²": 1.23}, ...]`` 或
         ``rows=[["社区", "面积km²"], ["A", 1.23]]``（首行全为字符串时当表头）
      2) layer：导出某图层的属性表 —— ``layer="COM_S"``（名称忽略大小写），
         ``columns`` 可选指定字段（字段名同样忽略大小写），
         ``only_selected`` 只导选中要素。

    编码固定 utf-8-sig（带 BOM）：Windows 版 Excel 双击打开中文不乱码。
    返回写入的行数、列名与最终绝对路径（相对路径会被落到桌面）。
    """
    if rows is None and layer is None:
        return {
            "error": "必须提供 rows（表格数据）或 layer（图层名）之一。",
            "hint": "统计结果用 rows 传表格；导出属性表用 layer='图层名'。",
        }

    path, path_error = _resolve_csv_path(output_path)
    if path is None:
        return {"error": path_error}

    if os.path.exists(path) and not _skip_all_confirms:
        try:
            preview = json.dumps(
                {
                    "output_path": _sanitize_untrusted(path, 300),
                    "rows": len(rows) if isinstance(rows, (list, tuple)) else None,
                    "layer": _sanitize_untrusted(layer or "", 120),
                },
                ensure_ascii=False, indent=2,
            )
            if not _request_confirmation("export_table_to_csv",
                                         f"将覆盖已存在的文件：\n{preview}"):
                return {"error": "用户取消了 export_table_to_csv 操作。"}
        except Exception as e:
            logger.debug("export_table_to_csv 覆盖确认失败: %s", e, exc_info=True)
            return {"error": "确认通道未就绪，已拒绝覆盖已存在的文件。"}

    capped = _MAX_CSV_ROWS
    if limit:
        try:
            capped = min(_MAX_CSV_ROWS, max(1, int(limit)))
        except (TypeError, ValueError):
            capped = _MAX_CSV_ROWS

    if rows is not None:
        header, matrix, err = _rows_to_matrix(rows, columns)
        if err:
            return {"error": err}
        matrix = matrix[:capped]
    else:
        target = _find_layer(layer)
        if target is None:
            return _layer_not_found_error(layer)
        if target.type() != QgsMapLayer.LayerType.VectorLayer:
            return {"error": f"图层 {target.name()} 不是矢量图层，无法导出属性表。"}

        field_names = [f.name() for f in target.fields()]
        if columns:
            header = []
            for col in columns:
                real = _find_field_name(target, col)
                if real is None:
                    return {
                        "error": "字段 '%s' 不存在。可用字段: %s"
                                 % (_sanitize_untrusted(col, 120),
                                    [_sanitize_untrusted(n, 120) for n in field_names]),
                        "hint": "字段名区分大小写，本工具已按忽略大小写查找仍未匹配。",
                    }
                header.append(real)
        else:
            header = list(field_names)

        matrix = []
        try:
            features = target.selectedFeatures() if only_selected else target.getFeatures()
            for feature in features:
                if len(matrix) >= capped:
                    break
                row = []
                for field in header:
                    try:
                        row.append(_csv_cell(feature.attribute(field)))
                    except Exception:
                        row.append(_csv_cell(feature[field]))
                matrix.append(row)
        except Exception as e:
            logger.debug("读取图层要素失败: %s", e, exc_info=True)
            return {"error": f"读取图层要素失败: {e}"}

    try:
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(matrix)
        size = os.path.getsize(path)
    except Exception as e:
        logger.debug("写入 CSV 失败: %s", e, exc_info=True)
        return {"error": f"写入 CSV 失败: {e}", "path": path}

    result = {
        "path": path,
        "rows": len(matrix),
        "columns": header,
        "bytes": size,
        "encoding": "utf-8-sig",
        "message": "已导出 %d 行到 %s（UTF-8 带 BOM，Windows Excel 双击即可正常显示中文）。"
                   % (len(matrix), path),
    }
    if len(matrix) >= capped:
        result["truncated"] = True
        result["hint"] = "已达本次导出行数上限 %d，可能仍有剩余数据未写入。" % capped
    return result


# ──────────────────────────────────────────────
# 面积统计（按正确投影 / 中央经线）
# ──────────────────────────────────────────────

# CGCS2000 3 度带官方 EPSG 基准：4542 = 中央经线 99°E，每 3 度 +1
# （99→4542、102→4543、105→4544、114→4547…；实测 4542 的描述确为
#  "CGCS2000 / 3-degree Gauss-Kruger CM 99E"）
_ZONE_EPSG_BASE_CM = 99
_ZONE_EPSG_BASE = 4542
# 单级汇总最多回吐多少组（只影响回给模型的 JSON；CSV 落盘不受此限）
_MAX_AREA_GROUPS = 200


def _zone_crs_for_central_meridian(cm):
    """按中央经线取 CGCS2000 3 度带 CRS，返回 (crs, error)。

    ⚠️ **绝对不要**用 ``QgsCoordinateReferenceSystem("+proj=...")`` 直接构造：
    那样拿到的是**无效 CRS**（``isValid()`` 为 False）却**不抛任何异常**，
    ``QgsGeometry.transform()`` 会静默跳过 → 面积仍是平方度 → 除以 1e6 后
    四舍五入成 ``0.0``。全程零报错，是最难自查的一类坑（v2.4.13 实测踩到）。
    必须走 ``fromEpsgId`` / ``fromProj``，并且**显式校验 isValid()**。
    """
    epsg = None
    try:
        epsg = _ZONE_EPSG_BASE + int(round((cm - _ZONE_EPSG_BASE_CM) / 3.0))
        crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem.fromProj(
                "+proj=tmerc +lat_0=0 +lon_0=%d +k=1 +x_0=500000 +y_0=0 "
                "+ellps=GRS80 +units=m +no_defs" % cm)
    except Exception as e:  # noqa: BLE001
        return None, "构造中央经线 %d°E 的投影坐标系失败: %s" % (cm, e)
    if not crs.isValid():
        return None, ("无法为中央经线 %d°E 构造有效的投影坐标系"
                      "（EPSG:%s 与 proj 串均无效）；已中止，避免算出 0 面积。"
                      % (cm, epsg))
    return crs, None


def _as_field_list(value):
    """把 group_by / carry_fields 规整成非空字段名列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    return [str(v).strip() for v in items if str(v).strip()]


def _write_area_csvs(output_dir, layer, group_fields, carry, stores,
                     want_zone, want_ell, totals):
    """把面积统计写成 CSV（UTF-8 带 BOM）。返回文件清单（含出错项）。"""
    root = os.path.abspath(os.path.expanduser(str(output_dir or _desktop_dir())))
    normalized = root.replace("\\", "/").lower()
    for bad in _UNSAFE_OUTPUT_DIRS:
        if normalized == bad or normalized.startswith(bad + "/"):
            return [{"error": "拒绝写入系统目录 %s：请换到桌面或工程目录。"
                              % _sanitize_untrusted(bad, 60)}]
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:
        return [{"error": "无法创建输出目录 %s: %s"
                          % (_sanitize_untrusted(root, 300), e)}]

    stem = _UNSAFE_FILENAME_RE.sub("_", str(layer.name()) or "layer")
    written = []

    def _dump(fname, header, rows):
        path = os.path.join(root, fname)
        try:
            import csv
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(header)
                writer.writerows(rows)
            written.append({"path": path, "rows": len(rows),
                            "bytes": os.path.getsize(path)})
        except Exception as e:  # noqa: BLE001
            written.append({"path": path, "error": str(e)})

    def _area_of(rec):
        return rec["area"] if want_zone else rec["ell"]

    for field in group_fields:
        header = list(carry) + [field]
        if want_zone:
            header.append("面积_km2")
        if want_ell:
            header.append("椭球面积_km2")
        header.append("要素数")
        rows = []
        for key, rec in sorted(stores[field].items(), key=lambda kv: -_area_of(kv[1])):
            row = [rec["carry"].get(c, "") for c in carry] + [key]
            if want_zone:
                row.append(round(rec["area"] / 1e6, 4))
            if want_ell:
                row.append(round(rec["ell"] / 1e6, 4))
            row.append(rec["n"])
            rows.append(row)
        _dump("%s_面积_%s.csv" % (stem, field), header, rows)

    header = ["图层", "坐标系", "要素数"]
    row = [str(layer.name()), totals.get("crs", ""), totals.get("features_used", 0)]
    if want_zone:
        header.append("总面积_km2")
        row.append(totals.get("zone"))
    if want_ell:
        header.append("椭球总面积_km2")
        row.append(totals.get("ell"))
    _dump("%s_面积_汇总.csv" % stem, header, [row])
    return written


def compute_area_stats(layer_id_or_name=None, group_by=None, carry_fields=None,
                       output_dir=None, method="both", limit=0):
    """【面积统计】按正确的投影 / 中央经线统计矢量**面**图层的面积，可按字段分级汇总。

    这是「统计某图层的面积」「按区县 / 乡镇 / 村分级汇总面积」这类需求的**唯一正确
    入口**——不要再用 execute_pyqgis 手写 PyQGIS。手写极易踩两个坑：① 直接拿地理
    坐标系（如 EPSG:4490）算面积，得到的是**平方度**，毫无意义；② 用
    ``QgsCoordinateReferenceSystem("+proj=...")`` 构造投影坐标系，拿到的是
    **无效 CRS 且不报错**，``transform()`` 静默跳过 → 面积全是 0。

    两种口径同时给出，便于互相校验：

    - ``zone``：逐要素按**质心经度**取 3 度带（中央经线 ``CM = 3*round(lon/3)``，
      CGCS2000，走官方 EPSG 4542/4543/4544…），把几何变换到该带后取**投影平面**面积。
      这就是「按分度带投影与中央经线算面积」的标准做法。
    - ``ellipsoid``：``QgsDistanceArea`` + GRS80 椭球算**大地测量面积**，与投影无关，
      可作基准。两者差异通常 <0.03%（投影在中央经线之外的尺度畸变）。

    参数：
    - layer_id_or_name：图层名或 ID（名称匹配忽略大小写，见 _find_layer）
    - group_by：可选，字段名 或 字段名列表。传了就对每个字段各出一张分级汇总表
      （例如 ["COU_NAME", "CITY_NAME"] 同时出区县 / 地市州两级）
    - carry_fields：可选，附加到每行的字段（取该组第一条要素的值），便于带上编码
    - output_dir：可选，给了就把每级汇总写成 CSV（UTF-8 带 BOM，Excel 双击不乱码）
    - method：zone / ellipsoid / both（默认 both）
    - limit：每级最多回吐多少组（0 = 默认上限），CSV 不受此限
    """
    try:
        target = _find_layer(layer_id_or_name)
        if target is None:
            return _layer_not_found_error(layer_id_or_name)
        if target.type() != QgsMapLayer.LayerType.VectorLayer:
            return {"error": "图层 %s 不是矢量图层，无法统计面积。"
                             % _sanitize_untrusted(target.name(), 120)}

        geom_name = _geometry_type_name(target)
        if geom_name != "Polygon":
            return {
                "error": "图层 %s 不是面图层（当前几何类型：%s），无法统计面积。"
                         % (_sanitize_untrusted(target.name(), 120), geom_name),
                "hint": "本工具只处理 Polygon / MultiPolygon。其它几何类型请改用 "
                        "get_layer_profile 的字段统计，或用 execute_pyqgis 手写。",
            }

        mode = str(method or "both").strip().lower()
        if mode not in ("zone", "ellipsoid", "both"):
            return {"error": "method 只能是 zone / ellipsoid / both（收到: %s）。"
                             % _sanitize_untrusted(mode, 20),
                    "hint": "默认 both，会同时给出 3 度带投影面积与椭球面积，便于校验。"}
        want_zone = mode in ("zone", "both")
        want_ell = mode in ("ellipsoid", "both")

        field_names = [_sanitize_untrusted(f.name(), 120) for f in target.fields()]

        def _resolve(requested, what):
            real = _find_field_name(target, requested)
            if real is None:
                return None, {
                    "error": "未找到%s '%s'。" % (what, _sanitize_untrusted(requested, 120)),
                    "available_fields": field_names,
                    "hint": "字段名区分大小写，本工具已按忽略大小写查找仍未匹配。",
                }
            return real, None

        group_fields = []
        for raw in _as_field_list(group_by):
            real, err = _resolve(raw, "字段")
            if err:
                return err
            if real not in group_fields:
                group_fields.append(real)

        carry = []
        for raw in _as_field_list(carry_fields):
            real, err = _resolve(raw, "附加字段")
            if err:
                return err
            if real not in carry and real not in group_fields:
                carry.append(real)

        from qgis.core import (QgsDistanceArea, QgsCoordinateTransform,
                               QgsGeometry as _QGeom)

        project = QgsProject.instance()
        tc = project.transformContext()
        src_crs = target.crs()

        dist = None
        if want_ell:
            dist = QgsDistanceArea()
            dist.setSourceCrs(src_crs, tc)
            try:
                dist.setEllipsoid("GRS80")
            except Exception:  # noqa: BLE001
                dist.setEllipsoid("WGS84")

        cache = {}
        zone_counts = {}
        zone_crs_used = {}
        stores = {}
        for f in group_fields:
            stores[f] = {}
        total_zone = 0.0
        total_ell = 0.0
        used = 0
        skipped = 0

        for feat in target.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                skipped += 1
                continue
            a_zone = 0.0
            a_ell = 0.0
            if want_zone:
                cm = int(round(float(geom.centroid().asPoint().x()) / 3.0)) * 3
                ent = cache.get(cm)
                if ent is None:
                    crs, err = _zone_crs_for_central_meridian(cm)
                    if err:
                        return {"error": err, "central_meridian": cm}
                    ent = (QgsCoordinateTransform(src_crs, crs, tc), crs)
                    cache[cm] = ent
                tr, crs = ent
                zk = "CM%dE" % cm
                zone_counts[zk] = zone_counts.get(zk, 0) + 1
                zone_crs_used[zk] = crs.authid() or crs.description()
                moved = _QGeom(geom)
                moved.transform(tr)
                a_zone = moved.area()
                total_zone += a_zone
            if want_ell:
                a_ell = dist.measureArea(geom)
                total_ell += a_ell
            used += 1

            for f in group_fields:
                try:
                    value = feat[f]
                except Exception:  # noqa: BLE001
                    value = None
                key = "" if value is None else str(value)
                rec = stores[f].get(key)
                if rec is None:
                    rec = {"area": 0.0, "ell": 0.0, "n": 0, "carry": {}}
                    for cf in carry:
                        try:
                            rec["carry"][cf] = _csv_cell(feat[cf])
                        except Exception:  # noqa: BLE001
                            rec["carry"][cf] = ""
                    stores[f][key] = rec
                rec["area"] += a_zone
                rec["ell"] += a_ell
                rec["n"] += 1

        cap = _MAX_AREA_GROUPS
        try:
            if limit:
                cap = max(1, min(_MAX_AREA_GROUPS, int(limit)))
        except (TypeError, ValueError):
            cap = _MAX_AREA_GROUPS

        result = {
            "layer": _sanitize_untrusted(target.name(), 120),
            "layer_id": target.id(),
            "crs": src_crs.authid() or src_crs.description(),
            "geometry_type": geom_name,
            "features_used": used,
            "features_skipped": skipped,
            "unit": "km²",
            "method": {
                "zone": "3 度带高斯-克吕格投影，逐要素质心经度取带"
                        "（中央经线 CM = 3*round(lon/3)）",
                "ellipsoid": "椭球大地测量（QgsDistanceArea + GRS80），与投影无关",
                "both": "同时给出 3 度带投影面积与椭球面积，两者可互相校验",
            }[mode],
        }
        if want_zone:
            result["zone_feature_counts"] = zone_counts
            result["zone_crs_used"] = zone_crs_used
            result["total_km2_zone"] = round(total_zone / 1e6, 4)
        if want_ell:
            result["total_km2_ellipsoid"] = round(total_ell / 1e6, 4)
        if want_zone and want_ell and total_ell:
            result["difference_pct"] = round(
                (total_zone - total_ell) / total_ell * 100.0, 6)
            result["difference_km2"] = round((total_zone - total_ell) / 1e6, 4)

        if group_fields:
            groups = []
            for f in group_fields:
                rows = []
                for key, rec in sorted(stores[f].items(),
                                       key=lambda kv: -(kv[1]["area"] if want_zone
                                                        else kv[1]["ell"])):
                    item = {f: _sanitize_untrusted(key, 200)}
                    for cf in carry:
                        item[cf] = rec["carry"].get(cf, "")
                    if want_zone:
                        item["area_km2"] = round(rec["area"] / 1e6, 4)
                    if want_ell:
                        item["area_km2_ellipsoid"] = round(rec["ell"] / 1e6, 4)
                    item["feature_count"] = rec["n"]
                    rows.append(item)
                entry = {"field": f, "group_count": len(rows), "items": rows[:cap]}
                if len(rows) > cap:
                    entry["truncated"] = True
                    entry["hint"] = ("仅返回前 %d 组（已按面积降序）；完整结果请传 "
                                     "output_dir 落 CSV。" % cap)
                groups.append(entry)
            result["groups"] = groups
        elif not output_dir:
            result["hint"] = ("未按字段分组。若要按区县 / 乡镇等分级汇总，请传 "
                              "group_by=[\"字段名\", ...]；要落 CSV 请传 output_dir。")

        if used == 0:
            result["hint"] = "图层里没有任何有效面要素，未产生统计数据。"

        if output_dir:
            result["csv_files"] = _write_area_csvs(
                output_dir, target, group_fields, carry, stores,
                want_zone, want_ell,
                {"crs": result["crs"], "features_used": used,
                 "zone": round(total_zone / 1e6, 4), "ell": round(total_ell / 1e6, 4)})
        return result
    except Exception as e:  # noqa: BLE001
        logger.debug("compute_area_stats 失败: %s", e, exc_info=True)
        return {"error": "统计面积失败: %s" % e}


# ──────────────────────────────────────────────
# 逐要素出图（打印布局：指北针 + 比例尺）
# ──────────────────────────────────────────────

# 文件名禁用字符（Windows/Linux/macOS 并集）+ 控制字符
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
# Windows 保留设备名，用作文件名会导致创建失败
_RESERVED_FILENAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + ["com%d" % i for i in range(1, 10)]
    + ["lpt%d" % i for i in range(1, 10)]
)
# 指北针 SVG 候选相对目录（覆盖 macOS bundle / Windows OSGeo4W / Linux 布局）
_SVG_REL_DIRS = ("svg", "qgis/svg", "Resources/qgis/svg",
                 "share/qgis/svg", "Resources/svg")
# 标准图纸尺寸（毫米，纵向宽×高）——制图常见诉求如「按 B0 号图出图」
_STANDARD_PAGE_SIZES = {
    "A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0),
    "A1": (594.0, 841.0), "A0": (841.0, 1189.0),
    "B4": (250.0, 353.0), "B3": (353.0, 500.0), "B2": (500.0, 707.0),
    "B1": (707.0, 1000.0), "B0": (1000.0, 1414.0),
}


def _safe_filename(raw, max_len: int = 80) -> str:
    """把任意要素属性值净化成安全的文件名主体（不含扩展名）。

    要素属性属于不可信外部数据，可能含路径分隔符、控制字符或 Windows 保留名，
    直接拼进路径会造成写到目录外或创建失败。
    """
    s = _UNSAFE_FILENAME_RE.sub("_", str(raw if raw is not None else ""))
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(". ")          # '.' / '..' 与 Windows 尾部点号均非法
    if not s:
        s = "feature"
    if s.lower() in _RESERVED_FILENAMES:
        s = "_" + s
    return s[:max_len]


def _find_north_arrow_svg():
    """定位 QGIS 自带的指北针 SVG，找不到返回 None（调用方降级，不报错）。

    为什么不写死路径：QGIS 的资源目录在各平台差异极大 —— macOS bundle 在
    ``<app>/Contents/Resources/qgis/svg/arrows/``（且无头模式下 ``prefixPath``
    为空、``svgPaths()`` 会返回带重复 ``Contents/MacOS`` 段的无效路径），
    Windows OSGeo4W 在 ``<prefix>/svg/arrows/``，Linux 在 ``/usr/share/qgis/svg/``。
    这里把官方搜索路径、pkgDataPath 与「从各前缀逐级向上探测」三路合并。
    """
    cands = []
    with contextlib.suppress(Exception):
        for p in (QgsApplication.svgPaths() or []):
            if isinstance(p, str) and p:
                cands.append(p)
    with contextlib.suppress(Exception):
        pkg = QgsApplication.pkgDataPath()
        if isinstance(pkg, str) and pkg:
            cands.append(os.path.join(pkg, "svg"))
    # 只接受真正的字符串：某些发行版/替身环境下这些 API 可能返回非字符串，
    # 直接 os.path.abspath 会抛 TypeError。
    roots = []
    for r in (QgsApplication.prefixPath(),
              os.environ.get("QGIS_PREFIX_PATH", ""),
              os.environ.get("QGIS_APP", ""),
              os.environ.get("QGIS_PLUGINPATH", "")):
        if isinstance(r, str) and r.strip():
            roots.append(r)
    for root in roots:
        try:
            d = os.path.abspath(root)
        except (TypeError, ValueError):
            continue
        for _ in range(6):          # 最多上溯 6 层（bundle 内需 3 层）
            for rel in _SVG_REL_DIRS:
                cands.append(os.path.join(d, rel))
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    for d in cands:
        target = None
        if os.path.isdir(os.path.join(d, "arrows")):
            target = os.path.join(d, "arrows")
        elif os.path.isdir(d):
            target = d
        if not target:
            continue
        try:
            names = sorted(os.listdir(target))
        except OSError:
            continue
        for fn in names:
            low = fn.lower()
            if low.startswith("northarrow") and low.endswith(".svg"):
                return os.path.join(target, fn)
    return None


def export_features_maps(
    layer: str = "",
    output_dir: str = "",
    name_field: str = "",
    width_px: int = 1600,
    height_px: int = 1200,
    dpi: int = 150,
    margin_percent: float = 10.0,
    north_arrow: bool = True,
    scale_bar: bool = True,
    limit: int = 100,
    page_size: str = "",
    orientation: str = "portrait",
):
    """对指定图层的每个要素逐个出图，可配置指北针与比例尺。

    为什么要有这个工具（v2.4.10）：这类「逐要素出图 + 指北针 + 比例尺」的需求
    原先只能靠 execute_pyqgis 裸写 PyQGIS 实现，而模型极易写出过时 API ——
    例如 ``QgsLayoutItemNorthArrow``，该类在 QGIS 3.44 / 4.x 中**已被移除**
    （实测两版均不存在），于是必然报错，模型反复重写、白烧工具轮次直至上限，
    最终任务失败。这里把整条链路固化成一次调用：建临时打印布局 → 地图项铺满
    页面 → 指北针（QgsLayoutItemPicture + setNorthMode，QGIS 现行做法）→
    比例尺（QgsLayoutItemScaleBar）→ 逐要素设范围并导出 PNG。

    参数
    ----
    layer: 图层名称或 ID（矢量图层）
    output_dir: 输出目录绝对路径，不存在会自动创建
    name_field: 用于命名文件的字段名；留空或值为空时用要素 ID
    width_px / height_px / dpi: 输出图片像素尺寸与分辨率（决定页面物理尺寸）
    margin_percent: 要素范围外扩百分比，避免要素贴边（默认 10）
    north_arrow / scale_bar: 是否添加指北针 / 比例尺
    limit: 最多导出多少个要素（防一次导出上千张把主线程卡死）
    page_size: 标准图纸尺寸（"A4"/"A3"/"B0" 等）；给了它则忽略宽高像素，
        像素尺寸由 dpi 换算，保证「图纸 1 mm 就是 1 mm」
    orientation: 图纸方向 portrait（纵向，默认）/ landscape（横向）

    返回
    ----
    export/输出的文件清单、失败项、指北针与比例尺的实际状态
    """
    try:
        from qgis.core import (
            QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemPicture,
            QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize,
            QgsLayoutExporter, QgsUnitTypes, QgsRectangle,
        )
    except ImportError as e:
        return {"error": f"当前 QGIS 缺少打印布局 API，无法逐要素出图: {e}"}

    # ── 参数校验（一律夹到安全区间，避免非法值把主线程拖死）──
    try:
        width_px = max(200, min(int(width_px or 1600), 20000))
        height_px = max(200, min(int(height_px or 1200), 20000))
        dpi = max(36, min(int(dpi or 150), 600))
        limit = max(1, min(int(limit or 100), 1000))
        margin_percent = max(0.0, min(float(margin_percent or 0.0), 200.0))
    except (TypeError, ValueError) as e:
        return {"error": f"参数类型不合法: {e}"}

    # ── 输出目录（先校验参数，再去找图层：参数错了不必等图层查找）──
    output_dir = str(output_dir or "").strip()
    if not output_dir:
        return {"error": "必须提供 output_dir（输出目录的绝对路径）。"}
    if not os.path.isabs(output_dir):
        return {"error": f"output_dir 必须是绝对路径：{output_dir}"}
    if not os.path.isdir(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            return {"error": f"无法创建输出目录 {output_dir}: {e}"}

    # ── 定位图层（名称匹配忽略大小写）──
    project = QgsProject.instance()
    target = _find_layer(layer) if layer else None
    if target is None:
        return _layer_not_found_error(layer)
    if target.type() != QgsMapLayer.LayerType.VectorLayer:
        return {"error": f"图层「{target.name()}」不是矢量图层，无法逐要素出图。"}

    # ── 收集要素与目标文件名 ──
    total = target.featureCount()
    plans = []
    skipped = []
    used_names = {}
    try:
        for feat in target.getFeatures():
            if len(plans) >= limit:
                break
            raw = ""
            if name_field:
                try:
                    raw = feat[name_field]
                except Exception as e:
                    logger.debug("读取字段 %s 失败（改用要素 ID）: %s", name_field, e)
                    raw = ""
            if raw is None or not str(raw).strip():
                raw = "fid_%d" % feat.id()
            stem = _safe_filename(raw)
            n = used_names.get(stem, 0) + 1
            used_names[stem] = n
            if n > 1:
                stem = "%s_%d" % (stem, n)
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                skipped.append({"fid": feat.id(), "reason": "空几何"})
                continue
            plans.append({
                "fid": feat.id(),
                "stem": stem,
                "rect": QgsRectangle(geom.boundingBox()),
            })
    except Exception as e:
        return {"error": f"读取图层要素失败: {e}"}

    if not plans:
        return {
            "error": f"图层「{target.name()}」没有可导出的要素（空几何或无要素）。",
            "total_features": total,
        }

    # ── 覆盖确认（与 render_map 同策略：只在会覆盖已有文件时问一次）──
    clash = [
        os.path.join(output_dir, p["stem"] + ".png")
        for p in plans
        if os.path.exists(os.path.join(output_dir, p["stem"] + ".png"))
    ]
    if clash and not _skip_all_confirms:
        if _code_confirm_callback is None:
            return {"error": "确认通道未就绪，已拒绝覆盖已存在的文件。"}
        preview = json.dumps(
            {"output_dir": _sanitize_untrusted(output_dir, 300),
             "覆盖数量": len(clash), "示例": clash[:5]},
            ensure_ascii=False, indent=2,
        )
        try:
            if not _request_confirmation("export_features_maps", f"将覆盖已存在的图片：\n{preview}"):
                return {"error": "用户取消了 export_features_maps 操作。"}
        except Exception as e:
            logger.debug("export_features_maps 覆盖确认失败: %s", e, exc_info=True)
            return {"error": "确认通道未就绪，已拒绝覆盖已存在的文件。"}

    # ── 构建临时打印布局（不注册进 layoutManager，不污染用户工程）──
    layout = QgsPrintLayout(project)
    try:
        layout.initializeDefaults()
    except Exception as e:
        logger.debug("布局初始化默认页面失败: %s", e, exc_info=True)
    layout.setName("__qgis_agent_feature_export__")

    # 图纸尺寸优先：给了 page_size（如 "B0"）就按标准图纸出图，像素尺寸由 dpi
    # 反推，保证「图纸 1 mm 就是 1 mm」；未给则按 width_px/height_px + dpi 换算。
    std = _STANDARD_PAGE_SIZES.get(str(page_size or "").strip().upper())
    if std:
        page_w_mm, page_h_mm = std
        if str(orientation or "").strip().lower().startswith("land"):
            page_w_mm, page_h_mm = page_h_mm, page_w_mm
        width_px = max(200, min(int(round(page_w_mm / 25.4 * dpi)), 20000))
        height_px = max(200, min(int(round(page_h_mm / 25.4 * dpi)), 20000))
        if width_px >= 20000 or height_px >= 20000:
            # 大图纸 + 高 dpi 会撞像素上限：收敛 dpi，避免导出把主线程卡死
            dpi = max(36, min(
                int(20000.0 / page_w_mm * 25.4),
                int(20000.0 / page_h_mm * 25.4),
            ))
            width_px = int(round(page_w_mm / 25.4 * dpi))
            height_px = int(round(page_h_mm / 25.4 * dpi))
    else:
        page_w_mm = max(20.0, float(width_px) / float(dpi) * 25.4)
        page_h_mm = max(20.0, float(height_px) / float(dpi) * 25.4)
    try:
        layout.pageCollection().page(0).setPageSize(
            QgsLayoutSize(page_w_mm, page_h_mm, QgsUnitTypes.LayoutMillimeters))
    except Exception as e:
        logger.debug("设置页面尺寸失败，沿用默认页面: %s", e, exc_info=True)

    edge = min(page_w_mm, page_h_mm) * 0.02
    map_w = page_w_mm - edge * 2
    map_h = page_h_mm - edge * 2

    map_item = QgsLayoutItemMap(layout)
    with contextlib.suppress(Exception):
        map_item.setBackgroundColor(QColor(255, 255, 255))
    map_item.attemptMove(QgsLayoutPoint(edge, edge, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(map_item)

    # ── 指北针（QgsLayoutItemNorthArrow 已移除，改用 Picture + setNorthMode）──
    north_note = ""
    if north_arrow:
        svg_path = _find_north_arrow_svg()
        if not svg_path:
            north_note = "未找到 QGIS 自带指北针 SVG，本次未添加"
            logger.warning("未找到指北针 SVG，已跳过（不影响出图）")
        else:
            try:
                pic = QgsLayoutItemPicture(layout)
                pic.setPicturePath(svg_path)
                # 枚举取自 QgsLayoutItemPicture 自身；用
                # QgsLayoutNorthArrowHandler.NorthMode 会 TypeError。
                if hasattr(pic, "setNorthMode"):
                    pic.setNorthMode(QgsLayoutItemPicture.TrueNorth)
                if hasattr(pic, "setLinkedMap"):
                    pic.setLinkedMap(map_item)      # 跟地图旋转联动
                side = min(page_w_mm, page_h_mm) * 0.08
                pic.attemptMove(QgsLayoutPoint(
                    page_w_mm - side - edge, edge, QgsUnitTypes.LayoutMillimeters))
                pic.attemptResize(
                    QgsLayoutSize(side, side, QgsUnitTypes.LayoutMillimeters))
                layout.addLayoutItem(pic)
                north_note = os.path.basename(svg_path)
            except Exception as e:
                logger.debug("添加指北针失败: %s", e, exc_info=True)
                north_note = f"添加失败（已跳过）: {e}"

    # ── 比例尺（长度必须在每个要素的地图范围确定后重算，这里只建不量）──
    scale_note = ""
    sb = None
    if scale_bar:
        try:
            sb = QgsLayoutItemScaleBar(layout)
            sb.setStyle("Single Box")
            sb.setLinkedMap(map_item)
            sb.attemptMove(QgsLayoutPoint(
                edge + page_w_mm * 0.03,
                page_h_mm - edge - page_h_mm * 0.07,
                QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(sb)
            scale_note = "Single Box"
        except Exception as e:
            logger.debug("添加比例尺失败: %s", e, exc_info=True)
            sb = None
            scale_note = f"添加失败（已跳过）: {e}"

    # ── 逐要素导出 ──
    exporter = QgsLayoutExporter(layout)
    settings = exporter.ImageExportSettings()
    settings.dpi = dpi
    ratio = (map_w / map_h) if map_h else 1.0
    exported_files = []
    failed = []
    for p in plans:
        rect = QgsRectangle(p["rect"])
        if rect.width() <= 0 or rect.height() <= 0:
            # 点要素 / 退化几何：给一个可用的最小范围
            span = 0.002 if target.crs().isGeographic() else 50.0
            ccx, ccy = rect.center().x(), rect.center().y()
            rect = QgsRectangle(ccx - span, ccy - span, ccx + span, ccy + span)
        pad = max(rect.width(), rect.height()) * (margin_percent / 100.0)
        rect = QgsRectangle(rect.xMinimum() - pad, rect.yMinimum() - pad,
                            rect.xMaximum() + pad, rect.yMaximum() + pad)
        # 让出图范围与页面纵横比一致，避免要素被拉伸或裁切
        w, h = rect.width(), rect.height()
        ccx, ccy = rect.center().x(), rect.center().y()
        if ratio > 0 and h > 0 and (w / h) < ratio:
            half = h * ratio / 2.0
            rect = QgsRectangle(ccx - half, rect.yMinimum(), ccx + half, rect.yMaximum())
        elif ratio > 0 and w > 0:
            half = w / ratio / 2.0
            rect = QgsRectangle(rect.xMinimum(), ccy - half, rect.xMaximum(), ccy + half)

        map_item.setExtent(rect)
        with contextlib.suppress(Exception):
            map_item.refresh()
        # 比例尺长度依赖地图「当前」比例尺：必须在 setExtent 之后重算，
        # 否则算出来是 0（图上只见一个「0」）。每个要素比例尺不同，逐个重算。
        # PyQt6 下 referenceWidth 按 int 校验，传 float 会 TypeError。
        if sb is not None:
            try:
                sb.applyDefaultSize(int(map_w * 0.3))
            except TypeError:
                with contextlib.suppress(Exception):
                    sb.applyDefaultSize()
            except Exception as e:
                logger.debug("比例尺重算失败: %s", e, exc_info=True)
        with contextlib.suppress(Exception):
            layout.refresh()

        out_path = os.path.join(output_dir, p["stem"] + ".png")
        try:
            res = exporter.exportToImage(out_path, settings)
        except Exception as e:
            res = e
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            exported_files.append(out_path)
        else:
            failed.append({"fid": p["fid"], "file": out_path, "result": str(res)})

    result = {
        "exported": len(exported_files),
        "total_features": total,
        "output_dir": output_dir,
        "files": exported_files[:50],
        "size_px": [width_px, height_px],
        "dpi": dpi,
        "north_arrow": north_note or "未启用",
        "scale_bar": scale_note or "未启用",
    }
    if failed:
        result["failed"] = failed[:10]
        result["hint"] = "部分要素导出失败，多为要素范围过小或输出路径不可写。"
    if skipped:
        result["skipped"] = skipped[:10]
    if len(plans) < total:
        result["truncated"] = True
        result["hint"] = (
            f"图层共 {total} 个要素，本次按 limit={limit} 只导出了前 {len(plans)} 个；"
            f"需要全部导出请提高 limit（会按批次占用主线程）。"
        )
    return result


# ──────────────────────────────────────────────
# 主线程调度器（解决 QGIS API 线程安全问题）
# ──────────────────────────────────────────────

from qgis.PyQt.QtCore import pyqtSignal, pyqtSlot, QMutex, QWaitCondition, QThread  # noqa: E402

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
    """在主线程中初始化桥接器。由插件入口 qgis_agent.py 调用。

    幂等：关闭再打开 Dock 时不会重复 connect，避免同一工具被执行多次。
    """
    bridge = _MainThreadBridge.get()
    if not getattr(bridge, "_wired", False):
        bridge.execute_request.connect(bridge._on_execute)
        bridge.confirm_request.connect(bridge._on_confirm)
        bridge._wired = True
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
# 算法参数 / 图层档案 / 渲染 / 投影转换工具
# ──────────────────────────────────────────────

def _find_layer(layer_id_or_name: str):
    """按 ID 或名称查找图层，找不到时返回 None。

    ⚠️ 名称匹配**必须大小写不敏感**（v2.4.12 修复）。现场故障：工程里的图层叫
    ``COM_S``，模型把用户口述的图层名写成小写的 ``com_s`` 去查，精确匹配落空 →
    工具回「未找到图层: com_s」→ 模型判断成「图层不存在」，连续 4 次失败后放弃；
    而用户看到的是「刚才还能找到怎么现在找不到了？图层一直存在，是大写的」。

    匹配顺序：ID 精确 → 名称精确 → 名称（去首尾空白 + casefold 折叠）→
    再去掉 ``.shp`` / ``.gpkg`` 之类的扩展名重试（模型常把数据源文件名当图层名）。
    """
    if layer_id_or_name is None:
        return None
    key = str(layer_id_or_name).strip()
    if not key:
        return None

    project = QgsProject.instance()
    layer = project.mapLayer(key)
    if layer:
        return layer

    layers = list(project.mapLayers().values())
    for lyr in layers:
        if lyr.name() == key:
            return lyr

    folded = key.casefold()
    for lyr in layers:
        if lyr.name().strip().casefold() == folded:
            return lyr

    if "." in folded:
        stem = folded.rsplit(".", 1)[0]
        if stem:
            for lyr in layers:
                if lyr.name().strip().casefold() == stem:
                    return lyr
    return None


def _closest_layer_names(layer_id_or_name, names, limit: int = 3) -> list:
    """从工程内真实图层名里挑出与输入最接近的几个（用于自我纠正提示）。

    排序依据：忽略大小写后完全相等 → 互相包含 → difflib 相似度 ≥ 0.5。
    只做提示，不改变匹配结果（匹配由 _find_layer 决定）。
    """
    key = str(layer_id_or_name or "").strip().casefold()
    if not key:
        return []
    same, contains, scored = [], [], []
    for name in names:
        folded = str(name).strip().casefold()
        if not folded:
            continue
        if folded == key:
            same.append(name)
        elif key in folded or folded in key:
            contains.append(name)
        else:
            try:
                ratio = difflib.SequenceMatcher(None, key, folded).ratio()
            except Exception:
                ratio = 0.0
            if ratio >= 0.5:
                scored.append((ratio, name))
    scored.sort(key=lambda item: -item[0])
    out = []
    for name in same + contains + [n for _r, n in scored]:
        if name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _layer_not_found_error(layer_id_or_name) -> dict:
    """统一的「未找到图层」返回体：附上工程内真实图层名与最接近的候选。

    v2.4.12：原先 7 处出口各自只回一句「未找到图层: X」，模型既不知道自己写错了
    大小写，也看不到正确的名字，只能再摸一轮 get_qgis_info —— 用户看到的就是
    一连串「未找到图层」的失败（铁律 7：同一份约定必须在所有出口保持一致）。
    """
    out = {"error": "未找到图层: %s" % _sanitize_untrusted(layer_id_or_name, 120)}
    try:
        names = [_sanitize_untrusted(lyr.name(), 120)
                 for lyr in QgsProject.instance().mapLayers().values()]
    except Exception as e:
        logger.debug("枚举图层名失败（已忽略）: %s", e, exc_info=True)
        names = []

    # 键恒定存在（哪怕为空列表）：调用方/守卫按同一形状读取，不必分支判断
    out["available_layers"] = names[:50]

    if not names:
        out["hint"] = "当前工程里没有任何图层，请先用 add_vector_layer 添加数据。"
        return out

    near = _closest_layer_names(layer_id_or_name, names)
    if near:
        out["did_you_mean"] = near
        out["hint"] = ("图层名区分大小写，本工具已按「忽略大小写」查找仍未匹配。"
                       "是否想用：%s？" % "、".join(near))
    else:
        out["hint"] = "可先调用 get_qgis_info 查看当前工程中的图层名称与 id。"
    return out


def _find_field_name(layer, field_name) -> str:
    """在图层字段里找真实字段名，找不到返回 None。

    与 _find_layer 同一个坑的另一半：shapefile/gpkg 的字段名常是 ``NAME`` /
    ``AREA_KM2`` 这种大写，而模型按用户口述写成小写去比对，精确匹配就会落空
    → 报「字段不存在」。这里做「精确 → 忽略大小写 + 去首尾空白」两级匹配，
    返回**真实字段名**（后续用真实名去取属性，不要再拿输入值）。
    """
    if field_name is None:
        return None
    key = str(field_name).strip()
    if not key:
        return None
    names = []
    try:
        names = [f.name() for f in layer.fields()]
    except Exception as e:
        logger.debug("读取字段列表失败: %s", e, exc_info=True)
        return None
    for name in names:
        if name == key:
            return name
    folded = key.casefold()
    for name in names:
        if name.strip().casefold() == folded:
            return name
    return None


def _enum_value(cls, enum_name: str, value_name: str):
    """按作用域枚举取值（PyQt5 扁平写法 / PyQt6 作用域写法双兼容）。

    依次尝试 cls.<enum_name>.<value_name> 与 cls.<value_name>，
    都取不到时返回 None（交由调用方回退）。
    """
    for holder_name in (enum_name, None):
        holder = cls if holder_name is None else getattr(cls, holder_name, None)
        with contextlib.suppress(Exception):
            return getattr(holder, value_name)
    return None


# ──────────────────────────────────────────────
# 1. get_algorithm_parameters
# ──────────────────────────────────────────────

def _processing_flag_names(flags_value: int) -> list:
    """把参数 flags 位掩码翻译为可读名称列表（枚举作用域双兼容）"""
    try:
        from qgis.core import QgsProcessingParameterDefinition as _Def
    except Exception as e:
        logger.debug("QgsProcessingParameterDefinition 导入失败: %s", e, exc_info=True)
        return []

    names = []
    for value_name in ("FlagOptional", "FlagAdvanced", "FlagHidden", "FlagIsModelOutput"):
        flag = _enum_value(_Def, "Flag", value_name)
        if flag is None:
            flag = getattr(_Def, value_name, None)
        try:
            if flag is not None and (int(flags_value) & int(flag)):
                names.append(value_name)
        except Exception as e:
            logger.debug("解析参数标记 %s 失败: %s", value_name, e, exc_info=True)
    return names


def _describe_processing_parameter(param) -> dict:
    """把单个 Processing 参数定义转为可序列化 dict"""
    flags_value = 0
    try:
        flags_value = int(param.flags())
    except Exception as e:
        logger.debug("读取参数 flags 失败: %s", e, exc_info=True)

    flag_names = _processing_flag_names(flags_value)
    optional = "FlagOptional" in flag_names
    if not optional:
        # 少数版本未暴露 Flag 枚举，退化为探测 isOptional()
        try:
            is_optional = getattr(param, "isOptional", None)
            if callable(is_optional):
                optional = bool(is_optional())
        except Exception as e:
            logger.debug("探测参数可选性失败: %s", e, exc_info=True)

    default_value = None
    try:
        raw_default = param.defaultValue()
        if raw_default is None:
            default_value = None
        elif isinstance(raw_default, (bool, int, float, str)):
            default_value = raw_default
        else:
            # 默认值可能来自模型文件，属不可信输入
            default_value = _sanitize_untrusted(raw_default, 200)
    except Exception as e:
        logger.debug("读取参数默认值失败: %s", e, exc_info=True)

    info = {
        "name": _sanitize_untrusted(param.name(), 120),
        "description": _sanitize_untrusted(param.description(), 300),
        "type": _sanitize_untrusted(param.type(), 60),
        "python_class": _sanitize_untrusted(type(param).__name__, 80),
        "default_value": default_value,
        "optional": optional,
        "flags": flag_names,
    }

    # 枚举/数值/字段类参数的取值约束，帮助 LLM 正确赋值
    for attr in ("options", "minimum", "maximum"):
        try:
            getter = getattr(param, attr, None)
            if not callable(getter):
                continue
            value = getter()
            info[attr] = value if isinstance(value, (bool, int, float, str)) else _sanitize_untrusted(value, 200)
        except Exception as e:
            logger.debug("读取参数 %s 失败: %s", attr, e, exc_info=True)
    return info


def _suggest_algorithm_ids(query: str, limit: int = 5) -> list:
    """在 Processing 注册表中模糊匹配算法 id，返回候选列表"""
    try:
        algorithms = QgsApplication.processingRegistry().algorithms() or []
    except Exception as e:
        logger.debug("枚举 Processing 算法失败: %s", e, exc_info=True)
        return []

    text = str(query or "").strip().lower()
    tail = text.split(":")[-1] if text else ""
    scored = []
    for alg in algorithms:
        try:
            alg_id = alg.id()
            display = alg.displayName() if hasattr(alg, "displayName") else alg.name()
        except Exception as e:  # noqa: BLE001 - 个别算法读不出来就跳过，不影响其它候选
            logger.debug("跳过无法读取的算法: %s", e)
            continue
        if not alg_id:
            continue
        alg_id_l = alg_id.lower()
        score = difflib.SequenceMatcher(None, text, alg_id_l).ratio() if text else 0.0
        if tail and tail in alg_id_l:
            score += 0.5
        elif text and text in f"{alg_id_l} {str(display).lower()}":
            score += 0.25
        scored.append((score, alg_id, display))

    scored.sort(key=lambda item: item[0], reverse=True)
    suggestions = []
    for score, alg_id, display in scored:
        if len(suggestions) >= limit:
            break
        if score < 0.2:
            continue
        suggestions.append({
            "id": _sanitize_untrusted(alg_id, 120),
            "name": _sanitize_untrusted(display, 120),
        })
    return suggestions


def _truncate_algorithm_result(result: dict, max_chars: int = _MAX_FEATURE_RESULT_CHARS) -> dict:
    """算法参数过多时按返回值长度截断（复用 _truncate_result 的长度度量）"""
    result = _truncate_result(result, max_chars)
    parameters = result.get("parameters")
    if not isinstance(parameters, list):
        return result

    before = len(parameters)
    while parameters and _dump_len(result) > max_chars:
        parameters.pop()
    if _dump_len(result) > max_chars:
        result["parameters"] = []
    if len(parameters) < before:
        result["parameter_count"] = len(parameters)
        result["truncated"] = f"...(参数过多，已截断 {before - len(parameters)} 个参数)"
    return result


def get_algorithm_parameters(algorithm_id: str) -> dict:
    """查询 QGIS Processing 算法的真实参数定义。

    返回算法 id、名称、分组以及每个参数的名称、描述、类型、默认值、
    是否可选。在调用 execute_processing 之前必须先调用本工具查询算法
    的真实参数名，不要凭记忆猜测或编造参数；算法 id 不存在时会返回
    名字相近的候选算法 id，便于自我纠正。
    """
    try:
        registry = QgsApplication.processingRegistry()
        if registry is None:
            return {"error": "Processing 注册表未初始化，本工具需在 QGIS 桌面环境中运行。"}

        alg = registry.algorithmById(algorithm_id)
        if alg is None:
            suggestions = _suggest_algorithm_ids(algorithm_id)
            error = f"未找到算法: {algorithm_id}"
            if suggestions:
                error += "。是否想找: " + ", ".join(s["id"] for s in suggestions)
            return {
                "error": error,
                "suggestions": suggestions,
                "hint": "请从 suggestions 中选一个正确的算法 id 重新调用本工具，确认参数后再调用 execute_processing。",
            }

        parameters = []
        for param in (alg.parameterDefinitions() or []):
            try:
                parameters.append(_describe_processing_parameter(param))
            except Exception as e:
                logger.debug("解析算法参数失败: %s", e, exc_info=True)

        result = {
            "id": _sanitize_untrusted(alg.id(), 120),
            "name": _sanitize_untrusted(
                alg.displayName() if hasattr(alg, "displayName") else alg.name(), 200
            ),
            "group": _sanitize_untrusted(alg.group() if hasattr(alg, "group") else "", 120),
            "parameter_count": len(parameters),
            "parameters": parameters,
        }
        return _truncate_algorithm_result(result)
    except Exception as e:
        logger.debug("get_algorithm_parameters 失败: %s", e, exc_info=True)
        return {"error": f"查询算法参数失败: {str(e)}"}


# ──────────────────────────────────────────────
# 2. get_layer_profile
# ──────────────────────────────────────────────

def _geometry_type_name(layer) -> str:
    """矢量图层几何类型名称（兼容 QGIS 3.22~3.40 的枚举作用域差异）"""
    try:
        geom_type = QgsWkbTypes.geometryType(layer.wkbType())
    except Exception as e:
        logger.debug("QgsWkbTypes.geometryType 失败，回退 layer.geometryType(): %s", e, exc_info=True)
        geom_type = layer.geometryType()

    names = {}
    try:
        names = {
            QgsWkbTypes.GeometryType.PointGeometry: "Point",
            QgsWkbTypes.GeometryType.LineGeometry: "Line",
            QgsWkbTypes.GeometryType.PolygonGeometry: "Polygon",
            QgsWkbTypes.GeometryType.NullGeometry: "NoGeometry",
            QgsWkbTypes.GeometryType.UnknownGeometry: "Unknown",
        }
    except AttributeError as e:
        logger.debug("作用域几何枚举不可用，回退整数映射: %s", e, exc_info=True)
    if not names:
        names = {0: "Point", 1: "Line", 2: "Polygon", 3: "NoGeometry", 4: "Unknown"}
    return names.get(geom_type, str(geom_type))


def _build_layer_profile(layer) -> dict:
    """构造单个图层的精简档案"""
    profile = {
        "id": layer.id(),
        # 图层名来自数据源/工程文件，属不可信输入
        "name": _sanitize_untrusted(layer.name(), 120),
        "type": _get_layer_type(layer),
    }

    crs = layer.crs()
    profile["crs"] = {
        "authid": _sanitize_untrusted(crs.authid() if crs else "", 60),
        "description": _sanitize_untrusted(crs.description() if crs else "", 150),
    }

    try:
        ext = layer.extent()
        profile["extent"] = {
            "xmin": round(ext.xMinimum(), 6),
            "ymin": round(ext.yMinimum(), 6),
            "xmax": round(ext.xMaximum(), 6),
            "ymax": round(ext.yMaximum(), 6),
        }
    except Exception as e:
        logger.debug("读取图层范围失败: %s", e, exc_info=True)

    if layer.type() == QgsMapLayer.LayerType.VectorLayer:
        profile["geometry_type"] = _geometry_type_name(layer)
        try:
            profile["wkb_type"] = _sanitize_untrusted(QgsWkbTypes.displayString(layer.wkbType()), 60)
            profile["has_z"] = bool(QgsWkbTypes.hasZ(layer.wkbType()))
            profile["has_m"] = bool(QgsWkbTypes.hasM(layer.wkbType()))
        except Exception as e:
            logger.debug("读取 WKB 类型信息失败: %s", e, exc_info=True)

        feature_count = layer.featureCount()
        try:
            if feature_count is None or feature_count < 0:
                provider = layer.dataProvider()
                feature_count = provider.featureCount() if provider is not None else -1
        except Exception as e:
            logger.debug("读取要素数失败: %s", e, exc_info=True)
        profile["feature_count"] = feature_count

        fields = []
        for fld in layer.fields():
            try:
                fields.append({
                    # 字段名来自数据源，属不可信输入
                    "name": _sanitize_untrusted(fld.name(), 120),
                    "type": _sanitize_untrusted(fld.typeName(), 60),
                    "length": fld.length(),
                    "precision": fld.precision(),
                })
            except Exception as e:
                logger.debug("读取字段信息失败: %s", e, exc_info=True)
        profile["fields"] = fields[:50]
        if len(fields) > 50:
            profile["fields_truncated"] = f"...(共 {len(fields)} 个字段，仅返回前 50 个)"

        try:
            provider = layer.dataProvider()
            if provider is not None:
                profile["provider"] = _sanitize_untrusted(provider.name(), 60)
        except Exception as e:
            logger.debug("读取数据源 provider 失败: %s", e, exc_info=True)

    elif layer.type() == QgsMapLayer.LayerType.RasterLayer:
        for attr, key in (("bandCount", "band_count"), ("width", "width"), ("height", "height")):
            try:
                profile[key] = getattr(layer, attr)()
            except Exception as e:
                logger.debug("读取栅格属性 %s 失败: %s", attr, e, exc_info=True)
        try:
            profile["resolution"] = {
                "x": layer.rasterUnitsPerPixelX(),
                "y": layer.rasterUnitsPerPixelY(),
            }
        except Exception as e:
            logger.debug("读取栅格分辨率失败: %s", e, exc_info=True)
        try:
            provider = layer.dataProvider()
            if provider is not None:
                profile["data_type"] = _sanitize_untrusted(provider.dataType(1), 60)
        except Exception as e:
            logger.debug("读取栅格数据类型失败: %s", e, exc_info=True)

    return profile


def get_layer_profile(layer_id: str = None) -> dict:
    """获取图层的精简档案（几何类型、要素数、坐标系、范围、字段列表）。

    矢量图层返回几何类型、要素数、CRS(authid+描述)、Extent(bbox)、
    字段列表(名称/类型/长度)以及是否含 Z/M；栅格图层返回波段数、
    分辨率、宽高、数据类型。不传 layer_id 时返回当前工程所有图层的
    档案（最多 20 个）。在空间分析或拼装 Processing 参数之前先用本
    工具"看一眼数据"，可避免字段名不匹配与坐标系遗漏两类错误。
    """
    try:
        project = QgsProject.instance()

        if layer_id:
            layer = _find_layer(layer_id)
            if not layer:
                # v2.4.12：原先只回一句「未找到图层」，模型既不知道大小写不匹配，
                # 也看不到真实图层名 → 只能再摸一轮，用户看到连续失败。
                return _layer_not_found_error(layer_id)
            return _truncate_result({"layer": _build_layer_profile(layer)})

        layers = list(project.mapLayers().values())
        profiles = []
        for lyr in layers[:20]:
            try:
                profiles.append(_build_layer_profile(lyr))
            except Exception as e:
                logger.debug("构建图层档案失败: %s", e, exc_info=True)

        result = {
            "project_crs": _sanitize_untrusted(project.crs().authid(), 60),
            "layer_count": len(layers),
            "returned": len(profiles),
            "layers": profiles,
        }
        if len(layers) > 20:
            result["truncated"] = f"...(仅返回前 20 个图层，共 {len(layers)} 个)"
        return _truncate_result(result)
    except Exception as e:
        logger.debug("get_layer_profile 失败: %s", e, exc_info=True)
        return {"error": f"获取图层档案失败: {str(e)}"}


# ──────────────────────────────────────────────
# 3. set_layer_renderer
# ──────────────────────────────────────────────

# 色带取不到时的兜底调色板
_FALLBACK_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _get_color_ramp(color_ramp: str):
    """按名称取色带，取不到依次回退 Viridis 与内置渐变，返回 (ramp, 名称)"""
    try:
        from qgis.core import QgsStyle, QgsGradientColorRamp

        style = QgsStyle.defaultStyle()
        if style is not None:
            for name in (color_ramp, "Viridis"):
                if not name:
                    continue
                try:
                    ramp = style.colorRamp(str(name))
                except Exception as e:
                    logger.debug("读取色带 %s 失败: %s", name, e, exc_info=True)
                    ramp = None
                if ramp is not None:
                    return ramp, str(name)
        return QgsGradientColorRamp(QColor("#f7fbff"), QColor("#08306b")), "fallback_gradient"
    except Exception as e:
        logger.debug("构造色带失败，使用内置调色板: %s", e, exc_info=True)
        return None, None


def _pick_color(ramp, index: int, total: int):
    """从色带取第 index 个颜色，色带不可用时回退内置调色板"""
    ratio = 0.5 if total <= 1 else index / float(total - 1)
    if ramp is not None:
        try:
            return ramp.color(ratio)
        except Exception as e:
            logger.debug("色带取色失败: %s", e, exc_info=True)
    return QColor(_FALLBACK_PALETTE[index % len(_FALLBACK_PALETTE)])


def _default_symbol(layer):
    """按图层几何类型构造默认符号（兼容 defaultSymbol 参数类型差异）"""
    try:
        from qgis.core import QgsSymbol, QgsMarkerSymbol, QgsLineSymbol, QgsFillSymbol
    except Exception as e:
        logger.debug("渲染符号模块导入失败: %s", e, exc_info=True)
        return None

    geom_type = layer.geometryType()
    symbol = None
    try:
        symbol = QgsSymbol.defaultSymbol(geom_type)
    except TypeError:
        try:
            symbol = QgsSymbol.defaultSymbol(int(geom_type))
        except Exception as e:
            logger.debug("defaultSymbol(int) 失败: %s", e, exc_info=True)
    except Exception as e:
        logger.debug("defaultSymbol 失败: %s", e, exc_info=True)

    if symbol is None:
        try:
            factories = {0: QgsMarkerSymbol, 1: QgsLineSymbol, 2: QgsFillSymbol}
            factory = factories.get(int(geom_type), QgsMarkerSymbol)
            symbol = factory.createSimple({})
        except Exception as e:
            logger.debug("createSimple 兜底失败: %s", e, exc_info=True)
    return symbol


def _collect_unique_values(layer, field: str, limit: int = 100) -> list:
    """取字段唯一值（优先 uniqueValues，失败时遍历要素）"""
    values = []
    fields = layer.fields()
    index = -1
    for getter in ("lookupField", "indexOf"):
        method = getattr(fields, getter, None)
        if callable(method):
            try:
                index = int(method(field))
                break
            except Exception as e:
                logger.debug("%s 取字段索引失败: %s", getter, e, exc_info=True)
    if index >= 0:
        try:
            values = list(layer.uniqueValues(index))
        except Exception as e:
            logger.debug("uniqueValues(int) 失败: %s", e, exc_info=True)
            values = []
    if not values:
        try:
            values = list(layer.uniqueValues(field))
        except Exception as e:
            logger.debug("uniqueValues(str) 失败: %s", e, exc_info=True)
            values = []
    if not values:
        seen = set()
        try:
            for feat in layer.getFeatures():
                value = feat.attribute(field)
                try:
                    key = value
                    hash(key)
                except TypeError:
                    key = str(value)
                    value = key
                if key not in seen:
                    seen.add(key)
                    values.append(value)
                if len(values) >= limit:
                    break
        except Exception as e:
            logger.debug("遍历要素取唯一值失败: %s", e, exc_info=True)

    try:
        values = sorted(values, key=lambda v: (v is None, str(v)))
    except Exception as e:
        logger.debug("唯一值排序失败: %s", e, exc_info=True)
    return values[:limit]


def _build_single_renderer(layer, ramp):
    """单一符号渲染器"""
    from qgis.core import QgsSingleSymbolRenderer

    symbol = _default_symbol(layer)
    if symbol is None:
        return None
    try:
        symbol.setColor(_pick_color(ramp, 0, 1))
    except Exception as e:
        logger.debug("设置符号颜色失败: %s", e, exc_info=True)
    return QgsSingleSymbolRenderer(symbol)


def _build_categorized_renderer(layer, field: str, ramp, limit: int = 100):
    """分类渲染器（按字段唯一值），返回 (renderer, 值列表)"""
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory

    values = _collect_unique_values(layer, field, limit)
    symbol = _default_symbol(layer)
    if symbol is None:
        return None, values

    renderer = QgsCategorizedSymbolRenderer(field, [])
    total = len(values) or 1
    for i, value in enumerate(values):
        try:
            sym = symbol.clone()
            sym.setColor(_pick_color(ramp, i, total))
            label = "(空值)" if value is None else _sanitize_untrusted(value, 80)
            renderer.addCategory(QgsRendererCategory(value, sym, str(label)))
        except Exception as e:
            logger.debug("构造分类类别失败: %s", e, exc_info=True)
    return renderer, values


def _get_classification_method(mode: str):
    """按名称取分级方法（QGIS 3.10+ 分类方法注册表），返回 (method, 规范名)"""
    alias = {
        "equalinterval": "EqualInterval", "equal": "EqualInterval", "等间距": "EqualInterval",
        "quantile": "Quantile", "equalcount": "Quantile", "分位数": "Quantile",
        "jenks": "Jenks", "naturalbreaks": "Jenks", "natural breaks": "Jenks", "自然间断": "Jenks",
        "stddev": "StdDev", "standarddeviation": "StdDev", "标准差": "StdDev",
        "pretty": "Pretty",
    }
    raw = str(mode or "EqualInterval").strip()
    key = raw.lower().replace(" ", "").replace("-", "").replace("_", "")
    name = alias.get(key, raw)

    try:
        if int(Qgis.QGIS_VERSION_INT) >= 31000:
            registry = QgsApplication.classificationMethodRegistry()
            if registry is not None and hasattr(registry, "method"):
                found = registry.method(name) or registry.method("EqualInterval")
                if found is not None:
                    method = found.clone() if hasattr(found, "clone") else found
                    return method, name
    except Exception as e:
        logger.debug("分级方法注册表不可用: %s", e, exc_info=True)
    return None, name


def _graduated_mode_enum(mode: str):
    """旧版 QGIS 的 QgsGraduatedSymbolRenderer.Mode 枚举值（新版无需）"""
    try:
        from qgis.core import QgsGraduatedSymbolRenderer as _R
    except Exception as e:
        logger.debug("QgsGraduatedSymbolRenderer 导入失败: %s", e, exc_info=True)
        return None

    mapping = {
        "EqualInterval": "EqualInterval",
        "Quantile": "Quantile",
        "Jenks": "Jenks",
        "StdDev": "StdDev",
        "Pretty": "Pretty",
    }
    return _enum_value(_R, "Mode", mapping.get(mode, "EqualInterval"))


def _build_graduated_renderer(layer, field: str, ramp, classes: int, mode: str):
    """分级渲染器（按字段数值区间），返回 (renderer, 分级区间列表)"""
    from qgis.core import QgsGraduatedSymbolRenderer

    method, method_name = _get_classification_method(mode)
    mode_enum = _graduated_mode_enum(method_name)
    symbol = _default_symbol(layer)
    if symbol is None:
        return None, []

    renderer = QgsGraduatedSymbolRenderer()
    try:
        renderer.setClassAttribute(field)
    except Exception as e:
        logger.debug("setClassAttribute 失败: %s", e, exc_info=True)
    try:
        if hasattr(renderer, "setSourceSymbol"):
            renderer.setSourceSymbol(symbol.clone())
    except Exception as e:
        logger.debug("setSourceSymbol 失败: %s", e, exc_info=True)

    method_used = "default"
    if method is not None and hasattr(renderer, "setClassificationMethod"):
        try:
            renderer.setClassificationMethod(method)
            method_used = method_name
        except Exception as e:
            logger.debug("setClassificationMethod 失败: %s", e, exc_info=True)
    if method_used == "default" and mode_enum is not None and hasattr(renderer, "setMode"):
        try:
            renderer.setMode(mode_enum)
            method_used = method_name
        except Exception as e:
            logger.debug("setMode 失败: %s", e, exc_info=True)

    try:
        try:
            # QGIS 3.10+ 使用已设置的分类方法
            renderer.updateClasses(layer, classes)
        except TypeError:
            # 旧版签名需要显式传入 Mode 枚举
            renderer.updateClasses(layer, mode_enum, classes)
    except Exception as e:
        logger.debug("updateClasses 失败: %s", e, exc_info=True)
        return None, []

    if ramp is not None:
        try:
            if hasattr(renderer, "updateColorRamp"):
                renderer.updateColorRamp(ramp)
            else:
                renderer.setColorRamp(ramp)
        except Exception as e:
            logger.debug("应用色带失败: %s", e, exc_info=True)

    ranges = []
    try:
        for rng in renderer.ranges():
            ranges.append({
                "lower": rng.lowerValue(),
                "upper": rng.upperValue(),
                "label": _sanitize_untrusted(rng.label(), 80),
            })
    except Exception as e:
        logger.debug("读取分级区间失败: %s", e, exc_info=True)
    return renderer, ranges


def set_layer_renderer(
    layer_id: str,
    renderer_type: str = "single",
    field: str = None,
    color_ramp: str = "Viridis",
    classes: int = 5,
    mode: str = "EqualInterval",
) -> dict:
    """设置矢量图层的渲染样式（符号化）。

    支持三种渲染类型：single=单一符号；categorized=按字段唯一值分类
    设色（适合类型、名称等离散字段）；graduated=按字段数值区间分级
    设色（适合高度、面积、人口等连续数值字段，例如"对建筑图层按高度
    字段分级设色"）。设置后会立即重绘图层并刷新图例与画布。

    Args:
        layer_id: 图层名称或ID
        renderer_type: single / categorized / graduated
        field: 分类或分级所依据的字段名（categorized/graduated 必填）
        color_ramp: 色带名称，如 Viridis / RdYlGn / Spectral / Blues
        classes: graduated 的分级数量
        mode: graduated 的分级方式: EqualInterval / Quantile / Jenks / StdDev
    """
    rtype = str(renderer_type or "single").strip().lower()
    if rtype not in ("single", "categorized", "graduated"):
        return {"error": f"不支持的渲染类型: {renderer_type}（可选 single / categorized / graduated）"}
    if rtype != "single" and not field:
        return {"error": "categorized / graduated 渲染必须通过 field 指定字段名"}

    try:
        # 提前探测渲染 API 是否可用（各 QGIS 版本类名一致，但导入失败需明确报错）
        from qgis.core import (
            QgsCategorizedSymbolRenderer,  # noqa: F401
            QgsGraduatedSymbolRenderer,  # noqa: F401
            QgsSingleSymbolRenderer,  # noqa: F401
        )
    except Exception as e:
        logger.debug("渲染模块导入失败: %s", e, exc_info=True)
        return {"error": f"渲染模块导入失败: {str(e)}"}

    layer = _find_layer(layer_id)
    if not layer:
        return _layer_not_found_error(layer_id)
    if layer.type() != QgsMapLayer.LayerType.VectorLayer:
        return {"error": f"图层 {layer.name()} 不是矢量图层，无法设置渲染"}

    if rtype != "single":
        real_field = _find_field_name(layer, field)
        field_names = [f.name() for f in layer.fields()]
        if real_field is None:
            return {
                "error": f"字段 '{field}' 不存在。可用字段: {[_sanitize_untrusted(n, 120) for n in field_names]}",
                "hint": "字段名区分大小写，本工具已按忽略大小写查找仍未匹配；"
                        "可先调用 get_layer_profile 查看真实字段名。",
            }
        field = real_field

    try:
        classes = max(1, min(int(classes or 5), 100))
    except Exception as e:
        logger.debug("分级数量非法，回退 5: %s", e, exc_info=True)
        classes = 5

    ramp, ramp_name = _get_color_ramp(color_ramp)
    detail = {}
    try:
        if rtype == "single":
            renderer = _build_single_renderer(layer, ramp)
        elif rtype == "categorized":
            renderer, values = _build_categorized_renderer(layer, field, ramp)
            detail = {
                "categories": len(values),
                "values": [_sanitize_untrusted(v, 80) for v in values[:20]],
            }
        else:
            renderer, ranges = _build_graduated_renderer(layer, field, ramp, classes, mode)
            detail = {"classes": len(ranges), "ranges": ranges}
    except Exception as e:
        logger.debug("构造渲染器失败: %s", e, exc_info=True)
        return {"error": f"构造渲染器失败: {str(e)}"}

    if renderer is None:
        return {"error": f"构造 {rtype} 渲染器失败，请确认图层几何类型与字段类型是否匹配"}

    try:
        layer.setRenderer(renderer)
    except Exception as e:
        logger.debug("setRenderer 失败: %s", e, exc_info=True)
        return {"error": f"应用渲染器失败: {str(e)}"}

    layer.triggerRepaint()

    # 刷新图例符号与画布（无 GUI 场景下 iface 可能为 None）
    try:
        if iface is not None:
            if hasattr(iface, "layerTreeView") and iface.layerTreeView() is not None:
                iface.layerTreeView().refreshLayerSymbology(layer.id())
            if iface.mapCanvas() is not None:
                iface.mapCanvas().refresh()
    except Exception as e:
        logger.debug("刷新图层符号失败: %s", e, exc_info=True)

    result = {
        "layer": _sanitize_untrusted(layer.name(), 120),
        "layer_id": layer.id(),
        "renderer_type": rtype,
        "color_ramp": ramp_name,
        "message": f"已为图层 '{layer.name()}' 设置 {rtype} 渲染",
    }
    if rtype != "single":
        result["field"] = field
    if rtype == "graduated":
        result["mode"] = mode
    result.update(detail)
    return result


# ──────────────────────────────────────────────
# 4. reproject_layer
# ──────────────────────────────────────────────

# 解析失败时回给 LLM 的常见坐标系提示
_COMMON_CRS_HINTS = [
    "EPSG:4326（WGS 84 经纬度）",
    "EPSG:3857（Web Mercator，网络底图常用）",
    "EPSG:4490（CGCS2000 经纬度）",
    "EPSG:32650（WGS 84 / UTM 50N）",
]

_CRS_ALIASES = {
    "wgs84": "EPSG:4326", "wgs 84": "EPSG:4326",
    "cgcs2000": "EPSG:4490", "cgcs 2000": "EPSG:4490",
    "webmercator": "EPSG:3857", "web mercator": "EPSG:3857", "pseudo mercator": "EPSG:3857",
    "utm50n": "EPSG:32650",
}


def _parse_crs(target_crs: str):
    """解析目标坐标系，返回 (crs, 错误信息)；成功时错误信息为 None"""
    raw = str(target_crs or "").strip()
    if not raw:
        return None, "target_crs 不能为空。常见取值: " + "; ".join(_COMMON_CRS_HINTS)

    candidates = [raw]
    alias = _CRS_ALIASES.get(raw.lower())
    if alias:
        candidates.insert(0, alias)
    if raw.isdigit():
        candidates.append(f"EPSG:{raw}")

    for candidate in candidates:
        # 新版 QGIS：createFromUserInput 支持 "EPSG:4326"/"4326"/"WGS 84"/proj 串
        if hasattr(QgsCoordinateReferenceSystem, "createFromUserInput"):
            try:
                crs = QgsCoordinateReferenceSystem.createFromUserInput(candidate)
                if crs is not None and crs.isValid():
                    return crs, None
            except Exception as e:
                logger.debug("createFromUserInput(%s) 失败: %s", candidate, e, exc_info=True)
        try:
            crs = QgsCoordinateReferenceSystem(candidate)
            if crs.isValid():
                return crs, None
        except Exception as e:
            logger.debug("QgsCoordinateReferenceSystem(%s) 失败: %s", candidate, e, exc_info=True)

    return None, f"无法解析坐标系: {raw}。常见取值: " + "; ".join(_COMMON_CRS_HINTS)


def _driver_for_path(path: str) -> str:
    """按输出文件扩展名推断 OGR 驱动名"""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".shp": "ESRI Shapefile",
        ".gpkg": "GPKG",
        ".geojson": "GeoJSON",
        ".json": "GeoJSON",
        ".kml": "KML",
        ".gml": "GML",
    }.get(ext, "GPKG")


def _default_reproject_path(layer, crs) -> str:
    """未指定输出路径时，在工程目录（或临时目录）生成 原名_坐标系.gpkg"""
    base = os.path.splitext(os.path.basename(layer.name()))[0] or "layer"
    base = re.sub(r'[\\/:*?"<>|]+', "_", base).strip()[:80] or "layer"
    authid = (crs.authid() or "custom").replace(":", "_")
    project_file = QgsProject.instance().fileName()
    out_dir = os.path.dirname(project_file) if project_file else tempfile.gettempdir()
    return os.path.join(out_dir, f"{base}_{authid}.gpkg")


def _parse_writer_error(ret):
    """统一解析 QgsVectorFileWriter 返回值 → (错误码 int, 错误信息 str)"""
    message = ""
    if isinstance(ret, (tuple, list)):
        err = ret[0]
        if len(ret) > 1:
            message = str(ret[1] or "")
    else:
        err = ret
    try:
        code = int(err)
    except Exception:
        code = -1
    return code, message


def _write_vector_with_crs(layer, path: str, crs, driver_name: str = "GPKG") -> str:
    """手写降级写出（坐标转换 + 写文件），返回空串表示成功，否则返回错误信息"""
    try:
        from qgis.core import QgsVectorFileWriter, QgsCoordinateTransform
    except Exception as e:
        return f"写出模块导入失败: {str(e)}"

    transform = None
    try:
        transform = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance())
    except Exception as e:
        logger.debug("构造坐标转换失败: %s", e, exc_info=True)

    # 新版 API（QGIS 3.2+）：SaveVectorOptions
    try:
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver_name
        options.fileEncoding = "UTF-8"
        if transform is not None:
            options.ct = transform
        code, message = _parse_writer_error(QgsVectorFileWriter.writeAsVectorFormat(layer, path, options))
        # WriterError.NoError 在所有版本中均为 0
        if code == 0:
            return ""
        logger.debug("SaveVectorOptions 写出失败: code=%s msg=%s", code, message)
    except Exception as e:
        logger.debug("SaveVectorOptions 写出异常: %s", e, exc_info=True)

    # 旧版 API：位置参数签名
    try:
        code, message = _parse_writer_error(
            QgsVectorFileWriter.writeAsVectorFormat(layer, path, "UTF-8", crs, driver_name)
        )
        if code == 0:
            return ""
        return f"写出失败: {message}"
    except Exception as e:
        return f"写出失败: {str(e)}"


def reproject_layer(
    layer_id: str,
    target_crs: str,
    output_path: str = None,
    add_to_project: bool = True,
) -> dict:
    """将矢量图层重投影（坐标转换）到目标坐标系并输出为新文件。

    target_crs 接受 "EPSG:4326"、"4326"、"WGS 84"、"CGCS2000" 等
    多种写法。需要把图层在经纬度与投影坐标系之间转换时使用本工具，
    不要手工拼装 native:reprojectlayer 的 TARGET_CRS 参数。内部优先
    走 Processing 算法，失败时降级为手写写出。

    Args:
        layer_id: 图层名称或ID
        target_crs: 目标坐标系
        output_path: 输出文件路径(.gpkg/.shp)，不指定则自动生成
        add_to_project: 是否将结果图层加入当前工程
    """
    layer = _find_layer(layer_id)
    if not layer:
        return _layer_not_found_error(layer_id)
    if layer.type() != QgsMapLayer.LayerType.VectorLayer:
        return {
            "error": f"图层 {layer.name()} 不是矢量图层，暂不支持重投影",
            "hint": "栅格重投影请调用 execute_processing 使用 gdal:warpreproject。",
        }

    crs, crs_error = _parse_crs(target_crs)
    if crs is None:
        return {"error": crs_error}

    if output_path:
        out_path = output_path
        try:
            if os.path.exists(out_path) and not _skip_all_confirms:
                preview = json.dumps(
                    {
                        "layer": _sanitize_untrusted(layer.name(), 120),
                        "output_path": _sanitize_untrusted(out_path, 300),
                        "target_crs": _sanitize_untrusted(crs.authid(), 60),
                    },
                    ensure_ascii=False, indent=2,
                )
                if not _request_confirmation("reproject_layer", f"将覆盖已存在的文件：\n{preview}"):
                    return {"error": "用户取消了 reproject_layer 操作。"}
        except Exception as e:
            logger.debug("reproject_layer 覆盖确认检查失败: %s", e, exc_info=True)
            return {"error": "确认通道未就绪，已拒绝覆盖已存在的文件。"}
    else:
        out_path = _default_reproject_path(layer, crs)

    # 禁止输出路径指向源图层数据源，避免原始数据被就地改写
    try:
        if os.path.exists(layer.source()) and os.path.abspath(out_path) == os.path.abspath(layer.source()):
            return {
                "error": "输出路径与源图层数据源相同，已拒绝执行以避免覆盖原始数据: "
                         + _sanitize_untrusted(out_path, 300)
            }
    except Exception as e:
        logger.debug("源路径比对失败，跳过: %s", e, exc_info=True)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            logger.debug("创建输出目录失败: %s", e, exc_info=True)
            return {"error": f"无法创建输出目录: {_sanitize_untrusted(out_dir, 300)}"}

    method_used = None
    try:
        import processing
        target_value = crs.authid() or crs.toWkt()
        try:
            processing.run(
                "native:reprojectlayer",
                {"INPUT": layer, "TARGET_CRS": target_value, "OUTPUT": out_path},
            )
        except Exception as first_error:
            # 部分版本不接受 QgsVectorLayer 作为 INPUT，退化为数据源路径
            logger.debug("processing INPUT 传图层失败，改用数据源: %s", first_error, exc_info=True)
            processing.run(
                "native:reprojectlayer",
                {"INPUT": layer.source(), "TARGET_CRS": target_value, "OUTPUT": out_path},
            )
        method_used = "native:reprojectlayer"
    except Exception as e:
        logger.debug("native:reprojectlayer 失败，降级手写写出: %s", e, exc_info=True)

    if method_used is None:
        write_error = _write_vector_with_crs(layer, out_path, crs, _driver_for_path(out_path))
        if write_error:
            return {"error": f"重投影失败: {write_error}"}
        method_used = "QgsVectorFileWriter"

    if not os.path.exists(out_path):
        return {"error": f"重投影执行完成但未找到输出文件: {_sanitize_untrusted(out_path, 300)}"}

    out_name = f"{layer.name()}_{crs.authid() or 'reprojected'}"
    out_layer = QgsVectorLayer(out_path, out_name, "ogr")
    if not out_layer.isValid():
        return {"error": f"输出文件无法作为矢量图层加载: {_sanitize_untrusted(out_path, 300)}"}

    added = False
    if add_to_project:
        try:
            canvas = iface.mapCanvas() if iface is not None else None
            if canvas is not None:
                canvas.setRenderFlag(False)
            try:
                QgsProject.instance().addMapLayer(out_layer)
                added = True
            finally:
                if canvas is not None:
                    canvas.setRenderFlag(True)
                    from qgis.PyQt.QtCore import QTimer
                    QTimer.singleShot(50, canvas.refresh)
        except Exception as e:
            logger.debug("重投影结果加入工程失败: %s", e, exc_info=True)

    return {
        "source_layer": _sanitize_untrusted(layer.name(), 120),
        "source_crs": _sanitize_untrusted(layer.crs().authid(), 60),
        "target_crs": _sanitize_untrusted(crs.authid(), 60),
        "output_path": _sanitize_untrusted(out_path, 300),
        "output_layer": _sanitize_untrusted(out_layer.name(), 120),
        "feature_count": out_layer.featureCount(),
        "method": method_used,
        "added_to_project": added,
        "message": f"已将图层 '{layer.name()}' 重投影到 {crs.authid() or target_crs}",
    }


# ──────────────────────────────────────────────
# 技能系统（skills/）接线
# ──────────────────────────────────────────────

_skill_manager = None  # 延迟初始化的全局 SkillManager 单例


def run_skill(skill_name: str, **params) -> str:
    """运行一个已注册的技能（如 web_search、gis_data_search、format_results）。

    内部通过 skills 包的 SkillManager 查找并执行该技能，返回结果字符串。
    该工具本身也注册进 TOOL_DEFINITIONS，使 LLM 可以直接调用技能。
    """
    global _skill_manager
    try:
        from .skills.skill_manager import get_skill_manager
        from .skills.builtins import register_builtin_skills
    except Exception as _e:
        logger.debug("导入 skills 模块失败: %s", _e, exc_info=True)
        return f"技能系统不可用（模块导入失败）: {_e}"

    try:
        if _skill_manager is None:
            _skill_manager = get_skill_manager()
        # 确保内置技能已注册（只注册一次）
        if not _skill_manager.get_all():
            try:
                register_builtin_skills(_skill_manager)
            except Exception as _e:
                logger.debug("注册内置技能失败（可忽略）: %s", _e, exc_info=True)

        result = _skill_manager.execute(skill_name, **params)
        return _format_skill_result(result)
    except Exception as _e:
        logger.debug("run_skill 执行异常: %s", _e, exc_info=True)
        return f"执行技能 '{skill_name}' 失败: {_e}"


def _format_skill_result(result) -> str:
    """把 SkillResult 规整为字符串（供 LLM / 用户阅读）"""
    if result is None:
        return "(技能无返回结果)"
    if hasattr(result, "success"):
        if result.success:
            out = result.output
            if out is None:
                return "(技能执行成功，无输出)"
            if isinstance(out, (dict, list)):
                try:
                    return json.dumps(out, ensure_ascii=False, indent=2)
                except Exception:
                    return str(out)
            return str(out)
        return f"(技能执行失败: {result.error})"
    return str(result)


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
        "name": "export_table_to_csv",
        "description": "【导出表格为 CSV】把统计结果或图层属性表保存成 CSV 文件（Windows Excel 可直接打开，中文不乱码）。这是「把结果存成 CSV / Excel / 表格文件」「导出属性表」「保存到桌面」这类需求的正确工具——不要用 execute_pyqgis 写 import csv / open()，受限环境会直接拒绝（禁止导入 csv 模块、禁止调用 open），只会白白失败。两种用法：① rows：直接传算好的表格，如 [{\"社区\":\"A\",\"面积km²\":1.23}, ...]，或 [ [\"社区\",\"面积km²\"], [\"A\",1.23] ]（首行全为字符串时当表头）；② layer：传图层名导出属性表，可用 columns 指定字段、only_selected 只导选中要素。output_path 留空则自动存到桌面；相对路径也会落到桌面。",
        "parameters": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "description": "要导出的表格数据：对象数组（推荐，键即表头）或二维数组（首行全为字符串时当表头）",
                },
                "layer": {"type": "string", "description": "要导出属性表的图层名称或 ID（名称忽略大小写）"},
                "columns": {
                    "type": "array",
                    "description": "指定列/字段及其顺序：rows 模式为对象键名，layer 模式为字段名（同样忽略大小写）",
                },
                "output_path": {
                    "type": "string",
                    "description": "输出 CSV 的路径，留空则存到桌面；相对路径按桌面处理；必须是 .csv",
                },
                "limit": {"type": "integer", "description": "最多导出多少行，默认 0 表示全部"},
                "only_selected": {"type": "boolean", "description": "layer 模式下是否只导出选中要素，默认 false"},
            },
            "required": [],
        },
    },
    {
        "name": "compute_area_stats",
        "description": "【面积统计】按正确的投影 / 中央经线统计矢量**面**图层的面积，并可按字段分级汇总。这是「统计某图层的面积」「按社区 / 街道乡镇 / 区县 / 地市州汇总面积」「算面积」这类需求的唯一正确入口——**不要**用 execute_pyqgis 手写 PyQGIS：手写要么直接拿地理坐标系把面积算成平方度，要么用 QgsCoordinateReferenceSystem(\"+proj=...\") 造出无效 CRS 却不报错，最后得到一张全是 0 的表。两种口径同时给出便于互校：① zone=逐要素按**质心经度**取 3 度带（中央经线 CM=3*round(lon/3)，CGCS2000，官方 EPSG 4542/4543/4544…）的投影平面面积；② ellipsoid=QgsDistanceArea + GRS80 椭球的**大地测量**面积（与投影无关，可作基准）。group_by 传字段名或字段名列表即可逐级汇总；output_dir 传目录则把每级汇总写成 UTF-8 带 BOM 的 CSV。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id_or_name": {"type": "string", "description": "要统计的矢量面图层名称或 ID（名称忽略大小写）"},
                "group_by": {
                    "type": "array",
                    "description": "按哪些字段分级汇总（字段名，忽略大小写）。例如 [\"NAME\",\"STR_NAME\",\"COU_NAME\",\"CITY_NAME\"] 一次出社区 / 街道乡镇 / 区县 / 地市州四级；留空则只回整体统计",
                },
                "carry_fields": {
                    "type": "array",
                    "description": "附加到每行的字段（取该组第一条要素的值），便于带上编码 / 上级名，如 [\"CODE\"]",
                },
                "output_dir": {"type": "string", "description": "落 CSV 的目录（如桌面路径）。留空则不落盘"},
                "method": {"type": "string", "description": "zone（3 度带投影）/ ellipsoid（椭球）/ both（默认，两者都给）"},
                "limit": {"type": "integer", "description": "每级最多回吐多少组，默认 0（用内置上限）；CSV 落盘不受此限"},
            },
            "required": ["layer_id_or_name"],
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
    {
        "name": "export_features_maps",
        "description": "【逐要素批量出图】把某个矢量图层的每个要素逐个出成一张 PNG 图片，可自动配置指北针与比例尺，支持标准图纸尺寸（A0-A4 / B0-B4）。这是「对图层要素逐个出图 / 批量出图 / 按 B0 号图出图 / 制图输出」类需求的正确工具——不要用 execute_pyqgis 手写打印布局代码（QgsLayoutItemNorthArrow 等旧 API 在新版 QGIS 已被移除，手写必然失败）。工具内部会：建临时打印布局 → 地图项铺满页面 → 逐要素把地图范围缩放到该要素（自动外扩留边、按页面纵横比校正、白底）→ 比例尺按每个要素的比例尺重算 → 导出 PNG。一次调用即完成全部要素，输出目录不存在会自动创建。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "图层名称或 ID（必须是矢量图层）"},
                "output_dir": {"type": "string", "description": "输出目录的绝对路径，不存在会自动创建"},
                "name_field": {"type": "string", "description": "用于给图片命名的字段名（如“支局名称”）；留空或该字段为空时用要素 ID"},
                "width_px": {"type": "integer", "description": "输出图片宽度像素，默认 1600"},
                "height_px": {"type": "integer", "description": "输出图片高度像素，默认 1200"},
                "dpi": {"type": "integer", "description": "输出分辨率，默认 150（决定页面物理尺寸）"},
                "margin_percent": {"type": "number", "description": "要素范围外扩百分比，避免要素贴边，默认 10"},
                "north_arrow": {"type": "boolean", "description": "是否添加指北针，默认 true"},
                "scale_bar": {"type": "boolean", "description": "是否添加比例尺，默认 true"},
                "limit": {"type": "integer", "description": "最多导出多少个要素，默认 100（防一次导出上千张卡住主线程）"},
                "page_size": {"type": "string", "description": "标准图纸尺寸（可选）：A4/A3/A2/A1/A0/B4/B3/B2/B1/B0。用户说「按 B0 号图出图」时传 'B0'。给了它就忽略 width_px/height_px，像素尺寸由 dpi 自动换算"},
                "orientation": {"type": "string", "description": "图纸方向：portrait（纵向，默认）或 landscape（横向）"},
            },
            "required": ["layer", "output_dir"],
        },
    },
    {
        "name": "get_algorithm_parameters",
        "description": "查询 QGIS Processing 算法的真实参数定义（参数名、描述、类型、默认值、是否可选）。在调用 execute_processing 之前必须先调用本工具查询算法的真实参数名，不要凭记忆猜测参数；算法 id 不存在时会返回名字相近的候选算法 id，便于自我纠正。",
        "parameters": {
            "type": "object",
            "properties": {
                "algorithm_id": {"type": "string", "description": "算法ID，如 native:buffer、native:clip、qgis:dissolve、gdal:contour"},
            },
            "required": ["algorithm_id"],
        },
    },
    {
        "name": "get_layer_profile",
        "description": "获取图层的精简档案：几何类型、要素数、坐标系(CRS authid+描述)、空间范围(Extent bbox)、字段列表(名称/类型/长度)、是否含Z/M；栅格图层则返回波段数、分辨率、宽高、数据类型。不传 layer_id 时返回当前工程所有图层的档案（最多20个）。在空间分析或拼装 Processing 参数之前先用本工具“看一眼数据”，可避免字段名不匹配、坐标系遗漏两类错误。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层名称或ID；不传则返回当前工程所有图层的档案"},
            },
            "required": [],
        },
    },
    {
        "name": "set_layer_renderer",
        "description": "设置矢量图层的渲染样式（符号化）。三种类型：single=单一符号；categorized=按字段唯一值分类设色（适合类型、名称、用地性质等离散字段）；graduated=按字段数值区间分级设色（适合高度、面积、人口等连续数值字段，如“对建筑图层按高度字段分级设色”）。设置后立即重绘图层并刷新图例与画布。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层名称或ID"},
                "renderer_type": {"type": "string", "description": "渲染类型: single(单一符号) / categorized(分类) / graduated(分级)，默认 single"},
                "field": {"type": "string", "description": "分类或分级所依据的字段名，categorized 与 graduated 必填"},
                "color_ramp": {"type": "string", "description": "色带名称，如 Viridis / RdYlGn / Spectral / Blues，默认 Viridis"},
                "classes": {"type": "integer", "description": "分级数量(graduated)，默认 5"},
                "mode": {"type": "string", "description": "分级方式(graduated): EqualInterval(等间距) / Quantile(分位数) / Jenks(自然间断) / StdDev(标准差)，默认 EqualInterval"},
            },
            "required": ["layer_id", "renderer_type"],
        },
    },
    {
        "name": "reproject_layer",
        "description": "将矢量图层重投影（坐标转换）到目标坐标系并输出为新文件，可选择加入当前工程。target_crs 支持 'EPSG:4326'、'4326'、'WGS 84'、'CGCS2000' 等多种写法。需要在经纬度与投影坐标系之间转换图层时使用本工具，不要手工拼装 native:reprojectlayer 的 TARGET_CRS 参数。",
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层名称或ID"},
                "target_crs": {"type": "string", "description": "目标坐标系，如 EPSG:4326 / 3857 / 4490 / 'WGS 84' / 'CGCS2000'"},
                "output_path": {"type": "string", "description": "输出文件路径(.gpkg/.shp)，不指定则自动生成到工程目录(或临时目录)，命名为 原名_目标坐标系.gpkg"},
                "add_to_project": {"type": "boolean", "description": "是否将结果图层加入当前工程，默认 true"},
            },
            "required": ["layer_id", "target_crs"],
        },
    },
    {
        "name": "run_skill",
        "description": "运行一个已注册的技能（skill）。可用于联网搜索、GIS 数据源检索、结果格式化等扩展能力。可用技能示例：web_search(网络搜索, 参数 query/num_results/engine)、gis_data_search(GIS 数据源检索, 参数 query/data_type)、format_results(格式化搜索结果, 参数 results/format)。技能由 skills 系统管理，调用前无需关心其内部实现。",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "要运行的技能名称，如 web_search / gis_data_search / format_results"},
                "query": {"type": "string", "description": "搜索类技能的查询词（web_search / gis_data_search 使用）"},
                "num_results": {"type": "integer", "description": "返回结果数量（web_search 使用，默认 5）"},
                "engine": {"type": "string", "description": "搜索引擎：duckduckgo / google / bing（web_search 使用，默认 duckduckgo）"},
                "data_type": {"type": "string", "description": "数据类型：vector / raster / all（gis_data_search 使用，默认 all）"},
                "results": {"type": "array", "description": "待格式化的结果列表（format_results 使用）"},
                "format": {"type": "string", "description": "输出格式：markdown / html / json（format_results 使用，默认 markdown）"},
            },
            "required": ["skill_name"],
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
    "export_table_to_csv": export_table_to_csv,
    "compute_area_stats": compute_area_stats,
    "export_features_maps": export_features_maps,
    "get_algorithm_parameters": get_algorithm_parameters,
    "get_layer_profile": get_layer_profile,
    "set_layer_renderer": set_layer_renderer,
    "reproject_layer": reproject_layer,
    "run_skill": run_skill,
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
        # 用户需阅读长代码 + 可能的 LLM 审查，默认等待 5 分钟
        timeout_sec = 300
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
    # 长耗时算法（大栅格/渲染/加载大工程）可能超过 30s，默认放宽到 180s
    mutex.lock()
    timeout_sec = 180
    if not result_holder["done"]:
        wait_cond.wait(mutex, timeout_sec * 1000)
    mutex.unlock()

    if not result_holder["done"]:
        return {
            "error": f"工具 {tool_name} 执行超过 {timeout_sec} 秒仍未返回。",
            "hint": "主线程可能仍在继续执行该操作。请稍后查看图层/日志确认结果，不要立即重复调用。",
            "still_running": True,
        }

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
