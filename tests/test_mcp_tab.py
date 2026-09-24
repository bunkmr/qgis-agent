# -*- coding: utf-8 -*-
"""「MCP」独立页签的结构一致性守卫（源码级，不需要真实 Qt）。

为什么用 AST 而不是实例化控件：本仓库的裸测试环境没有 Qt，控件构造要
搭一整套替身且断言价值有限；而这里要防的几类退化**全部是源码结构问题**：

1. 页签增删后**下标错位** —— v2 的页签 tooltip 是**按顺序**下发的
   （``for index, tip in enumerate([...])``），少写一条 / 顺序调换，
   后面所有页签的提示都会讲错内容，而且不会有任何报错。
2. 页签文案与页容器对不上 —— ``addTab`` 顺序就是界面顺序。
3. MCP 控件被填回「模型」页 —— 这正是本次需求要拆开的东西。
4. 页签切换回调退回硬编码下标 —— 增删页签时静默指向别的页。
5. 文档/提示文案还指着旧路径「模型配置 → MCP 服务」。
6. 「测试连通性」又用 ``sys.executable`` 拉子进程 —— 在 macOS 上那是
   QGIS 的 GUI 主程序，等于自检时再弹一个 QGIS 窗口。
"""

import ast
import os
import unittest

try:
    from . import support
except ImportError:
    import support

ROOT = support.PROJECT_ROOT
BASE_UI = os.path.join(ROOT, "qgis_agent_dockwidget_base_ui.py")
DOCK_V2 = os.path.join(ROOT, "qgis_agent_dockwidget_v2.py")
PLUGIN = os.path.join(ROOT, "qgis_agent.py")

EXPECTED_TABS = ["对话", "历史", "模型", "MCP", "工作流", "报告", "帮助"]

# 旧路径文案：MCP 曾经挂在「模型」页底部。CHANGELOG 是历史记录，不参与扫描。
STALE_PATH_TEXT = "模型配置 → MCP 服务"
SCAN_FILES = [
    "mcp_protocol.py",
    "mcp_bridge.py",
    "qgis_agent.py",
    "qgis_agent_dockwidget_v2.py",
    "qgis_agent_dockwidget_base_ui.py",
    "help_content.py",
    "README.md",
    "mcp_server/README.md",
    "mcp_server/qgis_agent_mcp_server.py",
]


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


def _decorated_attr_name(node):
    """`self.twTabs` -> 'twTabs'"""
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class TestTabRegistration(unittest.TestCase):

    def setUp(self):
        self.tree = _parse(BASE_UI)

    def _tabs(self):
        """按源码顺序取出 (容器属性名, 页签文案)"""
        found = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "addTab":
                continue
            if not node.args or not isinstance(node.args[0], ast.Attribute):
                continue
            if _decorated_attr_name(func.value) != "twTabs":
                continue
            label = node.args[1].value if len(node.args) > 1 else None
            found.append((node.args[0].attr, label))
        return found

    def test_tab_order(self):
        tabs = self._tabs()
        self.assertEqual([label for _obj, label in tabs], EXPECTED_TABS)

    def test_mcp_tab_sits_right_after_model_tab(self):
        tabs = self._tabs()
        labels = [label for _obj, label in tabs]
        self.assertEqual(labels[labels.index("模型") + 1], "MCP")
        self.assertEqual(tabs[labels.index("MCP")][0], "tbMcp")

    def test_every_tab_has_a_widget_object(self):
        for obj, label in self._tabs():
            with self.subTest(tab=label):
                self.assertTrue(obj.startswith("tb"), obj)


class TestMcpPageLayout(unittest.TestCase):

    def setUp(self):
        self.tree = _parse(BASE_UI)

    def test_mcp_page_and_layout_exist(self):
        source = _read(BASE_UI)
        self.assertIn("self.tbMcp = QtWidgets.QWidget()", source)
        self.assertIn("self.mcpLayout = QtWidgets.QVBoxLayout(self.tbMcp)", source)

    def test_mcp_layout_ends_with_stretch(self):
        """末尾必须是 addStretch：否则分组框会被拉长填满整页。"""
        init = _func(self.tree, "setupUi")
        calls = [n for n in ast.walk(init)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "addStretch"
                 and _decorated_attr_name(n.func.value) == "mcpLayout"]
        self.assertTrue(calls, "mcpLayout 末尾缺少 addStretch()")


class TestTabTooltipsMatchTabs(unittest.TestCase):
    """tooltip 是按顺序下发的：数量必须与页签数一致，否则整体错位。"""

    def setUp(self):
        self.tree = _parse(DOCK_V2)

    def _tooltips(self):
        func = _func(self.tree, "_configure_tab_bar")
        for node in ast.walk(func):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "enumerate"
                    and node.args
                    and isinstance(node.args[0], ast.List)):
                return [e.value for e in node.args[0].elts]
        raise AssertionError("_configure_tab_bar 里没找到 enumerate([...]) 形式的提示列表")

    def test_tooltip_count_matches_tab_count(self):
        self.assertEqual(len(self._tooltips()), len(EXPECTED_TABS))

    def test_tooltip_of_mcp_tab_mentions_mcp(self):
        tips = self._tooltips()
        self.assertIn("MCP", tips[EXPECTED_TABS.index("MCP")])

    def test_model_tab_tooltip_no_longer_mentions_mcp(self):
        tips = self._tooltips()
        self.assertNotIn("MCP", tips[EXPECTED_TABS.index("模型")])


class TestPluginWiring(unittest.TestCase):

    def setUp(self):
        self.tree = _parse(PLUGIN)
        self.source = _read(PLUGIN)

    def test_mcp_ui_goes_into_mcp_layout(self):
        func = _func(self.tree, "_build_mcp_settings_ui")
        targets = [n for n in ast.walk(func)
                   if isinstance(n, ast.Attribute) and n.attr == "mcpLayout"]
        self.assertTrue(targets, "MCP 控件没有填进 mcpLayout")
        self.assertNotIn("dockwidget.settingsLayout", ast.unparse(func))

    def test_tab_changed_has_no_hardcoded_index(self):
        func = _func(self.tree, "_on_tab_changed")
        bad = [n for n in ast.walk(func)
               if isinstance(n, ast.Compare)
               and isinstance(n.left, ast.Name)
               and n.left.id == "index"
               and any(isinstance(c, ast.Constant) and isinstance(c.value, int)
                       for c in n.comparators)]
        self.assertFalse(bad, "_on_tab_changed 又用上硬编码下标了")

    def test_selfcheck_uses_resolver_not_sys_executable_directly(self):
        func = _func(self.tree, "_on_mcp_selfcheck")
        called = [n for n in ast.walk(func)
                  if isinstance(n, ast.Call)
                  and ((isinstance(n.func, ast.Name)
                        and n.func.id == "resolve_python_executable")
                       or (isinstance(n.func, ast.Attribute)
                           and n.func.attr == "resolve_python_executable"))]
        self.assertTrue(called, "自检没有走 resolve_python_executable")

    def test_copy_config_surfaces_interpreter_hint(self):
        func = _func(self.tree, "_on_mcp_copy_config")
        self.assertIn("last_python_hint", ast.unparse(func))


class TestNoStalePathText(unittest.TestCase):
    """MCP 已经独立成页，任何地方都不该再说「模型配置 → MCP 服务」。"""

    def test_no_stale_mcp_path_in_docs_or_code(self):
        offenders = []
        for rel in SCAN_FILES:
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            if STALE_PATH_TEXT in _read(path):
                offenders.append(rel)
        self.assertEqual(offenders, [], "仍指向旧路径：%s" % ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
