# -*- coding: utf-8 -*-
"""execute_pyqgis 受限运行环境 / execute_processing 输出契约的源码级守卫。

本轮报障点：用户问「统计各支局面积的最大/最小/中位数」，Agent 转了 10 轮工具
调用，一个数字都没给出来，最后交了一篇通篇 ✅ 但数值全是「待计算」的总结。

真因有两条，都很隐蔽：

**一、execute_pyqgis 是一把「永远打不响的枪」。**
    受限命名空间的 ``__builtins__`` 是一份纯白名单字典，**里面没有
    ``__import__``**（它还同时被列在 ``_REMOVED_BUILTINS`` 里）。于是
    ``exec(code, namespace)`` 中任何 ``import`` / ``from ... import`` 都会抛
    ``ImportError: __import__ not found`` —— 而错误信息**完全不指向根因**。
    可是 AST 扫描层（``_scan_code_safety``）又明确**放行** qgis / math / json
    等模块，两层安全策略互相矛盾。净效果：**任何带 import 的代码 100% 失败**，
    而 LLM 写 PyQGIS 代码的天然起手式恰恰就是 ``from qgis.core import ...``。

**二、execute_processing 的 memory: 输出不进工程、且只回一个 repr 字符串。**
    旧实现 ``serialized[k] = str(v)`` 会把结果图层变成
    ``"<QgsVectorLayer: 'output' (memory)>"`` —— 模型既不知道图层名、也不知道
    ID，更不知道结果在哪，只能猜个 "output" 去查，然后报「未找到图层」。

这类 bug 的可怕之处：**既有测试全绿**。因为 ``tests/fakes.py`` 的 ``_call_tool``
直接把整条 ``call_tool`` 换成了预写死的返回值，于是 ``exec(code, namespace)``
这段真实执行路径**从未被任何测试碰过** —— 假绿。所以这里把契约钉在源码层：

1. 受限命名空间必须注入 ``__import__``，且其判据与 AST 层**同源**；
2. 白名单外的模块必须仍然被拒（安全边界不得因修复而降级）；
3. ``_REMOVED_BUILTINS`` 这份与白名单矛盾的死代码不得复活；
4. ``execute_processing`` 必须把图层类输出加入工程并回报结构化信息；
5. 重试计数在成功时必须清零、强制总结必须要求「如实」、提示词必须写明
   导入白名单与禁止虚报 —— 这几条是同一轮报障里一起暴露出来的。
"""

import ast
import os
import unittest

try:
    from . import support
except ImportError:
    import support

ROOT = support.PROJECT_ROOT
TOOLS = os.path.join(ROOT, "qgis_tools.py")
PROCESSOR = os.path.join(ROOT, "processor.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _parse(path):
    return ast.parse(_read(path), filename=path)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("没找到函数 %s" % name)


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _sub_key(node):
    """取 Subscript 的常量键，兼容旧版 ast.Index 包装。"""
    sl = node.slice
    idx_cls = getattr(ast, "Index", None)
    if idx_cls is not None and isinstance(sl, idx_cls):
        sl = sl.value
    return _const_str(sl)


def _keys_assigned_to(func, var_name):
    """收集 ``var_name[<常量>] = ...`` 里被赋值的所有常量键。"""
    keys = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == var_name):
                    key = _sub_key(target)
                    if key is not None:
                        keys.add(key)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == var_name:
                key = _sub_key(node)
                if key is not None:
                    keys.add(key)
    return keys


def _called_names(func):
    """收集函数体内所有被调用的名字（Name 或 Attribute 的末段）。"""
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


class TestSafeBuiltinsWhitelist(unittest.TestCase):
    """受限命名空间的内建白名单。"""

    def test_safe_builtins_includes_type(self):
        """``type()`` 必须可用 —— 模型判断几何/对象类型时会用到。

        它原先被列在 _REMOVED_BUILTINS 里（连同 __import__ 一起），
        而 AST 层已经拦掉了 ``__`` 开头的属性访问，逃逸路径并不成立。
        """
        tree = _parse(TOOLS)
        values = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_SAFE_BUILTINS":
                        for elt in getattr(node.value, "elts", []):
                            s = _const_str(elt)
                            if s:
                                values.add(s)
        self.assertIn("type", values, "_SAFE_BUILTINS 缺少 type")
        for required in ("print", "isinstance", "len", "sorted", "sum", "str", "dict"):
            self.assertIn(required, values, "_SAFE_BUILTINS 缺少 %s" % required)

    def test_removed_builtins_dead_code_does_not_come_back(self):
        """_REMOVED_BUILTINS 是死代码，且把 __import__ 列为「应移除」。

        它与白名单语义重复、互相矛盾 —— 正是「禁 import」这个错误意图的来源。

        注意：这里检查的是**是否被定义**（AST 赋值），不是文本是否出现 ——
        注释里提到它的历史是合理的，只有真的重新定义才是回归。
        """
        tree = _parse(TOOLS)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == "_REMOVED_BUILTINS":
                        self.fail(
                            "_REMOVED_BUILTINS 复活了：它从未被引用，且与 "
                            "_SAFE_BUILTINS 语义冲突，保留只会让人误以为存在第二套策略")


class TestSafeImportInjection(unittest.TestCase):
    """execute_pyqgis 必须注入受控 __import__。"""

    def test_execute_pyqgis_injects_dunder_import(self):
        """本 bug 的**正面守卫**：缺了这行，任何 import 都会 100% 失败。"""
        func = _func(_parse(TOOLS), "execute_pyqgis")
        keys = _keys_assigned_to(func, "safe_builtins")
        self.assertIn(
            "__import__", keys,
            "execute_pyqgis 没有给 safe_builtins 注入 __import__ —— "
            "代码里任何 `import` 都会抛 'ImportError: __import__ not found'，"
            "而 LLM 写 PyQGIS 几乎必然带 import（这是本轮报障的根因）")

    def test_namespace_actually_carries_safe_builtins(self):
        """namespace['__builtins__'] 必须指向那份白名单，而不是被再次覆盖。"""
        source = _read(TOOLS)
        self.assertIn('"__builtins__": safe_builtins', source,
                      "namespace 没有挂载 safe_builtins")

    def test_safe_import_shares_whitelist_with_ast_layer(self):
        """执行层的导入判据必须与 AST 层**同源**（_is_module_allowed）。

        否则两层又会各走各的：AST 放行、执行拒绝（正是本 bug 的形态），
        或者反过来 AST 拒绝、执行放行（安全漏洞）。
        """
        func = _func(_parse(TOOLS), "_make_safe_import")
        self.assertIn(
            "_is_module_allowed", _called_names(func),
            "_make_safe_import 没有复用 _is_module_allowed —— "
            "两层安全策略会再次漂移")

    def test_safe_import_rejects_relative_imports(self):
        """相对导入必须显式拒绝（level > 0 时不得落到真实 __import__）。"""
        func = _func(_parse(TOOLS), "_make_safe_import")
        src = ast.dump(func)
        self.assertIn("level", src, "_make_safe_import 没有处理 level（相对导入）")


class TestAstLayerStillBlocksDangerousModules(unittest.TestCase):
    """安全边界不得因修复 import 而降级。"""

    def test_unsafe_module_set_is_intact(self):
        tree = _parse(TOOLS)
        blocked = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_UNSAFE_MODULES":
                        for elt in getattr(node.value, "elts", []):
                            s = _const_str(elt)
                            if s:
                                blocked.add(s)
        for must in ("os", "subprocess", "shutil", "socket", "ctypes", "pathlib", "io"):
            self.assertIn(must, blocked, "_UNSAFE_MODULES 不再拦截 %s" % must)

    def test_safe_module_prefixes_still_cover_qgis(self):
        """qgis / processing 必须仍在放行前缀里，否则修复等于没修。"""
        tree = _parse(TOOLS)
        prefixes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_SAFE_MODULE_PREFIXES":
                        for elt in getattr(node.value, "elts", []):
                            s = _const_str(elt)
                            if s:
                                prefixes.add(s)
        for must in ("qgis", "osgeo", "processing"):
            self.assertIn(must, prefixes, "_SAFE_MODULE_PREFIXES 缺少 %s" % must)


class TestProcessingOutputContract(unittest.TestCase):
    """execute_processing 的结果图层必须进工程并回报结构化信息。"""

    def test_processing_adds_layer_output_to_project(self):
        func = _func(_parse(TOOLS), "execute_processing")
        self.assertIn(
            "addMapLayer", _called_names(func),
            "execute_processing 不再把图层类输出加入工程 —— "
            "OUTPUT='memory:' 的结果会变成孤儿图层，模型随后按名字找不到它")

    def test_processing_returns_structured_info_for_layers(self):
        src = ast.dump(_func(_parse(TOOLS), "execute_processing"))
        self.assertIn("QgsMapLayer", src,
                      "execute_processing 不再识别图层类输出（isinstance(v, QgsMapLayer)）——"
                      "会退回成 str(v)，即 '<QgsVectorLayer: output (memory)>' 这种无用 repr")

    def test_processing_hint_mentions_project(self):
        source = _read(TOOLS)
        self.assertIn("结果图层已加入当前工程", source,
                      "缺少结果可用的 hint，模型不知道去哪里找")


class TestProcessorReliabilityFixes(unittest.TestCase):
    """同一轮报障暴露出的 processor 侧问题。"""

    def test_debug_retries_reset_inside_loop(self):
        """成功必须清零失败计数，否则零散失败会累积成「放弃重试」。

        原先只在循环外初始化、失败就累加、**成功从不重置** —— 一轮长任务里
        4 次零散失败（每次后面都改对了）也会触发整轮中止。
        """
        func = _func(_parse(PROCESSOR), "agent_chat")
        reset_in_loop = False
        for node in ast.walk(func):
            if isinstance(node, ast.For):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assign):
                        for target in sub.targets:
                            if isinstance(target, ast.Name) and target.id == "debug_retries":
                                reset_in_loop = True
        self.assertTrue(
            reset_in_loop,
            "agent_chat 的工具循环里没有重置 debug_retries —— "
            "零散的失败会累积触发「已放弃自动重试」")

    def test_forced_summary_demands_honesty(self):
        """跑满轮次的强制总结必须要求如实区分成败、不得占位/编造。"""
        source = _read(PROCESSOR)
        self.assertIn("如实总结", source,
                      "强制总结的措辞没有要求「如实」—— 模型会把失败包装成一串 ✅")
        self.assertIn("待计算", source,
                      "没有明确禁止用「待计算」等占位词顶替数值")
        self.assertIn("不得编造", source,
                      "没有明确禁止编造数值")

    def test_system_prompt_declares_import_whitelist(self):
        """提示词必须写明导入白名单，否则模型只能靠猜（并反复撞墙）。"""
        source = _read(PROCESSOR)
        self.assertIn("导入白名单", source,
                      "系统提示词没有声明导入白名单")
        for token in ("qgis.*", "processing", "禁止导入"):
            self.assertIn(token, source, "导入规则缺少 %s" % token)

    def test_system_prompt_forbids_false_reporting(self):
        source = _read(PROCESSOR)
        self.assertIn("禁止虚报成果", source,
                      "系统提示词缺少「禁止虚报成果」硬规则")

    def test_system_prompt_gives_statistics_recipe(self):
        """统计类需求要给出「一次算完 + 椭球面积」的推荐范式与单位换算。"""
        source = _read(PROCESSOR)
        self.assertIn("QgsDistanceArea", source)
        self.assertIn("measureArea", source)
        self.assertIn("1e6", source,
                      "没有给出 m² → km² 的换算系数")


if __name__ == "__main__":
    unittest.main()
