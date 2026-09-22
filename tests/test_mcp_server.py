# -*- coding: utf-8 -*-
"""mcp_server/qgis_agent_mcp_server.py 测试。

用一个**真实的 TCP socket 假桥接服务**替代 QGIS 插件，验证：
- MCP 协议握手（initialize）与协议版本协商；
- tools/list 透传插件侧工具清单（含 inputSchema 转换后的结果）；
- tools/call 成功 / 工具报错 / 桥接不可达 三种路径；
- JSON-RPC 错误码与「通知无响应」；
- stdout 严格只输出协议（一行一条 JSON）。

不依赖 Qt / QGIS / 网络外网。
"""

import importlib.util
import io
import json
import os
import socket
import threading
import time
import unittest

try:
    from . import support
except ImportError:
    import support


def _load_server_module():
    path = os.path.join(support.PROJECT_ROOT, "mcp_server", "qgis_agent_mcp_server.py")
    spec = importlib.util.spec_from_file_location("qgis_agent_mcp_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server_mod = _load_server_module()

TOKEN = "tok"
MCP_TOOLS = [
    {"name": "get_qgis_info", "description": "取状态",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_layer_features", "description": "取要素",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
]


class _FakeBridge(object):
    """假桥接服务：真实监听 127.0.0.1 的一个随机端口，实现插件侧行协议。"""

    def __init__(self, tools=None, call_result=None, call_error=None, token=TOKEN):
        self.tools = MCP_TOOLS if tools is None else tools
        self.call_result = {"layers": ["a"]} if call_result is None else call_result
        self.call_error = call_error
        self.token = token
        self.requests = []
        self._stop = False
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.3)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            with conn.makefile("rwb") as stream:
                for raw in stream:
                    if not raw.strip():
                        continue
                    try:
                        request = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        continue
                    self.requests.append(request)
                    response = self._handle(request)
                    stream.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                    stream.flush()
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle(self, request):
        if request.get("token") != self.token:
            return {"ok": False, "code": "unauthorized", "error": "访问令牌无效。"}
        method = request.get("method")
        if method == "ping":
            return {"ok": True, "result": {"pong": True, "qgis_version": "4.2.1",
                                           "allow_dangerous": False}}
        if method == "list_tools":
            return {"ok": True, "result": {"tools": self.tools}}
        if method == "call_tool":
            if self.call_error:
                return {"ok": False, "code": "tool_not_allowed", "error": self.call_error}
            return {"ok": True, "result": self.call_result}
        return {"ok": False, "code": "unknown_method", "error": "未知方法"}

    def close(self):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)
        time.sleep(0.05)


class _StdoutCapture(object):
    """把 sys.stdout 换成带 .buffer 的假对象，捕获协议输出。"""

    def __init__(self):
        self.buffer = io.BytesIO()

    def messages(self):
        text = self.buffer.getvalue().decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def write(self, data):  # 兜底：有代码直接往 stdout 写字符串时也不炸
        if isinstance(data, str):
            self.buffer.write(data.encode("utf-8"))

    def flush(self):
        pass


class _ServerHarness(unittest.TestCase):
    def setUp(self):
        self.bridge = _FakeBridge()
        self.server = server_mod.MCPServer()
        self.server.bridge = server_mod.BridgeClient(
            host="127.0.0.1", port=self.bridge.port, token=TOKEN)
        self.capture = _StdoutCapture()
        self._old_stdout = os.sys.stdout
        os.sys.stdout = self.capture

    def tearDown(self):
        os.sys.stdout = self._old_stdout
        self.server.bridge.close()
        self.bridge.close()

    def send(self, method, msg_id=1, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if msg_id is not None:
            message["id"] = msg_id
        if params is not None:
            message["params"] = params
        self.server.handle(message)
        messages = self.capture.messages()
        return messages[-1] if messages else None


class InitializeTestCase(_ServerHarness):
    def test_initialize_returns_capabilities(self):
        resp = self.send("initialize", params={"protocolVersion": "2024-11-05",
                                               "capabilities": {}})
        self.assertEqual(resp["id"], 1)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], server_mod.SERVER_NAME)
        self.assertIn("instructions", result)

    def test_initialize_echoes_supported_newer_version(self):
        resp = self.send("initialize", params={"protocolVersion": "2025-06-18"})
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_falls_back_for_unknown_version(self):
        resp = self.send("initialize", params={"protocolVersion": "1999-01-01"})
        self.assertEqual(resp["result"]["protocolVersion"],
                         server_mod.FALLBACK_PROTOCOL_VERSION)

    def test_initialize_without_version_uses_fallback(self):
        resp = self.send("initialize", params={})
        self.assertEqual(resp["result"]["protocolVersion"],
                         server_mod.FALLBACK_PROTOCOL_VERSION)


class ToolsListTestCase(_ServerHarness):
    def test_tools_list_passes_bridge_tools_through(self):
        resp = self.send("tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(names, ["get_qgis_info", "get_layer_features"])

    def test_tools_list_asks_bridge_every_time(self):
        self.send("tools/list")
        self.send("tools/list", msg_id=2)
        methods = [r["method"] for r in self.bridge.requests]
        self.assertEqual(methods.count("list_tools"), 2)

    def test_bridge_unreachable_returns_jsonrpc_error_with_hint(self):
        self.bridge.close()
        resp = self.send("tools/list")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32000)
        self.assertIn("QGIS", resp["error"]["message"])

    def test_bridge_rejection_returns_error(self):
        self.bridge.token = "other"
        resp = self.send("tools/list")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32000)
        self.assertIn("令牌无效", resp["error"]["message"])


class ToolsCallTestCase(_ServerHarness):
    def test_successful_call_returns_text_content(self):
        resp = self.send("tools/call", params={"name": "get_qgis_info", "arguments": {}})
        result = resp["result"]
        self.assertNotIn("isError", result)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertIn("layers", result["content"][0]["text"])

    def test_arguments_forwarded_to_bridge(self):
        self.send("tools/call", params={"name": "get_layer_features",
                                        "arguments": {"limit": 7}})
        call = [r for r in self.bridge.requests if r["method"] == "call_tool"][0]
        self.assertEqual(call["params"]["name"], "get_layer_features")
        self.assertEqual(call["params"]["arguments"]["limit"], 7)

    def test_missing_arguments_defaults_to_empty_dict(self):
        self.send("tools/call", params={"name": "get_qgis_info"})
        call = [r for r in self.bridge.requests if r["method"] == "call_tool"][0]
        self.assertEqual(call["params"]["arguments"], {})

    def test_missing_name_is_invalid_params(self):
        resp = self.send("tools/call", params={})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_tool_returning_error_marks_is_error(self):
        self.bridge.call_result = {"error": "图层不存在"}
        resp = self.send("tools/call", params={"name": "get_qgis_info"})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("图层不存在", resp["result"]["content"][0]["text"])

    def test_unrelated_truthy_key_does_not_mark_is_error(self):
        # 只有 error 字段的「真值」才算失败；兄弟字段（如 error_count）不能误判
        self.bridge.call_result = {"error_count": 3, "warning": "部分要素被跳过"}
        resp = self.send("tools/call", params={"name": "get_qgis_info"})
        self.assertNotIn("isError", resp["result"])

    def test_empty_error_string_does_not_mark_is_error(self):
        self.bridge.call_result = {"error": ""}
        resp = self.send("tools/call", params={"name": "get_qgis_info"})
        self.assertNotIn("isError", resp["result"])

    def test_non_dict_argument_for_name_is_rejected(self):
        resp = self.send("tools/call", params={"name": ["get_qgis_info"]})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_bridge_rejection_is_soft_error_not_jsonrpc_error(self):
        self.bridge.call_error = "工具 execute_pyqgis 未获授权"
        resp = self.send("tools/call", params={"name": "execute_pyqgis"})
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("未获授权", resp["result"]["content"][0]["text"])

    def test_bridge_down_returns_soft_error_with_hint(self):
        self.bridge.close()
        resp = self.send("tools/call", params={"name": "get_qgis_info"})
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["isError"])
        text = resp["result"]["content"][0]["text"]
        self.assertIn("无法连接", text)
        self.assertIn("启动", text)

    def test_long_result_is_truncated(self):
        self.bridge.call_result = {"blob": "x" * (server_mod.MAX_RESULT_CHARS + 5000)}
        resp = self.send("tools/call", params={"name": "get_qgis_info"})
        text = resp["result"]["content"][0]["text"]
        self.assertLessEqual(len(text), server_mod.MAX_RESULT_CHARS + 40)
        self.assertIn("已截断", text)


class ProtocolEdgeCaseTestCase(_ServerHarness):
    def test_non_object_message_is_invalid_request(self):
        # serve() 解析失败走 -32700，这里验证 handle() 对非对象载荷的兜底（-32600）
        self.server.handle(None)
        resp = self.capture.messages()[-1]
        self.assertEqual(resp["error"]["code"], -32600)

    def test_unknown_method(self):
        resp = self.send("no/such/method")
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notification_produces_no_output(self):
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertTrue(self.server.initialized)
        self.assertEqual(self.capture.messages(), [])

    def test_ping_notification_produces_no_output(self):
        self.server.handle({"jsonrpc": "2.0", "method": "ping"})
        self.assertEqual(self.capture.messages(), [])

    def test_ping_request_returns_empty_result(self):
        resp = self.send("ping")
        self.assertEqual(resp["result"], {})

    def test_output_is_one_json_per_line(self):
        self.send("tools/list")
        raw = self.capture.buffer.getvalue().decode("utf-8")
        lines = [l for l in raw.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        json.loads(lines[0])


class BridgeClientConfigTestCase(unittest.TestCase):
    def test_env_vars_override(self):
        old = {k: os.environ.get(k) for k in
               (server_mod.ENV_PORT, server_mod.ENV_TOKEN)}
        try:
            os.environ[server_mod.ENV_PORT] = "12345"
            os.environ[server_mod.ENV_TOKEN] = "env-token"
            client = server_mod.BridgeClient()
            self.assertEqual(client.port, 12345)
            self.assertEqual(client.token, "env-token")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_default_port_when_nothing_configured(self):
        old_home = os.environ.get("HOME")
        old_port = os.environ.pop(server_mod.ENV_PORT, None)
        old_token = os.environ.pop(server_mod.ENV_TOKEN, None)
        home = support.temp_home()
        os.environ["HOME"] = home
        try:
            client = server_mod.BridgeClient()
            self.assertEqual(client.port, server_mod.DEFAULT_PORT)
            self.assertEqual(client.token, "")
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            if old_port is not None:
                os.environ[server_mod.ENV_PORT] = old_port
            if old_token is not None:
                os.environ[server_mod.ENV_TOKEN] = old_token


if __name__ == "__main__":
    unittest.main()
