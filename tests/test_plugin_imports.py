# -*- coding: utf-8 -*-
"""依赖探测的健壮性回归测试（qgis_agent._soft_import）。

背景（真机踩坑）：
    QGIS 3.44.14 环境里 pydantic 与 pydantic-core 版本错配，
    导入 langchain_core 时抛 SystemError（不是 ImportError）。
    旧实现只捕获 ImportError，异常从模块顶层逃逸 →
    整个插件加载失败且界面没有任何提示，用户完全无从判断原因。

本测试不 import 主模块（那需要 Qt/QGIS），而是用 ast 把 `_soft_import`
的函数体单独取出来执行，再用真实临时模块注入各种异常做验证。
"""

import ast
import os
import sys
import tempfile
import unittest

try:
    from . import support
except ImportError:
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT
PLUGIN_ENTRY = os.path.join(PROJECT_ROOT, "qgis_agent.py")

# 需要被「探测失败也算正常」的异常类型 —— 都是真机上实际出现过的
_FAILING_SNIPPETS = {
    "boom_systemerror": "raise SystemError('pydantic-core version mismatch')",
    "boom_oserror": "raise OSError('dlopen: framework load failed')",
    "boom_runtimeerror": "raise RuntimeError('half-installed dependency')",
    "boom_valueerror": "raise ValueError('bad version string')",
    "boom_attributeerror": "raise AttributeError('no such attribute')",
    "boom_importerror": "raise ImportError('missing submodule')",
}


def _load_soft_import():
    """从 qgis_agent.py 里单独取出 _soft_import 函数并编译执行。"""
    with open(PLUGIN_ENTRY, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=PLUGIN_ENTRY)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_soft_import":
            module = ast.Module(body=[node], type_ignores=[])
            namespace = {}
            exec(compile(module, PLUGIN_ENTRY + ":_soft_import", "exec"), namespace)
            return namespace["_soft_import"], node
    raise AssertionError("在 qgis_agent.py 中找不到 _soft_import")


class TestSoftImportCatchesEverything(unittest.TestCase):
    """_soft_import 必须把「依赖不可用」的各种异常统一吃掉，绝不外泄。"""

    @classmethod
    def setUpClass(cls):
        fn, cls.func_node = _load_soft_import()
        # 必须以 staticmethod 存：普通函数作为类属性会在 self.xxx() 时被绑定，
        # 平白多出一个 self 参数。
        cls.soft_import = staticmethod(fn)
        cls._tmpdir = tempfile.mkdtemp(prefix="qgis_agent_softimp_")
        for name, snippet in _FAILING_SNIPPETS.items():
            with open(os.path.join(cls._tmpdir, name + ".py"), "w",
                      encoding="utf-8") as fh:
                fh.write(snippet + "\n")
        sys.path.insert(0, cls._tmpdir)

    @classmethod
    def tearDownClass(cls):
        try:
            sys.path.remove(cls._tmpdir)
        except ValueError:
            pass
        for name in _FAILING_SNIPPETS:
            sys.modules.pop(name, None)

    # ── 行为 ──

    def test_importable_module_returns_true(self):
        self.assertTrue(self.soft_import("json"))

    def test_missing_module_returns_false(self):
        self.assertFalse(self.soft_import("definitely_not_a_real_module_xyz"))

    def test_system_error_does_not_escape(self):
        """核心回归：版本错配抛的 SystemError 必须被吃掉。"""
        self.assertFalse(self.soft_import("boom_systemerror"))

    def test_various_exceptions_do_not_escape(self):
        for name in _FAILING_SNIPPETS:
            with self.subTest(module=name):
                self.assertFalse(self.soft_import(name))

    def test_result_is_strict_bool(self):
        """返回值必须是 bool：上游用 all(...) 聚合，返回 None 会导致误判。"""
        for name in ("json", "definitely_not_a_real_module_xyz"):
            with self.subTest(module=name):
                self.assertIsInstance(self.soft_import(name), bool)

    # ── 源码约束（防止被人改回窄捕获）──

    def test_source_does_not_narrow_to_importerror(self):
        handlers = [n for n in ast.walk(self.func_node)
                    if isinstance(n, ast.ExceptHandler)]
        self.assertTrue(handlers, "_soft_import 里必须有 except 分支")
        for handler in handlers:
            if handler.type is None:      # 裸 except
                continue
            names = set()
            for sub in ast.walk(handler.type):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    names.add(sub.attr)
            self.assertFalse(
                names <= {"ImportError", "ModuleNotFoundError"},
                "_soft_import 不能只捕获 ImportError：依赖半装时抛的是 "
                "SystemError / OSError，异常逃逸会导致整个插件静默加载失败",
            )


class TestVersionNumberSingleSource(unittest.TestCase):
    """顺带守住「版本号唯一真源」：源码里不再散布硬编码版本字符串。"""

    def test_metadata_version_matches_changelog_latest(self):
        with open(os.path.join(PROJECT_ROOT, "metadata.txt"), "r",
                  encoding="utf-8-sig") as fh:
            meta = None
            for raw in fh:
                line = raw.strip()
                if line.startswith("version="):
                    meta = line.split("=", 1)[1].strip()
                    break
        self.assertTrue(meta, "metadata.txt 缺 version 字段")

        with open(os.path.join(PROJECT_ROOT, "CHANGELOG.md"), "r",
                  encoding="utf-8") as fh:
            changelog = fh.read()
        self.assertIn(
            "## [%s]" % meta, changelog,
            "CHANGELOG.md 缺少与 metadata.txt(%s) 对应的版本小节" % meta,
        )


if __name__ == "__main__":
    unittest.main()
