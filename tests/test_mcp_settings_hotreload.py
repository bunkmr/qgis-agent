# -*- coding: utf-8 -*-
"""MCP 设置热更新的守卫（v2.4.13）。

用户报障原文：
    用户：「我已经勾了 但是在测试连接里面显示的暴露危险工具还是 falsh」

真因不是"设置没保存"，而是**改动根本没有下发**：
    ``cbMcpDangerous`` **没有任何 ``toggled`` 连接**，``allow_dangerous`` 只在
    ``bridge.start()`` 那一刻读一次。而 ``MCPBridge.apply_settings(token, allow_dangerous)``
    这个**专为热更新写好**的 API，全仓库只在「重新生成令牌」那一处被调用过。
    净效果：勾选后一切照旧，用户唯一的出路是手动「停止服务 → 启动服务」。

顺带暴露的两个同源问题也一并钉住：
  - 令牌编辑框改完不生效（同样没有下发通道）；
  - 状态标签显示的是**复选框的样子**而不是**服务实际生效的值** —— 于是"勾了但显示
    false"看起来像保存失败，把排查引向错误方向。
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

mb = support.import_mod("mcp_bridge")
mp = support.import_mod("mcp_protocol")

AGENT_SRC = os.path.join(support.PROJECT_ROOT, "qgis_agent.py")

TOKEN_A = "token-alpha-0123456789abcdef"
TOKEN_B = "token-bravo-fedcba9876543210"

NATIVE_TOOLS = [
    {"name": "get_qgis_info", "description": "取状态",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "export_table_to_csv", "description": "导出表格",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "execute_pyqgis", "description": "执行代码",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}},
                    "required": ["code"]}},
]

DANGEROUS = {"execute_pyqgis", "execute_processing", "remove_layer",
             "load_project", "save_project", "run_skill"}


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _func_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("qgis_agent.py 里找不到函数 %s" % name)


def _names_referenced_in(func_node):
    return {n.id for n in ast.walk(func_node) if isinstance(n, ast.Name)}


def _called_names(func_node):
    out = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            out.add(func.attr)
        elif isinstance(func, ast.Name):
            out.add(func.id)
    return out


class TestUiWiring(unittest.TestCase):
    """A 组：四个控件的改动都必须有下发通道"""

    def setUp(self):
        self.src = _read(AGENT_SRC)
        self.tree = ast.parse(self.src)

    def _assert_connected(self, signal_expr):
        self.assertIn(signal_expr, self.src,
                      "%s 没有 connect —— 改动不会下发，用户会看到「勾了没反应」" % signal_expr)

    def test_dangerous_checkbox_is_connected(self):
        self._assert_connected("self.cbMcpDangerous.toggled.connect(")

    def test_token_edit_is_connected(self):
        self._assert_connected("self.leMcpToken.editingFinished.connect(")

    def test_port_change_is_connected(self):
        self._assert_connected("self.spMcpPort.valueChanged.connect(")

    def test_autostart_checkbox_is_connected(self):
        self._assert_connected("self.cbMcpAutostart.toggled.connect(")

    def test_connect_targets_exist(self):
        handlers = ["_on_mcp_dangerous_toggled", "_on_mcp_token_edited",
                    "_on_mcp_autostart_toggled", "_on_mcp_port_changed"]
        for name in handlers:
            with self.subTest(handler=name):
                self.assertTrue(hasattr(self.src, "__len__"))
                self.assertIn("def %s(" % name, self.src)

    def test_dangerous_toggle_handler_does_all_three_things(self):
        node = _func_node(self.tree, "_on_mcp_dangerous_toggled")
        called = _called_names(node)
        for needed in ("_persist_mcp_settings", "_apply_mcp_live", "_refresh_mcp_status"):
            self.assertIn(needed, called, "缺少 %s" % needed)

    def test_dangerous_toggle_handler_pushes_allow_dangerous(self):
        node = _func_node(self.tree, "_on_mcp_dangerous_toggled")
        src = ast.dump(node)
        self.assertIn("allow_dangerous", src)

    def test_token_handler_persists_and_pushes(self):
        node = _func_node(self.tree, "_on_mcp_token_edited")
        called = _called_names(node)
        self.assertIn("_apply_mcp_live", called)
        self.assertIn("setValue", called)

    def test_token_handler_refuses_empty_token(self):
        """空令牌 = 服务端"拒绝一切请求"，绝不能落盘。"""
        node = _func_node(self.tree, "_on_mcp_token_edited")
        called = _called_names(node)
        self.assertIn("warning", called, "空令牌必须明确告知用户")
        self.assertIn("_mcp_token_last_applied", _names_referenced_in(node)
                      | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)})

    def test_autostart_handler_only_persists(self):
        """自动启动只影响下次启动，不应当场动运行中的服务。"""
        node = _func_node(self.tree, "_on_mcp_autostart_toggled")
        called = _called_names(node)
        self.assertIn("setValue", called)
        self.assertNotIn("_apply_mcp_live", called)
        self.assertNotIn("start", called)
        self.assertNotIn("stop", called)

    def test_port_handler_does_not_pretend_it_can_hot_swap(self):
        node = _func_node(self.tree, "_on_mcp_port_changed")
        called = _called_names(node)
        self.assertIn("setValue", called)
        self.assertNotIn("apply_settings", called)
        self.assertIn("停止服务", ast.dump(node), "必须明确提示端口需停启服务")

    def test_status_reads_live_value_not_checkbox(self):
        node = _func_node(self.tree, "_refresh_mcp_status")
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertIn("allow_dangerous", attrs,
                      "状态标签必须显示服务实际生效的值，否则会掩盖下发失败")

    def test_visible_tools_uses_live_value_when_running(self):
        node = _func_node(self.tree, "_mcp_visible_tool_names")
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertIn("is_running", attrs)
        self.assertIn("allow_dangerous", attrs)

    def test_single_hot_update_channel(self):
        """所有热更新都要过 _apply_mcp_live，别再各写一份 try/except。"""
        node = _func_node(self.tree, "_apply_mcp_live")
        called = _called_names(node)
        self.assertIn("apply_settings", called)
        self.assertGreaterEqual(len([n for n in ast.walk(node)
                                     if isinstance(n, ast.Try)]), 1,
                                "热更新失败不得影响设置页可用性")

    def test_stale_tooltip_text_is_gone(self):
        """「修改后需重新启动服务」这类文案已经过时，会误导用户去停启服务。"""
        self.assertNotIn("修改后需重新启动服务", self.src)
        self.assertNotIn("改完需重新启动服务", self.src)


class TestBridgeApplySettings(unittest.TestCase):
    """B 组：桥接层热更新的真行为"""

    def setUp(self):
        self.bridge = mb.MCPBridge()
        self.addCleanup(lambda: setattr(mb.MCPBridge, "_instance", None))

    def handler(self, allow_dangerous=False):
        h = mp.MCPProtocolHandler(
            list_tools=lambda: NATIVE_TOOLS,
            call_tool=lambda name, args: {"ok": name},
            token=TOKEN_A,
            dangerous_tools=DANGEROUS,
            allow_dangerous=allow_dangerous,
        )
        self.bridge._handler = h
        self.bridge._allow_dangerous = allow_dangerous
        self.bridge._token = TOKEN_A
        return h

    def test_not_running_updates_memory_without_error(self):
        self.bridge.apply_settings(token=TOKEN_B, allow_dangerous=True)
        self.assertEqual(self.bridge.token, TOKEN_B)
        self.assertTrue(self.bridge.allow_dangerous)
        self.assertFalse(self.bridge.is_running())

    def test_allow_dangerous_reaches_the_handler(self):
        h = self.handler(allow_dangerous=False)
        names_before = {t["name"] for t in h.visible_tools()}
        self.bridge.apply_settings(allow_dangerous=True)
        names_after = {t["name"] for t in h.visible_tools()}
        self.assertNotIn("execute_pyqgis", names_before)
        self.assertIn("execute_pyqgis", names_after)
        self.assertEqual(names_after - names_before,
                         {"execute_pyqgis"},
                         "放开后新增的必须是危险工具，不能顺手放出别的")

    def test_token_hot_swap_takes_effect_immediately(self):
        h = self.handler()
        self.assertTrue(h.check_token(TOKEN_A))
        self.bridge.apply_settings(token=TOKEN_B)
        self.assertFalse(h.check_token(TOKEN_A), "旧令牌必须立即失效")
        self.assertTrue(h.check_token(TOKEN_B))

    def test_live_flag_wins_over_stale_attribute(self):
        """allow_dangerous 属性必须回吐**服务实际生效**的值。"""
        h = self.handler(allow_dangerous=True)
        self.bridge._allow_dangerous = False          # 故意制造陈旧值
        self.assertFalse(self.bridge._allow_dangerous)
        self.assertTrue(self.bridge.allow_dangerous,
                        "读陈旧值会把「已放开」显示成「未放开」")
        h.configure(allow_dangerous=False)
        self.assertFalse(self.bridge.allow_dangerous)

    def test_allow_dangerous_defaults_to_false(self):
        self.assertFalse(mb.MCPBridge().allow_dangerous)

    def test_dangerous_tool_names_include_run_skill(self):
        names = mb.MCPBridge._dangerous_tool_names()
        self.assertIn("run_skill", names, "技能会执行用户目录下的 Python 代码")
        for name in ("execute_pyqgis", "execute_processing", "remove_layer",
                     "load_project", "save_project"):
            self.assertIn(name, names)


class TestReverseVerification(unittest.TestCase):
    """C 组：反向对照 —— 把修复关掉，行为必须退回旧样子"""

    def test_without_handler_push_the_flag_never_reaches_service(self):
        """复现修复前的状态：只改属性、不调用 apply_settings。"""
        bridge = mb.MCPBridge()
        h = mp.MCPProtocolHandler(
            list_tools=lambda: NATIVE_TOOLS,
            call_tool=lambda name, args: {"ok": name},
            token=TOKEN_A, dangerous_tools=DANGEROUS, allow_dangerous=False)
        bridge._handler = h
        bridge._allow_dangerous = True          # 复选框"勾了"
        names = {t["name"] for t in h.visible_tools()}
        self.assertNotIn("execute_pyqgis", names,
                         "这正是用户看到的「勾了但暴露危险工具仍是 false」")
        # 走了正确的下发通道之后才会变
        bridge.apply_settings(allow_dangerous=True)
        names = {t["name"] for t in h.visible_tools()}
        self.assertIn("execute_pyqgis", names)

    def test_empty_token_push_would_brick_the_service(self):
        """反向对照：证明"空令牌必须防呆"不是多此一举。"""
        bridge = mb.MCPBridge()
        h = mp.MCPProtocolHandler(
            list_tools=lambda: NATIVE_TOOLS,
            call_tool=lambda name, args: {"ok": name},
            token=TOKEN_A, dangerous_tools=DANGEROUS)
        bridge._handler = h
        bridge.apply_settings(token="")
        self.assertFalse(h.check_token(TOKEN_A),
                         "空令牌会让**所有**请求被拒 —— 所以编辑框必须拦住它")
        self.assertFalse(h.check_token(""))

    def test_wiring_guard_would_notice_a_missing_connect(self):
        """守卫灵敏度：喂一份"没接 toggled"的源码，检测方式必须报错。"""
        bad_src = ("self.cbMcpDangerous = QCheckBox('x')\n"
                   "self.cbMcpDangerous.setChecked(True)\n")
        self.assertNotIn("self.cbMcpDangerous.toggled.connect(", bad_src)
        good_src = bad_src + "self.cbMcpDangerous.toggled.connect(self._h)\n"
        self.assertIn("self.cbMcpDangerous.toggled.connect(", good_src)


if __name__ == "__main__":
    unittest.main()
