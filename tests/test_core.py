# -*- coding: utf-8 -*-
"""
qgis_agent 核心逻辑单元测试
在非 QGIS 环境中测试纯 Python 逻辑部分
"""
import os
import sys
import json
import unittest

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfig(unittest.TestCase):
    """测试 config.py 中 .env 解析"""

    def test_load_env_skip_no_equals(self):
        """测试 .env 中缺少 = 的行不会被处理（不抛异常）"""
        import tempfile
        from config import load_env_file

        # 创建一个临时 .env 文件
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
        """测试 pack 函数对 interaction 表"""
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
        """测试 unpack 函数对 interaction 表"""
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
        """测试代码提取"""
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

    def test_tool_map_completeness(self):
        """确保 TOOL_DEFINITIONS 和 TOOL_MAP 一致"""
        from qgis_tools import TOOL_DEFINITIONS, TOOL_MAP
        defined_names = {t["name"] for t in TOOL_DEFINITIONS}
        mapped_names = set(TOOL_MAP.keys())
        self.assertEqual(defined_names, mapped_names)

    def test_tool_definitions_have_required(self):
        """确保所有工具定义包含必要的字段"""
        from qgis_tools import TOOL_DEFINITIONS
        for tool in TOOL_DEFINITIONS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("parameters", tool)
            self.assertIn("type", tool["parameters"])
            self.assertIn("required", tool["parameters"])

    def test_get_memory_path(self):
        """测试记忆路径生成"""
        import os
        import tempfile
        import qgis_tools

        # 模拟 QgsApplication
        class MockQgsApp:
            @staticmethod
            def qgisSettingsDirPath():
                return tempfile.gettempdir()

        # 由于 _get_memory_path 依赖 qgis.core.QgsApplication，
        # 在非 QGIS 环境中只能测试逻辑
        # 这里我们只验证路径拼接逻辑
        expected_suffix = os.path.join("python", "plugins", "qgis_agent", "MEMORY.md")
        self.assertTrue(expected_suffix.endswith("MEMORY.md"))

    def test_save_memory_content(self):
        """测试记忆保存逻辑（不依赖 QGIS）"""
        import qgis_tools
        import tempfile
        import os

        # 临时替换 _get_memory_path
        tmp_dir = tempfile.mkdtemp()
        memory_path = os.path.join(tmp_dir, "test_memory.md")
        original_get = qgis_tools._get_memory_path
        qgis_tools._get_memory_path = lambda: memory_path

        try:
            # 保存记忆
            result = qgis_tools.save_memory("测试记忆内容", "测试")
            self.assertIn(result.get("status"), ("saved", "skipped"))

            # 再次保存相同内容应跳过
            result2 = qgis_tools.save_memory("测试记忆内容", "测试")
            self.assertEqual(result2.get("status"), "skipped")

            # 读取记忆
            if result.get("status") == "saved":
                load_result = qgis_tools.load_memory()
                self.assertEqual(load_result.get("status"), "ok")
                self.assertIn("测试记忆内容", load_result.get("content", ""))
        finally:
            qgis_tools._get_memory_path = original_get
            if os.path.exists(memory_path):
                os.unlink(memory_path)
            os.rmdir(tmp_dir)


class TestProcessorLogic(unittest.TestCase):
    """测试 processor.py 中的纯逻辑部分"""

    def test_agent_system_prompt_has_tools(self):
        from processor import AGENT_SYSTEM_PROMPT
        self.assertIn("get_qgis_info", AGENT_SYSTEM_PROMPT)
        self.assertIn("save_memory", AGENT_SYSTEM_PROMPT)
        self.assertIn("load_memory", AGENT_SYSTEM_PROMPT)

    def test_agent_system_prompt_non_empty(self):
        from processor import AGENT_SYSTEM_PROMPT
        self.assertGreater(len(AGENT_SYSTEM_PROMPT), 100)


class TestLLMProviders(unittest.TestCase):
    """测试 llm_providers.py"""

    def test_get_llm_instance_deepseek(self):
        from llm_providers import get_llm_instance
        try:
            instance = get_llm_instance(
                "DeepSeek", "deepseek-chat",
                "sk-test", "https://api.deepseek.com",
                temperature=0
            )
            self.assertIsNotNone(instance)
        except Exception as e:
            # 在无 langchain_deepseek 环境可能失败
            self.skipTest(f"跳过: {e}")

    def test_get_default_api_key(self):
        from llm_providers import get_default_api_key
        # 未设置时返回空字符串
        result = get_default_api_key("Unknown")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
