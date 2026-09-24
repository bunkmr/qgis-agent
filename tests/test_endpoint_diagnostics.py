# -*- coding: utf-8 -*-
"""endpoint_diagnostics.py 测试。

分两层：
    1. 纯函数层（normalize_endpoint / _pick_closest / format_report / 参数校验）
       —— 任何环境都能跑；
    2. HTTP 端到端层 —— 用例内部启一个 stdlib 的 mock 服务端，复现 llama.cpp 的
       四种真实失败形态，验证诊断能给出正确结论。依赖 httpx（插件运行时依赖），
       裸测试环境没装时自动跳过（真机验收用 QGIS 自带 Python 跑，那里一定有）。
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # 既支持以包方式导入，也支持 `unittest discover -s tests` 的顶层导入
    from . import support
except ImportError:
    import support  # noqa: F401

SERVER_MODEL = "Qwen3-30B-A3B-Q4_K_M.gguf"

# mock 服务端行为模式（对应 llama.cpp 的真实返回）
MODE_OK = "ok"
MODE_MODEL_MISMATCH = "model_mismatch"
MODE_CTX_EXCEEDED = "ctx_exceeded"
MODE_NO_TOOLS = "no_tools"


def _has_httpx():
    try:
        import httpx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _err(code, message, etype="invalid_request_error"):
    return code, json.dumps({"error": {"code": code, "message": message, "type": etype}})


def _make_handler(mode, n_ctx=4096, strict_v1=False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # 静音
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - 继承自基类
            path = self.path.split("?")[0]
            if path == "/v1/models" or (path == "/models" and not strict_v1):
                return self._send(200, json.dumps({
                    "object": "list",
                    "data": [{"id": SERVER_MODEL, "object": "model"}]}))
            if path in ("/props", "/v1/props"):
                return self._send(200, json.dumps({
                    "n_ctx": n_ctx, "default_generation_settings": {"n_ctx": n_ctx}}))
            return self._send(404, "<html>File Not Found</html>", "text/html")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                req = {}
            has_tools = bool(req.get("tools"))
            asked = req.get("model", "")

            if mode == MODE_MODEL_MISMATCH and asked != SERVER_MODEL:
                return self._send(*_err(404, "model '%s' not found" % asked,
                                        "not_found_error"))
            if mode == MODE_CTX_EXCEEDED:
                return self._send(*_err(
                    400, "the request exceeds the available context size "
                         "(%d tokens), try increasing it" % n_ctx))
            if mode == MODE_NO_TOOLS and has_tools:
                return self._send(*_err(
                    500, "Failed to parse chat template: this model does not support tools",
                    "server_error"))
            return self._send(200, json.dumps({
                "id": "chatcmpl-t", "object": "chat.completion", "created": 1,
                "model": SERVER_MODEL,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "OK"}}]}))

    return Handler


class MockServer:
    """进程内 mock llama.cpp（stdlib，无外部依赖）。"""

    def __init__(self, mode=MODE_OK, n_ctx=4096, strict_v1=False):
        self.mode = mode
        self.n_ctx = n_ctx
        self.strict_v1 = strict_v1
        self._server = None
        self._thread = None

    def __enter__(self):
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_handler(self.mode, self.n_ctx, self.strict_v1))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        return False

    @property
    def port(self):
        return self._server.server_address[1]

    def base(self, with_v1=True):
        return "http://127.0.0.1:%d%s" % (self.port, "/v1" if with_v1 else "")


class PureTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ed = support.import_mod("endpoint_diagnostics")


class TestNormalizeEndpoint(PureTestCase):
    def test_keeps_v1_path(self):
        primary, cands, _ = self.ed.normalize_endpoint("http://127.0.0.1:8080/v1")
        self.assertEqual(primary, "http://127.0.0.1:8080/v1")
        self.assertEqual(cands, [primary])

    def test_strips_trailing_slash(self):
        primary, _, _ = self.ed.normalize_endpoint("https://api.deepseek.com/v1/")
        self.assertEqual(primary, "https://api.deepseek.com/v1")

    def test_adds_scheme(self):
        primary, _, notes = self.ed.normalize_endpoint("127.0.0.1:8080")
        self.assertTrue(primary.startswith("http://"))
        self.assertTrue(any("前缀" in n for n in notes))

    def test_offers_v1_variant_when_absent(self):
        _primary, cands, notes = self.ed.normalize_endpoint("http://192.168.2.7:8080")
        self.assertEqual(len(cands), 2)
        self.assertTrue(cands[1].endswith("/v1"))
        self.assertTrue(any("/v1" in n for n in notes))

    def test_warns_about_localhost(self):
        _primary, _cands, notes = self.ed.normalize_endpoint("http://127.0.0.1:8080/v1")
        self.assertTrue(any("本机" in n for n in notes))

    def test_empty_endpoint(self):
        primary, cands, notes = self.ed.normalize_endpoint("")
        self.assertEqual(primary, "")
        self.assertEqual(cands, [])
        self.assertTrue(notes)

    def test_none_endpoint(self):
        self.assertEqual(self.ed.normalize_endpoint(None)[1], [])


class TestPickClosest(PureTestCase):
    def test_exact_match(self):
        self.assertEqual(self.ed._pick_closest("a", ["a", "b"]), "a")

    def test_case_insensitive(self):
        self.assertEqual(self.ed._pick_closest("QWEN3", ["qwen3"]), "qwen3")

    def test_tag_containment(self):
        """ollama 里用户常写 qwen3 而服务端是 qwen3:8b。"""
        self.assertEqual(self.ed._pick_closest("qwen3", ["qwen3:8b"]), "qwen3:8b")

    def test_separator_insensitive(self):
        self.assertEqual(self.ed._pick_closest("qwen3-30b", ["Qwen3_30B"]), "Qwen3_30B")

    def test_no_match(self):
        self.assertEqual(self.ed._pick_closest("zzz", ["a", "b"]), "")

    def test_empty_inputs(self):
        self.assertEqual(self.ed._pick_closest("", ["a"]), "")
        self.assertEqual(self.ed._pick_closest("a", []), "")


class TestEstimateHelpers(PureTestCase):
    def test_estimate_tokens(self):
        self.assertEqual(self.ed.estimate_tokens(0), 0)
        self.assertGreater(self.ed.estimate_tokens(3200), 900)

    def test_estimate_tokens_never_raises(self):
        for value in (None, "x", -5):
            with self.subTest(value=repr(value)):
                self.assertIsInstance(self.ed.estimate_tokens(value), int)

    def test_plugin_request_scale_returns_ints(self):
        chars, tokens = self.ed.plugin_request_scale()
        self.assertIsInstance(chars, int)
        self.assertIsInstance(tokens, int)


class TestDiagnoseGuards(PureTestCase):
    def _diagnose(self, **kwargs):
        params = {"provider": "Custom", "model": "m", "api_key": "k",
                  "endpoint": "http://127.0.0.1:1/v1", "timeout": 1}
        params.update(kwargs)
        return self.ed.diagnose(**params)

    def test_missing_endpoint(self):
        result = self._diagnose(endpoint="")
        self.assertFalse(result["ok"])
        self.assertTrue(result["headline"])
        self.assertTrue(any(c["level"] == self.ed.LEVEL_FAIL for c in result["checks"]))

    def test_missing_model(self):
        result = self._diagnose(model="")
        self.assertFalse(result["ok"])
        self.assertIn("模型名", result["headline"])

    def test_never_raises_on_weird_input(self):
        """诊断入口必须永远返回报告，不能把异常抛给 UI 线程。"""
        for kwargs in (
            {"endpoint": 123},
            {"model": object()},
            {"endpoint": None, "model": None, "api_key": None},
            {"endpoint": "::::"},
            {"endpoint": "http://", "model": "x"},
        ):
            with self.subTest(kwargs=list(kwargs)):
                result = self._diagnose(**kwargs)
                self.assertIn("checks", result)
                self.assertIsInstance(result["ok"], bool)

    def test_report_shape(self):
        result = self._diagnose()
        self.assertEqual(
            set(result) >= {"ok", "headline", "base_url", "provider", "model",
                            "n_ctx", "checks", "suggestions"}, True)
        for check in result["checks"]:
            self.assertIn(check["level"], (self.ed.LEVEL_OK, self.ed.LEVEL_WARN,
                                           self.ed.LEVEL_FAIL, self.ed.LEVEL_INFO))
            self.assertTrue(check["title"])

    def test_format_report_is_readable(self):
        text = self.ed.format_report(self._diagnose(endpoint=""))
        self.assertIn("结论：", text)
        self.assertIn("检查明细：", text)


@unittest.skipUnless(_has_httpx(), "需要 httpx（插件运行时依赖；裸测试环境未安装时跳过）")
class TestDiagnoseAgainstMockServer(PureTestCase):
    def diagnose(self, server, model=SERVER_MODEL, endpoint=None):
        return self.ed.diagnose("Custom", model, "sk-local",
                                endpoint or server.base(), timeout=5)

    def test_all_green(self):
        with MockServer(MODE_OK) as server:
            result = self.diagnose(server)
        self.assertTrue(result["ok"])
        self.assertEqual(result["base_url"], server.base())
        self.assertEqual(result["n_ctx"], 4096)
        self.assertIn("全部通过", result["headline"])
        self.assertFalse(result["model_mismatch"])

    def test_detects_model_name_mismatch(self):
        """llama.cpp 的经典报错：model 'qwen3' not found —— 必须给出可用的真实名字。"""
        with MockServer(MODE_MODEL_MISMATCH) as server:
            result = self.diagnose(server, model="qwen3")
        self.assertFalse(result["ok"])
        self.assertTrue(result["model_mismatch"])
        titles = [c["title"] for c in result["checks"]]
        self.assertIn("模型名不匹配", titles)
        joined = " ".join(result["suggestions"])
        self.assertIn(SERVER_MODEL, joined)

    def test_detects_context_too_small(self):
        with MockServer(MODE_CTX_EXCEEDED, n_ctx=4096) as server:
            result = self.diagnose(server)
        self.assertFalse(result["ok"])
        joined = " ".join(result["suggestions"])
        self.assertIn("4096", joined)
        self.assertIn("ctx-size", joined)

    def test_detects_missing_tool_support(self):
        """最关键的一条：纯对话通过、带 tools 失败 —— 正是「别处能用这里不行」的成因。"""
        with MockServer(MODE_NO_TOOLS) as server:
            result = self.diagnose(server)
        self.assertFalse(result["ok"])
        titles = [c["title"] for c in result["checks"]]
        self.assertIn("对话接口可用", titles)      # 纯对话是通的
        self.assertIn("工具调用不可用", titles)     # 但工具不行
        joined = " ".join(result["suggestions"])
        self.assertIn("--jinja", joined)

    def test_offers_v1_variant_when_missing(self):
        """只填了 host:port 时：能通就沿用，不通就自动补 /v1 并提示改配置。

        网关（而非 llama.cpp）通常只认 /v1，因此这里让 mock 只服务 /v1 路径。
        """
        with MockServer(MODE_OK, strict_v1=True) as server:
            result = self.diagnose(server, endpoint=server.base(with_v1=False))
        self.assertTrue(result["ok"])
        self.assertTrue(result["base_url"].endswith("/v1"))
        self.assertTrue(any("已自动修正地址" == c["title"] for c in result["checks"]))
        self.assertTrue(any("改成" in s for s in result["suggestions"]))

    def test_keeps_user_endpoint_when_it_works(self):
        """llama.cpp 同时服务两种路径 —— 用户填的那个能用就别动它。"""
        with MockServer(MODE_OK) as server:
            base = server.base(with_v1=False)
            result = self.diagnose(server, endpoint=base)
        self.assertTrue(result["ok"])
        self.assertEqual(result["base_url"], base)

    def test_unreachable_port(self):
        result = self.ed.diagnose("Custom", SERVER_MODEL, "k",
                                  "http://127.0.0.1:9/v1", timeout=2)
        self.assertFalse(result["ok"])
        titles = [c["title"] for c in result["checks"]]
        self.assertIn("服务不可达", titles)
        # 服务都不可达了，就不该再报「对话接口不可用」把报告弄乱
        self.assertNotIn("对话接口不可用", titles)


if __name__ == "__main__":
    unittest.main()
