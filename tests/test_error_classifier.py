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
    ]

    def test_cases_route_to_expected_category(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(self.classify(text)["category"], expected)

    @unittest.expectedFailure
    def test_real_openai_quota_wording(self):
        """已知缺口：OpenAI 真实文案 "You exceeded your current quota" 未被识别

        error_classifier.py:87 的正则要求 quota 紧邻 exceed（quota[\\s_-]*exceed），
        而真实报文是 "exceeded ... quota"，顺序相反，最终落到 unknown，
        用户只会看到"错误原因无法自动识别"。
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
        self.assertIsNone(self.classify("context length exceeded")["action"])


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
        self.assertEqual(self.ec.CATEGORY_UNKNOWN, "unknown")

    def test_every_rule_has_compiled_patterns(self):
        self.assertEqual(len(self.ec._COMPILED_RULES), len(self.ec._RULES))
        for category, patterns, *_ in self.ec._COMPILED_RULES:
            with self.subTest(category=category):
                self.assertGreater(len(patterns), 0)


if __name__ == "__main__":
    unittest.main()
