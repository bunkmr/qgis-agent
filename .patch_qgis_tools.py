# -*- coding: utf-8 -*-
"""一次性安全加固补丁（仅作用于 qgis_tools.py）"""
import io
import sys

PATH = "qgis_tools.py"
src = io.open(PATH, encoding="utf-8").read()

PATCHES = []

# A) get_qgis_info 图层名净化
PATCHES.append((
'''        info = {
            "id": layer_id,
            "name": layer.name(),
            "type": _get_layer_type(layer),''',
'''        info = {
            "id": layer_id,
            # 图层名来自数据源/工程文件，属不可信输入，净化后再回喂 LLM
            "name": _sanitize_untrusted(layer.name(), 120),
            "type": _get_layer_type(layer),''',
))

# B) get_layer_features 属性值/字段名净化 + 总量截断
PATCHES.append((
'''        attrs = {}
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
    }''',
'''        attrs = {}
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
    return _truncate_result(result)''',
))

# C) 代码安全扫描辅助函数（插到 execute_pyqgis 之前）
SCANNER = u'''# ──────────────────────────────────────────────
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


'''

PATCHES.append((
'''def execute_pyqgis(code: str):
    """在 QGIS 环境中直接执行 PyQGIS 代码，并捕获输出"""
    stdout_capture = io.StringIO()''',
SCANNER + '''def execute_pyqgis(code: str):
    """在 QGIS 环境中直接执行 PyQGIS 代码，并捕获输出"""
    # 执行前先做 AST 静态扫描：命中黑名单直接拒绝，不再进入确认流程
    reject_reason = _scan_code_safety(code)
    if reject_reason:
        return {
            "error": f"代码安全检查未通过：{reject_reason}",
            "executed": False,
            "hint": "受限运行环境已移除 eval/exec/compile/__import__ 等内建，文件读写能力同样受限，请改用 QGIS API 完成该操作。",
        }

    stdout_capture = io.StringIO()''',
))

# D) namespace 受限 __builtins__ + 诚实的 exec 注释
PATCHES.append((
'''            "QgsTextFormat": QgsTextFormat,
        }
        exec(code, namespace)  # nosec B102 - intentional: executing user PyQGIS code in sandboxed namespace''',
'''            "QgsTextFormat": QgsTextFormat,
            # 受限内建：移除可执行任意代码/动态导入的入口
            "__builtins__": {
                k: v for k, v in builtins.__dict__.items()
                if k not in _REMOVED_BUILTINS
            },
        }
        # nosec B102 - 非沙箱：代码在用户逐次确认后以 QGIS 进程权限运行，已做 AST 危险调用扫描
        exec(code, namespace)''',
))

# E) render_map 覆盖已存在文件时确认
PATCHES.append((
'''def render_map(output_path: str, width: int = 800, height: int = 600):
    """将当前地图视图渲染为图片"""
    try:''',
'''def render_map(output_path: str, width: int = 800, height: int = 600):
    """将当前地图视图渲染为图片"""
    # 输出目标已存在时会被覆盖，先取得用户确认
    try:
        if output_path and os.path.exists(output_path) and not _skip_all_confirms:
            preview = json.dumps(
                {"output_path": _sanitize_untrusted(output_path, 300), "width": width, "height": height},
                ensure_ascii=False, indent=2,
            )
            if not _request_confirmation("render_map", f"将覆盖已存在的文件：\\n{preview}"):
                return {"error": "用户取消了 render_map 操作。"}
    except Exception as e:
        logger.debug("render_map 覆盖确认检查失败: %s", e, exc_info=True)
        return {"error": "确认通道未就绪，已拒绝覆盖已存在的文件。"}

    try:''',
))

# F) 危险工具名单扩展
PATCHES.append((
'''_DANGEROUS_TOOLS = {"execute_pyqgis", "execute_processing"}''',
'''_DANGEROUS_TOOLS = {
    "execute_pyqgis",
    "execute_processing",
    "remove_layer",
    "load_project",
    "save_project",
}''',
))

# G) 确认回调缺失时拒绝
PATCHES.append((
'''            confirm_holder["confirmed"] = True  # 无回调时默认确认''',
'''            # 确认通道未就绪时拒绝执行，避免危险工具静默运行
            confirm_holder["confirmed"] = False''',
))

# H) 抽出确认请求/预览构造，call_tool 复用（失败即拒绝）
PATCHES.append((
'''def call_tool(tool_name: str, arguments: dict) -> dict:
    """调用指定工具并返回结果。

    关键：QGIS API 不是线程安全的，所有工具必须在主线程中执行。
    如果当前不在主线程，通过信号/槽 + QWaitCondition 调度到主线程同步执行。

    危险工具（execute_pyqgis, execute_processing）在执行前会通过
    _code_confirm_callback 弹出确认对话框。
    """''',
'''def _build_confirm_preview(tool_name: str, arguments: dict) -> str:
    """构造确认弹窗展示的内容（危险提示 + 参数/代码预览）"""
    if tool_name == "execute_pyqgis":
        # 明确告知执行权限，避免用户误以为代码运行在隔离沙箱中
        return "此代码将在本机以你的 QGIS 进程权限运行。\\n" + str(arguments.get("code", ""))
    if tool_name == "execute_processing":
        body = f"algorithm: {arguments.get('algorithm', '')}\\n"
        body += f"parameters: {json.dumps(arguments.get('parameters', {}), indent=2, ensure_ascii=False)}"
        return "此算法将在本机以你的 QGIS 进程权限运行。\\n" + body
    if tool_name == "remove_layer":
        return "将从当前工程中移除图层：\\n" + _sanitize_untrusted(arguments.get("layer_id_or_name", ""), 200)
    if tool_name == "load_project":
        return "加载工程会丢弃当前未保存的修改：\\n" + _sanitize_untrusted(arguments.get("path", ""), 300)
    if tool_name == "save_project":
        target = arguments.get("path") or "(当前工程路径)"
        return "保存工程，可能覆盖已存在文件：\\n" + _sanitize_untrusted(target, 300)
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
    """''',
))

PATCHES.append((
'''    # ── 危险工具确认 ──
    if tool_name in _DANGEROUS_TOOLS and not _skip_all_confirms and _code_confirm_callback is not None:
        code_preview = ""
        if tool_name == "execute_pyqgis":
            code_preview = arguments.get("code", "")
        elif tool_name == "execute_processing":
            code_preview = f"algorithm: {arguments.get('algorithm', '')}\\n"
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
                return {"error": f"用户取消了 {tool_name} 操作。"}''',
'''    # ── 危险工具确认（确认通道不可用即拒绝，不放行）──
    if tool_name in _DANGEROUS_TOOLS and not _skip_all_confirms:
        if _code_confirm_callback is None:
            return {"error": f"确认通道未就绪，已拒绝执行 {tool_name}。"}
        if not _request_confirmation(tool_name, _build_confirm_preview(tool_name, arguments)):
            return {"error": f"用户取消了 {tool_name} 操作。"}''',
))

failed = []
for idx, (old, new) in enumerate(PATCHES):
    if src.count(old) != 1:
        failed.append((idx, src.count(old)))
        continue
    src = src.replace(old, new, 1)

if failed:
    sys.stderr.write("PATCH FAILED: %r\n" % (failed,))
    sys.exit(1)

io.open(PATH, "w", encoding="utf-8").write(src)
sys.stdout.write("OK: %d patches applied\n" % len(PATCHES))
