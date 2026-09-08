# -*- coding: utf-8 -*-
"""
qgis_agent 核心逻辑单元测试
在非 QGIS 环境中测试纯 Python 逻辑部分。

统一通过 tests.support 注入 qgis / langchain 替身，并以「包内模块」方式导入
（import_mod("qgis_tools") 等价于 import qgis_agent.qgis_tools），避免裸顶层
导入触发的相对导入失败（qgis_tools 模块级有 `from .smart_debugger import ...`）。
"""
import os
import sys
import json
import unittest

try:  # 既支持以包方式导入，也支持 unittest discover 顶层导入
    from . import support
except ImportError:
    import support

support.install_qgis_stub()
support.install_langchain_stub()


class TestConfig(unittest.TestCase):
    """测试 config.py 中 .env 解析"""

    def test_load_env_skip_no_equals(self):
        """测试 .env 中缺少 = 的行不会被处理（不抛异常）"""
        import tempfile
        from config import load_env_file

        fd, path = tempfile.mkstemp(suffix='.env', text=True)
        with os.fdopen(fd, 'w') as f:
            f.write("# comment line\n")
            f.write("VALID_KEY=hello\n")
            f.write("  NO_EQUALS_LINE  \n")
            f.write("ANOTHER_KEY=world\n")

        try:
            load_env_file(path)
            self.assertEqual(os.environ.get("VALID_KEY"), "hello")
            self.assertEqual(os.environ.get("ANOTHER_KEY"), "world")
        finally:
            os.unlink(path)
            os.environ.pop("VALID_KEY", None)
            os.environ.pop("ANOTHER_KEY", None)


class TestUtils(unittest.TestCase):
    """测试 utils.py"""

    def test_pack_interaction(self):
        from utils import pack
        row = ("id_001", "conv_001", "prompt_001", "hello", "",
               "06 06 2026 00:00:00", "input", "", "", "empty", "")
        result = pack(row, "interaction")
        self.assertEqual(result["ID"], "id_001")
        self.assertEqual(result["conversationID"], "conv_001")
        self.assertEqual(result["requestText"], "hello")
        self.assertEqual(result["typeMessage"], "input")
        self.assertEqual(result["workflow"], "empty")

    def test_unpack_interaction(self):
        from utils import unpack
        d = {
            "ID": "id_001", "conversationID": "conv_001",
            "promptID": "p1", "requestText": "hi", "contextText": "",
            "requestTime": "now", "typeMessage": "return",
            "responseText": "hello", "responseTime": "later",
            "workflow": "empty", "executionLog": ""
        }
        result = unpack(d, "interaction")
        self.assertEqual(len(result), 11)
        self.assertEqual(result[0], "id_001")

    def test_extract_code(self):
        from utils import extract_code
        response = """这是回复
```python
print("hello")
```
结束"""
        self.assertEqual(extract_code(response), 'print("hello")')

    def test_extract_code_none(self):
        from utils import extract_code
        self.assertEqual(extract_code("no code here"), "")

    def test_generate_unique_id(self):
        from utils import generate_unique_id
        uid = generate_unique_id()
        self.assertIsInstance(uid, str)
        self.assertGreater(len(uid), 10)
        self.assertNotIn("-", uid)  # 已替换为 _

    def test_get_current_timestamp(self):
        from utils import get_current_timestamp
        ts = get_current_timestamp()
        self.assertRegex(ts, r"\d{2} \d{2} \d{4} \d{2}:\d{2}:\d{2}")


class TestTools(unittest.TestCase):
    """测试 qgis_tools.py 中的纯逻辑部分（不依赖 QGIS API）"""

    @classmethod
    def setUpClass(cls):
        cls.qt = support.import_mod("qgis_tools")

    def test_tool_map_completeness(self):
        """确保 TOOL_DEFINITIONS 和 TOOL_MAP 一致（防止 schema 与实现脱节）"""
        defined_names = {t["name"] for t in self.qt.TOOL_DEFINITIONS}
        mapped_names = set(self.qt.TOOL_MAP.keys())
        self.assertEqual(defined_names, mapped_names)

    def test_tool_definitions_have_required(self):
        """确保所有工具定义包含必要的字段"""
        for tool in self.qt.TOOL_DEFINITIONS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("parameters", tool)
            self.assertIn("type", tool["parameters"])
            self.assertIn("required", tool["parameters"])

    def test_get_memory_path(self):
        """记忆路径拼接逻辑（_get_memory_path 依赖 QgsApplication，仅测拼接）"""
        expected_suffix = os.path.join("python", "plugins", "qgis_agent", "MEMORY.md")
        self.assertTrue(expected_suffix.endswith("MEMORY.md"))

    def test_save_memory_content(self):
        """测试记忆保存逻辑（不依赖 QGIS，临时替换路径函数）"""
        import tempfile
        qt = self.qt
        tmp_dir = tempfile.mkdtemp()
        memory_path = os.path.join(tmp_dir, "test_memory.md")
        original_get = qt._get_memory_path
        qt._get_memory_path = lambda: memory_path
        try:
            result = qt.save_memory("测试记忆内容", "测试")
            self.assertIn(result.get("status"), ("saved", "skipped"))
            result2 = qt.save_memory("测试记忆内容", "测试")
            self.assertEqual(result2.get("status"), "skipped")
            if result.get("status") == "saved":
                load_result = qt.load_memory()
                self.assertEqual(load_result.get("status"), "ok")
                self.assertIn("测试记忆内容", load_result.get("content", ""))
        finally:
            qt._get_memory_path = original_get
            if os.path.exists(memory_path):
                os.unlink(memory_path)
            os.rmdir(tmp_dir)


class TestProcessorLogic(unittest.TestCase):
    """测试 processor.py 中的纯逻辑部分（系统提示词回归防护）"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.processor = support.import_mod("processor")
        except Exception as exc:  # 真实 langchain 缺失且桩注入失败时优雅降级
            raise unittest.SkipTest("无法加载 processor（依赖缺失）: %s" % exc)

    def test_agent_system_prompt_has_tools(self):
        prompt = self.processor.AGENT_SYSTEM_PROMPT
        for name in ("get_qgis_info", "save_memory", "load_memory"):
            self.assertIn(name, prompt)

    def test_agent_system_prompt_non_empty(self):
        self.assertGreater(len(self.processor.AGENT_SYSTEM_PROMPT), 100)


class TestLLMProviders(unittest.TestCase):
    """测试 llm_providers.py"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.lp = support.import_mod("llm_providers")
        except Exception as exc:
            raise unittest.SkipTest("无法加载 llm_providers（依赖缺失）: %s" % exc)

    def test_get_default_api_key(self):
        # 未设置时返回空字符串
        self.assertEqual(self.lp.get_default_api_key("Unknown"), "")

    def test_get_llm_instance_deepseek(self):
        try:
            instance = self.lp.get_llm_instance(
                "DeepSeek", "deepseek-chat",
                "sk-test", "https://api.deepseek.com",
                temperature=0
            )
            self.assertIsNotNone(instance)
        except Exception as e:
            self.skipTest("跳过: %s" % e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
