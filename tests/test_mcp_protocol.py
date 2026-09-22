# -*- coding: utf-8 -*-
"""mcp_protocol.py 测试：令牌校验、工具可见性、行协议兜底、会话文件读写。

该模块声明纯标准库（不依赖 Qt / QGIS），因此可在裸环境直接 import。
"""

import json
import os
import shutil
import unittest

try:  # 既支持以包方式导入，也支持 `unittest discover -s tests` 的顶层模块导入
    from . import support
except ImportError:
    import support  # noqa: F401

mp = support.import_mod("mcp_protocol")

TOKEN = "test-token-0123456789abcdef"

NATIVE_TOOLS = [
    {"name": "get_qgis_info", "description": "取状态",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_layer_features", "description": "取要素",
     "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}},
                    "required": ["layer_id_or_name"]}},
    {"name": "execute_pyqgis", "description": "执行代码",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
]

DANGEROUS = {"execute_pyqgis", "execute_processing", "remove_layer",
             "load_project", "save_project"}


class _Recorder(object):
    """记录 call_tool 收到的调用，并返回预设结果。"""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result if result is not None else {"ok": "done"}
        self._raises = raises

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        if self._raises is not None:
            raise self._raises
        return self._result


def make_handler(token=TOKEN, allow_dangerous=False, recorder=None):
    return mp.MCPProtocolHandler(
        list_tools=lambda: NATIVE_TOOLS,
        call_tool=recorder or _Recorder(),
        token=token,
        dangerous_tools=DANGEROUS,
        allow_dangerous=allow_dangerous,
    )


def req(method, token=TOKEN, params=None):
    payload = {"method": method, "token": token}
    if params is not None:
        payload["params"] = params
    return payload


class AuthTestCase(unittest.TestCase):
    def test_rejects_when_no_token_configured(self):
        handler = make_handler(token="")
        resp = handler.handle_request(req("ping"))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], mp.ERR_UNAUTHORIZED)

    def test_rejects_wrong_token(self):
        handler = make_handler()
        resp = handler.handle_request(req("ping", token="nope"))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], mp.ERR_UNAUTHORIZED)

    def test_rejects_non_string_token(self):
        handler = make_handler()
        for bad in (None, 12345, ["x"], {"a": 1}):
            resp = handler.handle_request({"method": "ping", "token": bad})
            self.assertFalse(resp["ok"], bad)
            self.assertEqual(resp["code"], mp.ERR_UNAUTHORIZED, bad)

    def test_accepts_correct_token(self):
        resp = make_handler().handle_request(req("ping"))
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["result"]["pong"])

    def test_check_token_uses_constant_time_compare(self):
        handler = make_handler()
        self.assertTrue(handler.check_token(TOKEN))
        self.assertFalse(handler.check_token(TOKEN[:-1]))


class ToolsVisibilityTestCase(unittest.TestCase):
    def test_dangerous_hidden_by_default(self):
        resp = make_handler().handle_request(req("list_tools"))
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("get_qgis_info", names)
        self.assertNotIn("execute_pyqgis", names)

    def test_dangerous_visible_when_allowed(self):
        resp = make_handler(allow_dangerous=True).handle_request(req("list_tools"))
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("execute_pyqgis", names)

    def test_tools_slash_syntax_also_supported(self):
        resp = make_handler().handle_request(req("tools/list"))
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["result"]["tools"])

    def test_parameters_mapped_to_input_schema(self):
        resp = make_handler().handle_request(req("list_tools"))
        tool = [t for t in resp["result"]["tools"] if t["name"] == "get_layer_features"][0]
        self.assertIn("inputSchema", tool)
        self.assertNotIn("parameters", tool)
        self.assertEqual(tool["inputSchema"]["required"], ["layer_id_or_name"])

    def test_missing_parameters_gets_default_schema(self):
        converted = mp.to_mcp_tool({"name": "x", "description": "y"})
        self.assertEqual(converted["inputSchema"],
                         {"type": "object", "properties": {}, "required": []})
        self.assertEqual(converted["name"], "x")


class CallToolTestCase(unittest.TestCase):
    def test_dangerous_call_refused_by_default(self):
        recorder = _Recorder()
        handler = make_handler(recorder=recorder)
        resp = handler.handle_request(req("call_tool", params={
            "name": "execute_pyqgis", "arguments": {"code": "print(1)"}}))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], mp.ERR_TOOL_NOT_ALLOWED)
        self.assertEqual(recorder.calls, [])  # 拒绝发生在调用之前

    def test_dangerous_call_allowed_when_enabled(self):
        recorder = _Recorder(result={"output": "ok"})
        handler = make_handler(allow_dangerous=True, recorder=recorder)
        resp = handler.handle_request(req("call_tool", params={
            "name": "execute_pyqgis", "arguments": {"code": "print(1)"}}))
        self.assertTrue(resp["ok"])
        self.assertEqual(recorder.calls, [("execute_pyqgis", {"code": "print(1)"})])

    def test_normal_call_passes_arguments(self):
        recorder = _Recorder(result={"layers": []})
        handler = make_handler(recorder=recorder)
        resp = handler.handle_request(req("call_tool", params={
            "name": "get_layer_features", "arguments": {"layer_id_or_name": "roads", "limit": 5}}))
        self.assertTrue(resp["ok"])
        self.assertEqual(recorder.calls[0][1]["limit"], 5)

    def test_missing_name_is_bad_request(self):
        resp = make_handler().handle_request(req("call_tool", params={}))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], mp.ERR_BAD_REQUEST)

    def test_non_string_name_is_bad_request(self):
        recorder = _Recorder()
        handler = make_handler(recorder=recorder)
        for bad in (["get_qgis_info"], {"a": 1}, 12345, True):
            resp = handler.handle_request(req("call_tool", params={"name": bad}))
            self.assertFalse(resp["ok"], bad)
            self.assertEqual(resp["code"], mp.ERR_BAD_REQUEST, bad)
        self.assertEqual(recorder.calls, [])  # 从未落到真正的执行入口

    def test_huge_token_is_rejected_without_crashing(self):
        handler = make_handler()
        resp = handler.handle_request(req("ping", token="x" * (mp.MAX_LINE_BYTES - 1)))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], mp.ERR_UNAUTHORIZED)

    def test_non_dict_arguments_treated_as_empty(self):
        recorder = _Recorder()
        handler = make_handler(recorder=recorder)
        handler.handle_request(req("call_tool", params={"name": "get_qgis_info",
                                                       "arguments": "not-a-dict"}))
        self.assertEqual(recorder.calls, [("get_qgis_info", {})])

    def test_tool_exception_becomes_structured_error(self):
        recorder = _Recorder(raises=RuntimeError("boom"))
        handler = make_handler(recorder=recorder)
        resp = handler.handle_request(req("call_tool", params={"name": "get_qgis_info"}))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["code"], mp.ERR_INTERNAL)
        self.assertIn("boom", resp["error"])

    def test_non_dict_result_wrapped(self):
        recorder = _Recorder(result="plain string")
        handler = make_handler(recorder=recorder)
        resp = handler.handle_request(req("call_tool", params={"name": "get_qgis_info"}))
        self.assertEqual(resp["result"], {"result": "plain string"})


class HandleLineTestCase(unittest.TestCase):
    def test_bad_json_returns_bad_request(self):
        payload = json.loads(make_handler().handle_line("{not json"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], mp.ERR_BAD_REQUEST)

    def test_empty_line_returns_none(self):
        self.assertIsNone(make_handler().handle_line("   \n"))

    def test_non_object_payload_rejected(self):
        payload = json.loads(make_handler().handle_line("[1,2,3]"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], mp.ERR_BAD_REQUEST)

    def test_oversized_line_rejected(self):
        huge = b"x" * (mp.MAX_LINE_BYTES + 1)
        payload = json.loads(make_handler().handle_line(huge))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], mp.ERR_BAD_REQUEST)

    def test_bytes_line_decoded(self):
        payload = json.loads(make_handler().handle_line(json.dumps(req("ping")).encode("utf-8")))
        self.assertTrue(payload["ok"])

    def test_invalid_utf8_rejected(self):
        payload = json.loads(make_handler().handle_line(b"\xff\xfe\x00"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], mp.ERR_BAD_REQUEST)

    def test_unknown_method(self):
        payload = json.loads(make_handler().handle_line(json.dumps(req("no_such_method"))))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], mp.ERR_UNKNOWN_METHOD)

    def test_response_is_single_line(self):
        line = make_handler().handle_line(json.dumps(req("list_tools")))
        self.assertNotIn("\n", line)


class ConfigureTestCase(unittest.TestCase):
    def test_configure_updates_token(self):
        handler = make_handler(token="old")
        self.assertTrue(handler.check_token("old"))
        handler.configure(token="new")
        self.assertFalse(handler.check_token("old"))
        self.assertTrue(handler.check_token("new"))

    def test_configure_clearing_token_locks_everything(self):
        handler = make_handler()
        handler.configure(token="")
        self.assertFalse(handler.handle_request(req("ping"))["ok"])

    def test_configure_toggles_dangerous_visibility(self):
        handler = make_handler()
        handler.configure(allow_dangerous=True)
        resp = handler.handle_request(req("list_tools"))
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("execute_pyqgis", names)


class SessionFileTestCase(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self.home = support.temp_home()
        os.environ["HOME"] = self.home

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def test_write_then_read_roundtrip(self):
        path = mp.session_file_path()
        self.assertTrue(path.startswith(self.home))
        mp.write_session_file(9999, "tok-abc", extra={"host": "127.0.0.1"})
        data = mp.read_session_file()
        self.assertEqual(data["port"], 9999)
        self.assertEqual(data["token"], "tok-abc")
        self.assertEqual(data["host"], "127.0.0.1")
        self.assertEqual(data["protocol"], mp.PROTOCOL_VERSION)

    def test_clear_removes_file(self):
        mp.write_session_file(9999, "tok")
        self.assertTrue(mp.clear_session_file())
        self.assertEqual(mp.read_session_file(), {})
        # 重复删除不抛异常
        self.assertFalse(mp.clear_session_file())

    def test_read_missing_returns_empty_dict(self):
        self.assertEqual(mp.read_session_file(), {})

    def test_read_corrupted_returns_empty_dict(self):
        path = mp.session_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        self.assertEqual(mp.read_session_file(), {})

    def test_write_does_not_leave_tmp_file(self):
        mp.write_session_file(1, "t")
        leftovers = [n for n in os.listdir(os.path.join(self.home, mp.SESSION_DIRNAME))
                     if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    @unittest.skipIf(os.name == "nt", "Windows 不支持 posix 目录权限")
    def test_session_dir_is_owner_only(self):
        mp.write_session_file(1, "t")
        directory = os.path.join(self.home, mp.SESSION_DIRNAME)
        mode = oct(os.stat(directory).st_mode & 0o777)
        self.assertEqual(mode, oct(0o700), mode)

    @unittest.skipIf(os.name == "nt", "Windows 不支持 posix 文件权限")
    def test_session_file_is_owner_only(self):
        path = mp.write_session_file(1, "t")
        mode = oct(os.stat(path).st_mode & 0o777)
        self.assertEqual(mode, oct(0o600), mode)


if __name__ == "__main__":
    unittest.main()
