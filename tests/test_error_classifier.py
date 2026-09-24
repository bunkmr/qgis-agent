# -*- coding: utf-8 -*-
"""error_classifier.py 测试：错误分级正确性 + classify_error 绝不抛异常

该模块声明"纯 Python、只依赖 re/typing"，因此可在裸环境直接 import。
"""

import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

EXPECTED_KEYS = {"category", "title", "message", "hint", "action", "retryable"}


class ClassifierTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ec = support.import_mod("error_classifier")

    def classify(self, text):
        result = self.ec.classify_error(text)
        self.assertEqual(set(result), EXPECTED_KEYS)
        self.assertIsInstance(result["category"], str)
        self.assertIsInstance(result["message"], str)
        self.assertIsInstance(result["hint"], str)
        self.assertIsInstance(result["retryable"], bool)
        self.assertIn(result["action"], (None, "open_settings", "retry", "switch_model"))
        return result


class TestCategoryRouting(ClassifierTestCase):
    CASES = [
        # (错误文本, 期望分类)
        ("Error code: 401 - Invalid API key provided", "auth"),
        ("HTTP 401 Unauthorized", "auth"),
        ("Incorrect API key", "auth"),
        ("鉴权失败，请检查密钥", "auth"),
        ("api key is missing", "auth"),
        ("Error code: 403 Forbidden", "permission"),
        ("Permission denied for this resource", "permission"),
        ("访问被拒绝", "permission"),
        ("Error code: 429 rate limit reached", "rate_limit"),
        ("Too many requests, please slow down", "rate_limit"),
        ("Error: quota exceeded for this account", "rate_limit"),
        ("insufficient_quota on the free tier", "rate_limit"),
        ("配额已用尽", "rate_limit"),
        ("HTTPSConnectionPool: Read timed out. (read timeout=600)", "timeout"),
        ("The request timed out after 30s", "timeout"),
        ("deadline exceeded", "timeout"),
        ("Connection refused by the server", "connection"),
        ("Temporary failure in name resolution", "connection"),
        ("Max retries exceeded with url: /v1/chat/completions", "connection"),
        ("无法连接到服务器", "connection"),
        ("httpcore.ConnectError during start_tls: [Errno 54] Connection reset by peer", "tls_blocked"),
        ("curl: SSL_ERROR_SYSCALL after ClientHello", "tls_blocked"),
        ("This model's maximum context length is 8192 tokens", "context_length"),
        ("Please reduce the length of the messages", "context_length"),
        ("上下文过长，请精简历史", "context_length"),
        ("The model `gpt-4o-2026` does not exist", "model"),
        ("Model not found: my-model", "model"),
        ("unknown model name", "model"),
        ("This model does not support tools", "tool"),
        ("function calling is not enabled", "tool"),
        ("工具调用失败", "tool"),
        # ── 本地推理服务（llama.cpp 等）的真实措辞 ──
        # 下面四条都是对着 mock llama.cpp 实测出来的原文：v2.4.2 之前**全部**落到
        # unknown，用户在界面上只能看到「错误原因无法自动识别」。
        ("Error code: 404 - {'error': {'code': 404, 'message': \"model 'qwen3' not found\","
         " 'type': 'not_found_error'}}", "model"),
        ("Error code: 500 - {'error': {'code': 500, 'message': 'model is not loaded',"
         " 'type': 'server_error'}}", "model"),
        ("Error code: 400 - {'error': {'code': 400, 'message': 'the request exceeds the"
         " available context size (4096 tokens), try increasing it',"
         " 'type': 'invalid_request_error'}}", "context_length"),
        ("Error code: 404 - {'error': {'message': 'File Not Found'}}", "endpoint"),
        # 本地服务填了空 Key（OpenAI SDK 自身的报错文案）
        ("OpenAIError: The api_key client option must be set either by passing api_key"
         " to the client or by setting the OPENAI_API_KEY environment variable", "auth"),
        ("Error code: 502 - {'error': {'message': 'bad gateway'}}", "server"),
        # ── chat template / 消息格式（2026-09-24 用户现场）──
        # 用户配置本地 llama.cpp 后每次对话都报「模型服务内部错误 HTTP 500」，
        # 而这个模型在别的客户端一切正常。原文是 Jinja 模板拒绝第二条 system。
        # ⚠️ 注意这条报错里**含 "HTTP 500"**，所以 template 规则必须排在 server
        #    之前，否则会被 `\b50[0-9]\b` 抢走，然后给出「模型未加载 / 显存不足」
        #    这种完全不对路的建议 —— 这正是用户当时的现场。
        ("While executing CallExpression at line 85, column 32 in source: ... first %}"
         " {{- raise_exception('System message must be at the beginning.') }} ^"
         " Error: Jinja Exception: System message must be at the beginning.",
         "template"),
        ("Error: Jinja Exception: unknown filter 'x'", "template"),
        ("Failed to parse chat template: invalid role", "template"),
    ]

    def test_cases_route_to_expected_category(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(self.classify(text)["category"], expected)

    def test_real_openai_quota_wording(self):
        """OpenAI 真实文案 "You exceeded your current quota"。

        该用例曾是 expectedFailure：旧正则要求 quota 紧邻 exceed
        （`quota[\\s_-]*exceed`），而真实报文顺序相反（exceeded ... quota），
        最终落到 unknown，用户只看到「错误原因无法自动识别」。
        v2.4.2 补上 `exceed\\w*\\s+(your\\s+)?(current\\s+)?quota` 后转为正常守卫。
        """
        text = "You exceeded your current quota, please check your plan and billing details"
        self.assertEqual(self.classify(text)["category"], "rate_limit")

    def test_unknown_category_for_unmatched_text(self):
        self.assertEqual(self.classify("something totally unexpected")["category"], "unknown")

    def test_unknown_titles_are_chinese(self):
        result = self.classify("boom")
        self.assertEqual(result["title"], "请求失败")
        self.assertFalse(result["retryable"])

    def test_auth_more_specific_than_connection(self):
        """401 与 connection 关键词同时出现时，应优先判为 auth"""
        result = self.classify("401 unauthorized, connection refused")
        self.assertEqual(result["category"], "auth")


class TestRetryableAndAction(ClassifierTestCase):
    def test_retryable_categories(self):
        for text, expected in [
            ("429 rate limit", True),
            ("read timed out", True),
            ("connection refused", True),
            ("401 invalid api key", False),
            ("403 forbidden", False),
            ("maximum context length exceeded", False),
            ("model not found", False),
            ("does not support tools", False),
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.classify(text)["retryable"], expected)

    def test_action_mapping(self):
        self.assertEqual(self.classify("401 unauthorized")["action"], "open_settings")
        self.assertEqual(self.classify("429 rate limit")["action"], "retry")
        self.assertEqual(self.classify("read timed out")["action"], "switch_model")
        self.assertEqual(self.classify("model not found")["action"], "open_settings")
        self.assertEqual(self.classify("bad gateway")["action"], "retry")
        self.assertIsNone(self.classify("context length exceeded")["action"])

    def test_local_server_failures_are_retryable_as_expected(self):
        """本地推理服务的失败里，只有 5xx 值得直接重试。"""
        self.assertTrue(self.classify("Error code: 503 - server_error")["retryable"])
        self.assertFalse(self.classify("model is not loaded")["retryable"])
        self.assertFalse(self.classify("available context size exceeded")["retryable"])


class TestSummarizeError(ClassifierTestCase):
    """原始报文提炼：界面上要展示的是服务端那一句，而不是整坨 SDK 包装。"""

    def test_extracts_json_message(self):
        text = ("Error code: 404 - {'error': {'code': 404, "
                "'message': \"model 'qwen3' not found\", 'type': 'not_found_error'}}")
        self.assertEqual(self.ec.summarize_error(text),
                         "HTTP 404 · model 'qwen3' not found")

    def test_extracts_message_with_double_quotes(self):
        text = 'Error code: 500 - {"error": {"message": "model is not loaded"}}'
        self.assertEqual(self.ec.summarize_error(text), "HTTP 500 · model is not loaded")

    def test_falls_back_to_cleaned_text(self):
        """没有 message 字段时退化为「抹掉结构符号后的全文」，但不能是空串。"""
        out = self.ec.summarize_error("openai.APIConnectionError: Connection error.")
        self.assertIn("APIConnectionError", out)
        self.assertNotIn("{", out)

    def test_redacts_secrets(self):
        """错误报文经常把 Authorization 头一起吐回来，展示前必须脱敏。"""
        out = self.ec.summarize_error("Unauthorized: Bearer sk-abcdef1234567890")
        self.assertNotIn("sk-abcdef1234567890", out)
        self.assertIn("[已隐去]", out)

    def test_truncates_long_text(self):
        out = self.ec.summarize_error("x" * 5000, max_len=100)
        self.assertLessEqual(len(out), 101)
        self.assertTrue(out.endswith("…"))

    def test_never_raises_on_weird_input(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("boom")

        for value in (None, 123, [], {}, object(), Bad(), "", "   \n  "):
            with self.subTest(value=repr(value)[:30]):
                self.assertIsInstance(self.ec.summarize_error(value), str)

    def test_extract_status_code_variants(self):
        self.assertEqual(self.ec.extract_status_code("Error code: 404 - x"), 404)
        self.assertEqual(self.ec.extract_status_code("HTTP 503 Service Unavailable"), 503)
        self.assertIsNone(self.ec.extract_status_code("no code here"))
        # 端口号 / 上下文长度不能被误当成状态码
        self.assertIsNone(self.ec.extract_status_code("connect to 127.0.0.1:5000"))
        self.assertIsNone(self.ec.extract_status_code("near 4096 tokens"))
        for value in (None, 123, object()):
            with self.subTest(value=repr(value)[:20]):
                self.assertIsNone(self.ec.extract_status_code(value))


class TestCloudflareHint(ClassifierTestCase):
    def test_cloudflare_adds_gateway_hint(self):
        result = self.classify("Error 1020: Your request was blocked by Cloudflare")
        self.assertEqual(result["category"], "permission")
        self.assertIn("UA 白名单是否放行了本插件的请求", result["hint"])

    def test_plain_permission_has_no_extra_hint(self):
        result = self.classify("403 Forbidden")
        self.assertNotIn("UA 白名单是否放行了本插件的请求", result["hint"])


class TestNeverRaises(ClassifierTestCase):
    def test_empty_string(self):
        self.assertEqual(self.classify("")["category"], "unknown")

    def test_whitespace_only(self):
        self.assertEqual(self.classify("   \n\t  ")["category"], "unknown")

    def test_none_input(self):
        self.assertEqual(self.classify(None)["category"], "unknown")

    def test_non_string_inputs(self):
        for value in (123, 4.5, [], {}, object()):
            with self.subTest(value=repr(value)):
                self.assertEqual(self.classify(value)["category"], "unknown")

    def test_object_with_raising_str(self):
        class Bad:
            def __str__(self):
                raise RuntimeError("boom")

        self.assertEqual(self.classify(Bad())["category"], "unknown")

    def test_very_long_input(self):
        text = "error " * 200000 + "timeout"
        self.assertEqual(self.classify(text)["category"], "timeout")

    def test_unicode_and_control_characters(self):
        text = "错误\x00\x1b[31m 401 unauthorized"
        self.assertEqual(self.classify(text)["category"], "auth")


class TestConstants(ClassifierTestCase):
    def test_category_constants(self):
        self.assertEqual(self.ec.CATEGORY_AUTH, "auth")
        self.assertEqual(self.ec.CATEGORY_CONTEXT_LENGTH, "context_length")
        self.assertEqual(self.ec.CATEGORY_ENDPOINT, "endpoint")
        self.assertEqual(self.ec.CATEGORY_SERVER, "server")
        self.assertEqual(self.ec.CATEGORY_TEMPLATE, "template")
        self.assertEqual(self.ec.CATEGORY_UNKNOWN, "unknown")

    def test_rule_order_is_specific_before_generic(self):
        """规则顺序即优先级：`model 'x' not found` 必须由 model 接住，
        不能被后面的 endpoint（裸 404 / not found）抢走。"""
        categories = [category for category, *_ in self.ec._RULES]
        self.assertLess(categories.index(self.ec.CATEGORY_MODEL),
                        categories.index(self.ec.CATEGORY_ENDPOINT))
        self.assertLess(categories.index(self.ec.CATEGORY_MODEL),
                        categories.index(self.ec.CATEGORY_SERVER))

    def test_template_rule_precedes_server(self):
        """template 必须排在 server 之前。

        Jinja 模板报错一律伴随 "HTTP 500"，而 server 规则里有 `\\b50[0-9]\\b`。
        顺序一旦反了，用户看到的建议就会变成「模型未加载 / 显存不足」——
        与真实成因毫无关系（用户现场实测）。"""
        categories = [category for category, *_ in self.ec._RULES]
        self.assertLess(categories.index(self.ec.CATEGORY_TEMPLATE),
                        categories.index(self.ec.CATEGORY_SERVER))

    def test_system_position_error_is_not_swallowed_by_server(self):
        """端到端锁死：把 system 位置报错喂进来，绝不能落到 server。"""
        raw = ("HTTP 500 · While executing CallExpression at line 85, column 32 in source:"
               " ... first %} {{- raise_exception('System message must be at the"
               " beginning.') }} ^ Error: Jinja Exception: System message must be at"
               " the beginning.")
        info = self.ec.classify_error(raw)
        self.assertEqual(info.get("category"), self.ec.CATEGORY_TEMPLATE)
        self.assertIn("system", (info.get("hint") or "").lower())

    def test_every_rule_has_compiled_patterns(self):
        self.assertEqual(len(self.ec._COMPILED_RULES), len(self.ec._RULES))
        for category, patterns, *_ in self.ec._COMPILED_RULES:
            with self.subTest(category=category):
                self.assertGreater(len(patterns), 0)


if __name__ == "__main__":
    unittest.main()
