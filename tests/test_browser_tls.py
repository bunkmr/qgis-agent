# -*- coding: utf-8 -*-
"""「浏览器兼容 TLS」可选依赖（curl_cffi）缺失时的降级行为回归测试。

背景（真机踩坑）：
    用户在「模型配置」页勾选了「使用浏览器兼容 TLS」，但环境里没有 curl_cffi。
    旧实现在 llm_providers.get_llm_instance 里直接 raise RuntimeError，后果是
      · 「测试连接」只弹一个报错，测试根本跑不到；
      · 更严重的是 processor 每次构造都抛异常 —— **对话功能整条不可用**。
    curl_cffi 只是可选加速项，缺了它必须降级（改走标准 TLS 栈），而不是把插件打死。

本测试不 import llm_providers（它模块级依赖 httpx / langchain，裸环境没有），
而是用 ast 取出 `browser_tls_available` / `resolve_browser_tls` 单独执行，
再用真实临时模块注入各种「导入期异常」做验证。
"""

import ast
import contextlib
import importlib
import os
import sys
import tempfile
import unittest

try:
    from . import support
except ImportError:
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT
LLM_PROVIDERS = os.path.join(PROJECT_ROOT, "llm_providers.py")

_OK_SOURCE = "__version__ = '0.99.0'"

# curl_cffi 是带原生扩展的包：导入失败的各种真实类型
_FAILING_SOURCES = {
    "importerror": "raise ImportError('no module named _wrapper')",
    "modulenotfound": "raise ModuleNotFoundError('No module named curl_cffi._wrapper')",
    "oserror": "raise OSError('dlopen: framework load failed')",
    "systemerror": "raise SystemError('incompatible native extension')",
    "runtimeerror": "raise RuntimeError('half-installed dependency')",
}


class _RecordingLogger:
    """替身 logger：记下 warning 文案，用于断言「降级时确实告知了用户」。"""

    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(msg % args if args else msg)


def _load_functions(names):
    """从 llm_providers.py 里取出指定函数，编译进同一个命名空间后返回。"""
    with open(LLM_PROVIDERS, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=LLM_PROVIDERS)
    wanted = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted[node.name] = node
    missing = [n for n in names if n not in wanted]
    if missing:
        raise AssertionError("llm_providers.py 缺少函数：%s" % missing)

    module = ast.Module(body=[wanted[n] for n in names], type_ignores=[])
    logger = _RecordingLogger()
    namespace = {"logger": logger}
    exec(compile(module, LLM_PROVIDERS + ":browser_tls", "exec"), namespace)
    return namespace, wanted, logger


@contextlib.contextmanager
def fake_curl_cffi(source):
    """把一个假的 curl_cffi 模块放到 sys.path 最前面（源码可控）。"""
    tmpdir = tempfile.mkdtemp(prefix="qgis_agent_curlcffi_")
    with open(os.path.join(tmpdir, "curl_cffi.py"), "w", encoding="utf-8") as fh:
        fh.write(source + "\n")
    saved = sys.modules.pop("curl_cffi", None)
    sys.path.insert(0, tmpdir)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        try:
            sys.path.remove(tmpdir)
        except ValueError:
            pass
        sys.modules.pop("curl_cffi", None)
        if saved is not None:
            sys.modules["curl_cffi"] = saved
        importlib.invalidate_caches()


@contextlib.contextmanager
def no_curl_cffi():
    """保证此刻 curl_cffi 不可导入。"""
    saved = sys.modules.pop("curl_cffi", None)
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["curl_cffi"] = saved


class TestBrowserTlsAvailability(unittest.TestCase):
    """browser_tls_available 只回答「能不能用」，绝不外泄异常。"""

    @classmethod
    def setUpClass(cls):
        ns, nodes, cls.logger = _load_functions(
            ["browser_tls_available", "resolve_browser_tls"]
        )
        cls.available = staticmethod(ns["browser_tls_available"])
        cls.resolve = staticmethod(ns["resolve_browser_tls"])
        cls.nodes = nodes

    def test_available_module_returns_true(self):
        with fake_curl_cffi(_OK_SOURCE):
            self.assertTrue(self.available())

    def test_missing_module_returns_false(self):
        with no_curl_cffi():
            self.assertFalse(self.available())

    def test_import_time_exceptions_do_not_escape(self):
        """核心回归：导入期抛的非 ImportError 异常也必须被吃掉。"""
        for name, source in _FAILING_SOURCES.items():
            with self.subTest(kind=name), fake_curl_cffi(source):
                self.assertFalse(self.available())

    def test_result_is_strict_bool(self):
        for source in (_OK_SOURCE, _FAILING_SOURCES["oserror"]):
            with self.subTest(source=source.split("(")[0]), fake_curl_cffi(source):
                self.assertIsInstance(self.available(), bool)


class TestResolveBrowserTls(unittest.TestCase):
    """resolve_browser_tls：勾了但依赖不在位 → 降级，不报错。"""

    @classmethod
    def setUpClass(cls):
        ns, nodes, cls.logger = _load_functions(
            ["browser_tls_available", "resolve_browser_tls"]
        )
        cls.resolve = staticmethod(ns["resolve_browser_tls"])
        cls.nodes = nodes

    def setUp(self):
        self.logger.warnings.clear()

    def test_not_requested_is_noop(self):
        with no_curl_cffi():
            self.assertEqual(self.resolve(False), (False, ""))

    def test_requested_and_available_enables(self):
        with fake_curl_cffi(_OK_SOURCE):
            self.assertEqual(self.resolve(True), (True, ""))

    def test_requested_but_missing_degrades_without_raising(self):
        with no_curl_cffi():
            effective, reason = self.resolve(True)
        self.assertFalse(effective)
        self.assertTrue(reason, "降级时必须给出原因，供界面如实告知用户")

    def test_degrade_logs_warning(self):
        with no_curl_cffi():
            self.resolve(True)
        self.assertTrue(
            any("curl_cffi" in w for w in self.logger.warnings),
            "降级属于「用户以为开了、其实没开」，必须留下日志",
        )

    def test_all_import_time_failures_degrade(self):
        for name, source in _FAILING_SOURCES.items():
            with self.subTest(kind=name), fake_curl_cffi(source):
                self.assertEqual(self.resolve(True)[0], False)

    def test_return_arity_is_stable(self):
        """调用方按 (effective, reason) 解包；顺序/长度被改会静默出错。"""
        for requested in (True, False):
            with no_curl_cffi():
                result = self.resolve(requested)
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 2)
            self.assertIsInstance(result[0], bool)
            self.assertIsInstance(result[1], str)


class TestSourceConstraints(unittest.TestCase):
    """源码级约束：防止有人把「降级」改回「抛异常」。"""

    def _func_source(self, path, name):
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node, src
        raise AssertionError("%s 中找不到 %s" % (path, name))

    def test_availability_check_does_not_narrow_to_importerror(self):
        node, _ = self._func_source(LLM_PROVIDERS, "browser_tls_available")
        handlers = [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]
        self.assertTrue(handlers, "browser_tls_available 必须有 except 分支")
        for handler in handlers:
            if handler.type is None:
                continue
            names = set()
            for sub in ast.walk(handler.type):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    names.add(sub.attr)
            self.assertFalse(
                names <= {"ImportError", "ModuleNotFoundError"},
                "不能只捕 ImportError：curl_cffi 是原生扩展，半装时抛 OSError/SystemError，"
                "异常逃逸会让整条 LLM 调用链失败",
            )

    def test_get_llm_instance_no_longer_raises_for_missing_dep(self):
        node, src = self._func_source(LLM_PROVIDERS, "get_llm_instance")
        self.assertNotIn(
            "需要安装 curl_cffi", src,
            "旧实现用 raise RuntimeError 处理缺失依赖，会让对话与测试连接全部不可用",
        )
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        self.assertTrue(
            any(getattr(c.func, "id", "") == "resolve_browser_tls" for c in calls),
            "get_llm_instance 必须调用 resolve_browser_tls 做降级判断",
        )

    def test_processor_uses_resolver(self):
        """对话入口同样要走解析函数：陈旧设置也不能让 __init__ 抛异常。"""
        path = os.path.join(PROJECT_ROOT, "processor.py")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("resolve_browser_tls", src)

    def test_toggle_clears_setting_when_dependency_missing(self):
        """UI 层：依赖不在位时勾选必须被按住，且不把 True 写进设置。"""
        path = os.path.join(PROJECT_ROOT, "qgis_agent.py")
        node, _ = self._func_source(path, "_on_browser_tls_changed")
        clears = False
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            if getattr(call.func, "attr", "") != "setValue":
                continue
            if any(isinstance(a, ast.Constant) and a.value is False for a in call.args):
                clears = True
        self.assertTrue(
            clears,
            "_on_browser_tls_changed 在依赖缺失时必须把 use_browser_tls 写回 False，"
            "否则设置里写着「已启用」而实际永远不生效",
        )

    def test_checkbox_disabled_when_dependency_missing(self):
        """依赖缺失时开关必须置灰，且置灰要由 _browser_tls_ready 守卫。

        能点却永远不生效的开关，只会换来一个「装了也不生效」的弹窗；
        置灰 + 灰字说明才能真正断掉这条误导路径。
        """
        path = os.path.join(PROJECT_ROOT, "qgis_agent.py")
        node, _ = self._func_source(path, "_build_browser_tls_ui")

        def _disables(statements):
            for stmt in statements:
                for call in [n for n in ast.walk(stmt) if isinstance(n, ast.Call)]:
                    if getattr(call.func, "attr", "") != "setEnabled":
                        continue
                    if any(isinstance(a, ast.Constant) and a.value is False
                           for a in call.args):
                        return True
            return False

        guarded = any(
            isinstance(n, ast.If)
            and "_browser_tls_ready" in ast.dump(n.test)
            and _disables(n.body)
            for n in ast.walk(node)
        )
        self.assertTrue(
            guarded,
            "_build_browser_tls_ui 必须在 not self._browser_tls_ready 分支里 "
            "setEnabled(False)，否则未装 curl_cffi 时开关仍可点击并弹出误导对话框",
        )

    def test_hint_tells_user_how_to_install(self):
        """灰字提示要给出可照抄的命令，而不是只说「不可用」。"""
        path = os.path.join(PROJECT_ROOT, "qgis_agent.py")
        node, _ = self._func_source(path, "_refresh_browser_tls_hint")
        text = "".join(
            n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        self.assertIn("pip install curl_cffi", text)

    def test_token_field_shows_head_not_tail(self):
        """令牌 64 位、输入框显示不全：停在末尾时屏幕上只剩后半截，手抄必错。"""
        path = os.path.join(PROJECT_ROOT, "qgis_agent.py")
        node, _ = self._func_source(path, "_build_mcp_settings_ui")
        found = False
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            if getattr(call.func, "attr", "") != "setCursorPosition":
                continue
            if any(isinstance(a, ast.Constant) and a.value == 0 for a in call.args):
                found = True
        self.assertTrue(found, "令牌输入框必须把光标放回开头（setCursorPosition(0)）")


if __name__ == "__main__":
    unittest.main()
