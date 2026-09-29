# -*- coding: utf-8 -*-
"""沙箱「宿主运行时窄口」与 AST dunder 加固的守卫（v2.4.13）。

现场故障（GitHub issue #1 的真拦路虎，Qt5 + Qt6 双机复现）：
    Agent 在 execute_pyqgis 里调 ``QgsProject.instance().mapLayersByName(...)``，
    进程内**首次**调用时必炸，报一句完全指不到根因的话：
        <built-in method mapLayersByName ...> returned a result with an exception set
    同一个脚本**再跑一次（warm）就成功**，cold 必失败。

真因：PyQt/sip 在 mapLayersByName 的 C++ 实现里做了一次**惰性 ``import gc``**，
而它命中的是【当前帧的 ``__builtins__``】—— 正是本沙箱那份受限字典。
``gc`` 在 ``_UNSAFE_MODULES`` 里 → ImportError → 异常在 C 层被吞成上面那句话。

修法（本文件钉住的四条，缺一不可）：
  A. 执行层开一个**只给宿主**的窄口 ``_HOST_RUNTIME_MODULES``（目前只有 gc），
     且**绝不能**渗进 ``_is_module_allowed`` —— 一旦渗进去，gc 就成了用户可导入的
     模块，而 ``gc.get_objects()`` 能直接枚举出 os 模块对象、**无需任何 dunder
     就能逃逸**；
  B. AST 层必须照旧拒掉 ``import gc``，否则用户代码就能踩着这个窄口进来；
  C. AST 层补上**裸名 dunder** 检查 —— v2.4.12 之前
     ``__builtins__['__import__']('gc')`` 能整条绕过扫描（下标取值不是
     ``Call(func=Attribute)``，``_called_name`` 拿到空串，``__builtins__`` 这个名字
     当时没有任何一道检查在看）；
  D. 上述两条必须同时成立：单独放宽任何一条都是安全退化。
"""

import ast
import os
import unittest
from unittest import mock

try:
    from . import support
except ImportError:
    import support  # noqa: F401

support.install_qgis_stub()

QT = support.import_mod("qgis_tools")
TOOLS_SRC = os.path.join(support.PROJECT_ROOT, "qgis_tools.py")

#: v2.4.12 的判据下最容易绕过扫描的样本
BYPASS_SNIPPET = "__builtins__['__import__']('gc')"


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _func_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("源码里找不到函数 %s" % name)


def _names_referenced_in(func_node):
    """收集函数体里被引用的裸名字（用于检查某个常量有没有渗透进来）。

    本函数自身也要有灵敏度：喂一份「故意写坏」的源码时必须能找出来
    （见 TestGuardSensitivity）。
    """
    return {n.id for n in ast.walk(func_node) if isinstance(n, ast.Name)}


class TestHostRuntimeNarrowGate(unittest.TestCase):
    """A 组：宿主窄口本身"""

    def test_gc_is_still_unsafe_for_users(self):
        self.assertIn("gc", QT._UNSAFE_MODULES)
        self.assertNotIn("gc", QT._SAFE_MODULES)
        self.assertFalse(QT._is_module_allowed("gc"),
                         "gc 一旦进入用户白名单，沙箱等于失效")

    def test_host_gate_contains_only_gc(self):
        self.assertEqual(QT._HOST_RUNTIME_MODULES, frozenset({"gc"}),
                         "宿主窄口必须是一个显式白名单，不能顺手放大")

    def test_host_gate_does_not_leak_into_shared_judgement(self):
        """_is_module_allowed 是 AST 层与执行层**共用**的判据，窄口不得渗入。"""
        tree = ast.parse(_read(TOOLS_SRC))
        names = _names_referenced_in(_func_node(tree, "_is_module_allowed"))
        self.assertNotIn("_HOST_RUNTIME_MODULES", names,
                         "窄口渗进共用判据 = gc 变成用户可导入模块")

    def test_safe_import_honours_host_gate(self):
        safe_import = QT._make_safe_import()
        # 宿主窄口：PyQt 内部那次惰性 import 命中当前帧 __builtins__ 时放行
        self.assertIsNotNone(safe_import("gc"))
        # 白名单内的常规模块照常可用
        self.assertIsNotNone(safe_import("json"))

    def test_safe_import_still_blocks_everything_else(self):
        safe_import = QT._make_safe_import()
        for bad in ("os", "socket", "subprocess", "ctypes", "shutil", "inspect"):
            with self.subTest(module=bad):
                with self.assertRaises(ImportError):
                    safe_import(bad)


class TestAstLayerStillBlocksGc(unittest.TestCase):
    """B 组：AST 层必须照旧拒 gc（否则窄口就是给用户开的门）"""

    def test_import_gc_rejected(self):
        err = QT._scan_code_safety("import gc\nprint(gc.get_objects()[:1])")
        self.assertIsNotNone(err, "import gc 必须被 AST 层拒掉")
        self.assertIn("gc", err)

    def test_from_gc_import_rejected(self):
        self.assertIsNotNone(QT._scan_code_safety("from gc import get_objects"))

    def test_execute_pyqgis_rejects_gc_end_to_end(self):
        r = QT.execute_pyqgis("import gc\nprint(len(gc.get_objects()))")
        self.assertFalse(r.get("executed"))
        self.assertIn("gc", str(r.get("error", "")))


class TestAstDunderNameHole(unittest.TestCase):
    """C 组：v2.4.13 补的洞 —— 裸名 dunder"""

    def test_builtins_subscript_import_is_rejected(self):
        err = QT._scan_code_safety(BYPASS_SNIPPET)
        self.assertIsNotNone(err, "这是 v2.4.13 之前能整条绕过的写法")
        self.assertIn("双下划线", err)

    def test_the_old_ruleset_could_not_see_it(self):
        """反向对照：证明这条样本**不是**被旧判据抓到的。

        旧判据只看 ``Call(func=Name|Attribute)`` 的名字与 ``Attribute``。这个样本里
        ``__builtins__['__import__']('gc')`` 的 ``func`` 是 ``Subscript``，
        ``_called_name`` 返回空串 → 落不进 _UNSAFE_NAME_CALLS；
        ``__builtins__`` 又只是个 Name，当时没有任何检查在看它。
        """
        tree = ast.parse(BYPASS_SNIPPET)
        called = {QT._called_name(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(called & QT._UNSAFE_NAME_CALLS,
                         "旧判据看不到它 —— 正因如此才必须补 Name 分支")
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(attrs & QT._UNSAFE_ATTR_CALLS)

    def test_bare_dunder_names_are_rejected(self):
        for snippet in ("x = __name__",
                        "print(__class__)",
                        "y = __builtins__",
                        "z = __globals__"):
            with self.subTest(code=snippet):
                err = QT._scan_code_safety(snippet)
                self.assertIsNotNone(err, "%r 应被拒" % snippet)
                self.assertIn("双下划线", err)

    def test_dunder_attribute_still_rejected(self):
        self.assertIsNotNone(QT._scan_code_safety("print(().__class__.__bases__)"))
        self.assertIsNotNone(QT._scan_code_safety("getattr(x, '__class__')"))

    def test_normal_code_is_not_harmed(self):
        """不能因为堵洞而误伤正常代码。"""
        for snippet in ("from qgis.core import QgsProject",
                        "import math\nprint(math.pi)",
                        "d = {'a': 1}\nprint(d['a'])",
                        "print(len([i for i in range(3)]))"):
            with self.subTest(code=snippet):
                self.assertIsNone(QT._scan_code_safety(snippet),
                                  "%r 是正常代码，不该被拒" % snippet)


class TestGuardSensitivity(unittest.TestCase):
    """D 组：守卫自身的灵敏度 —— 抓不到退化就是假绿"""

    def test_source_guard_detects_the_leak_it_forbids(self):
        """把窄口故意写进 _is_module_allowed 的伪造源码，守卫必须能发现。"""
        bad_src = (
            "def _is_module_allowed(root: str) -> bool:\n"
            "    if root in _HOST_RUNTIME_MODULES:\n"
            "        return True\n"
            "    return False\n"
        )
        with mock.patch.object(QT, "_HOST_RUNTIME_MODULES", frozenset({"gc"})):
            names = _names_referenced_in(_func_node(ast.parse(bad_src), "_is_module_allowed"))
        self.assertIn("_HOST_RUNTIME_MODULES", names,
                      "如果这里也发现不了，上面那条源码守卫就是摆设")

    def test_name_dunder_branch_exists_in_source(self):
        """AST 层必须真的带着 Name 分支（语义测试过了，再钉一次结构）。"""
        tree = ast.parse(_read(TOOLS_SRC))
        node = _func_node(tree, "_scan_code_safety")
        hits = []
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            test = sub.test
            if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) \
                    and test.func.attr == "startswith":
                hits.append(ast.dump(test))
        self.assertTrue(any("'__'" in h for h in hits),
                        "缺少「名字以 __ 开头即拒」的分支")

    def test_reversed_gate_would_let_gc_through(self):
        """反向验证：把窄口当成共用判据会立刻放行 gc。"""
        self.assertFalse(QT._is_module_allowed("gc"))
        with mock.patch.object(QT, "_is_module_allowed",
                               lambda root: root in QT._HOST_RUNTIME_MODULES):
            self.assertTrue(QT._is_module_allowed("gc"),
                            "说明 _is_module_allowed 就是那条闸门本身")


if __name__ == "__main__":
    unittest.main()
