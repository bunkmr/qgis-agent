import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from qgis.core import Qgis
    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False


class TestUtils(unittest.TestCase):
    def test_generate_unique_id(self):
        from qgis_agent.utils import generate_unique_id
        uid1 = generate_unique_id()
        uid2 = generate_unique_id()
        self.assertNotEqual(uid1, uid2)
        self.assertIn("_", uid1)

    def test_get_current_timestamp(self):
        from qgis_agent.utils import get_current_timestamp
        ts = get_current_timestamp()
        self.assertIsInstance(ts, str)
        self.assertGreater(len(ts), 0)
        self.assertIn("2026", ts)

    def test_pack_unpack_conversation(self):
        from qgis_agent.utils import pack, unpack
        row = ("id1", "GLM::glm-4", "测试对话", "测试描述",
               "06 05 2026 10:00:00", "06 05 2026 10:00:00",
               0, 0, "local")
        packed = pack(row, "conversation")
        self.assertEqual(packed["ID"], "id1")
        self.assertEqual(packed["title"], "测试对话")
        unpacked = unpack(packed, "conversation")
        self.assertEqual(unpacked, list(row))

    def test_pack_unpack_interaction(self):
        from qgis_agent.utils import pack, unpack
        row = ("i1", "c1", "p1", "request", "context",
               "time1", "input", "response", "time2", "empty", "")
        packed = pack(row, "interaction")
        self.assertEqual(packed["requestText"], "request")
        self.assertEqual(packed["typeMessage"], "input")
        unpacked = unpack(packed, "interaction")
        self.assertEqual(unpacked, list(row))

    def test_extract_code(self):
        from qgis_agent.utils import extract_code
        response = """代码如下：```python
layer = iface.activeLayer()
print(layer.name())
```结束"""
        code = extract_code(response)
        self.assertIn("layer = iface.activeLayer()", code)
        self.assertNotIn("```", code)

    def test_extract_code_no_code(self):
        from qgis_agent.utils import extract_code
        response = "这里没有代码块"
        code = extract_code(response)
        self.assertEqual(code, "")

    def test_create_markdown(self):
        from qgis_agent.utils import create_markdown
        md = create_markdown("```python\nprint('hello')\n```")
        self.assertIn("<pre><code", md)
        self.assertIn("print('hello')", md)

    def test_nested_dict_to_list(self):
        from qgis_agent.utils import nested_dict_to_list
        d = {"A": ["a1", "a2"], "B": ["b1"]}
        result = nested_dict_to_list(d)
        self.assertIn("A::a1", result)
        self.assertIn("A::a2", result)
        self.assertIn("B::b1", result)
        self.assertEqual(len(result), 3)

    def test_format_description(self):
        from qgis_agent.utils import format_description
        self.assertEqual(format_description("test"), "test\n")

    def test_get_qgis_version_no_qgis(self):
        from qgis_agent.utils import get_qgis_version
        if not _HAS_QGIS:
            self.assertEqual(get_qgis_version(), "0.0")
        else:
            self.assertIsInstance(get_qgis_version(), str)

    def test_set_font_color_no_qgis(self):
        from qgis_agent.utils import set_font_color
        if not _HAS_QGIS:
            self.assertEqual(set_font_color(None), "#181C14")


class TestDataLoaderNoQGIS(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), "test_qgis_agent_noqgis.db")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _setup_loader(self):
        from qgis_agent.dataloader import DataLoader
        loader = DataLoader("test_qgis_agent_noqgis.db")
        loader.database_path = self.db_path
        return loader

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass

    def test_database_path(self):
        loader = self._setup_loader()
        self.assertIn("test_qgis_agent_noqgis.db", loader.database_path)

    def test_llm_full_dict(self):
        loader = self._setup_loader()
        self.assertIn("GLM", loader.llm_full_dict)
        self.assertIn("DeepSeek", loader.llm_full_dict)
        self.assertIn("XiaomiMiMo", loader.llm_full_dict)
        self.assertIn("Gemini", loader.llm_full_dict)
        self.assertIn("glm-4", loader.llm_full_dict["GLM"])
        self.assertIn("deepseek-chat", loader.llm_full_dict["DeepSeek"])

    def test_llm_endpoints(self):
        loader = self._setup_loader()
        self.assertIn("https://open.bigmodel.cn", loader.llm_endpoint_dict["GLM"])
        self.assertIn("https://api.deepseek.com", loader.llm_endpoint_dict["DeepSeek"])

    def test_get_llm_info(self):
        loader = self._setup_loader()
        provider, model = loader.get_llm_info("GLM::glm-4")
        self.assertEqual(provider, "GLM")
        self.assertEqual(model, "glm-4")

    def test_get_llm_info_default(self):
        loader = self._setup_loader()
        provider, model = loader.get_llm_info("Unknown::Model")
        self.assertEqual(provider, "default")
        self.assertEqual(model, "default")


class TestLLMProviders(unittest.TestCase):
    def test_providers_dict(self):
        from qgis_agent.llm_providers import LLM_PROVIDERS
        self.assertIn("GLM", LLM_PROVIDERS)
        self.assertIn("DeepSeek", LLM_PROVIDERS)
        self.assertIn("XiaomiMiMo", LLM_PROVIDERS)
        self.assertIn("Gemini", LLM_PROVIDERS)
        self.assertIn("OpenAI", LLM_PROVIDERS)

    def test_provider_models(self):
        from qgis_agent.llm_providers import LLM_PROVIDERS
        self.assertIn("glm-4", LLM_PROVIDERS["GLM"]["models"])
        self.assertIn("glm-4-flash", LLM_PROVIDERS["GLM"]["models"])
        self.assertIn("deepseek-chat", LLM_PROVIDERS["DeepSeek"]["models"])
        self.assertIn("deepseek-reasoner", LLM_PROVIDERS["DeepSeek"]["models"])
        self.assertIn("MiMo", LLM_PROVIDERS["XiaomiMiMo"]["models"])
        self.assertIn("gemini-2.0-flash", LLM_PROVIDERS["Gemini"]["models"])

    def test_provider_endpoints(self):
        from qgis_agent.llm_providers import LLM_PROVIDERS
        self.assertIn("bigmodel.cn", LLM_PROVIDERS["GLM"]["endpoint"])
        self.assertIn("deepseek.com", LLM_PROVIDERS["DeepSeek"]["endpoint"])
        self.assertIn("xiaomi.com", LLM_PROVIDERS["XiaomiMiMo"]["endpoint"])
        self.assertIn("googleapis.com", LLM_PROVIDERS["Gemini"]["endpoint"])

    def test_get_default_api_key(self):
        from qgis_agent.llm_providers import get_default_api_key
        # No env vars set, should return empty
        self.assertEqual(get_default_api_key("GLM"), "")

    def test_get_llm_instance_invalid(self):
        from qgis_agent.llm_providers import get_llm_instance
        with self.assertRaises(ValueError):
            get_llm_instance("UnknownProvider", "model", "key")


class TestIconGenerator(unittest.TestCase):
    def test_icon_generation(self):
        from qgis_agent.generate_icon import generate_icon
        png_data = generate_icon()
        self.assertIsNotNone(png_data)
        self.assertTrue(png_data.startswith(b'\x89PNG'))
        self.assertGreater(len(png_data), 100)

    def test_icon_file_exists(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icon.png")
        self.assertTrue(os.path.exists(icon_path))
        self.assertGreater(os.path.getsize(icon_path), 100)


class TestPackageManager(unittest.TestCase):
    def test_init(self):
        from qgis_agent.package_manager import PackageManager
        pm = PackageManager(["os"])
        self.assertEqual(len(pm.required_modules), 1)

    def test_check_existing_module(self):
        from qgis_agent.package_manager import PackageManager
        pm = PackageManager(["os", "sys"])
        try:
            pm.check_dependencies()
        except Exception:
            pass


class TestConfig(unittest.TestCase):
    def test_config_values(self):
        from qgis_agent.config import DB_NAME, PLUGIN_NAME, PLUGIN_VERSION, DEBUG_MODE
        self.assertEqual(DB_NAME, "QGIS_Agent.db")
        self.assertIn("Agent", PLUGIN_NAME)
        self.assertEqual(PLUGIN_VERSION, "1.0.0")
        self.assertIn(DEBUG_MODE, [True, False])


if __name__ == "__main__":
    unittest.main()
