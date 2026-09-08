# -*- coding: utf-8 -*-
"""打包与元数据一致性测试（从原 tests/__init__.py 的 TestConfig 拆出并重写）

原 `assertEqual(PLUGIN_VERSION, "1.0.0")` 与实际 2.1.3 不符，属于硬编码过期断言。
这里改成"以 metadata.txt 为唯一真源"的一致性校验，版本号升级无需改测试。
"""

import os
import re
import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT
METADATA_PATH = os.path.join(PROJECT_ROOT, "metadata.txt")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.py")
CHANGELOG_PATH = os.path.join(PROJECT_ROOT, "CHANGELOG.md")


def read_metadata_version():
    """纯文本解析 metadata.txt 的 version= 字段（不 import 任何主模块）"""
    with open(METADATA_PATH, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("[") or not line:
                continue
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    raise AssertionError("metadata.txt 中找不到 version 字段")


def read_metadata_fields():
    """解析 metadata.txt 的 [general] 段为字典"""
    fields = {}
    with open(METADATA_PATH, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("[") or not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


class TestVersionConsistency(unittest.TestCase):
    def test_metadata_version_is_semver(self):
        version = read_metadata_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    @unittest.skipUnless(os.path.exists(CONFIG_PATH), "缺少 config.py")
    def test_config_version_matches_metadata(self):
        """config.PLUGIN_VERSION 必须与 metadata.txt 的 version 一致"""
        try:
            config = support.import_mod("config")
        except Exception as exc:  # pragma: no cover - config.py 只依赖 os
            self.skipTest("无法导入 config.py: %s" % exc)
        self.assertEqual(config.PLUGIN_VERSION, read_metadata_version())

    def test_config_does_not_hardcode_version(self):
        """回归护栏：config.py 不得再写死版本号字面量（真源是 metadata.txt）"""
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIsNone(
            re.search(r"PLUGIN_VERSION\s*=\s*[\"']", source),
            "config.py 里出现了硬编码的 PLUGIN_VERSION 字面量")

    @unittest.skipUnless(os.path.exists(CHANGELOG_PATH), "缺少 CHANGELOG.md")
    def test_changelog_mentions_current_version(self):
        version = read_metadata_version()
        with open(CHANGELOG_PATH, "r", encoding="utf-8") as f:
            changelog = f.read()
        self.assertIn(version, changelog)


class TestMetadataFields(unittest.TestCase):
    def test_required_fields_present(self):
        fields = read_metadata_fields()
        for key in ("name", "version", "description", "qgisMinimumVersion", "author"):
            with self.subTest(field=key):
                self.assertIn(key, fields)
                self.assertTrue(fields[key].strip(), key)

    def test_experimental_and_deprecated_flags(self):
        fields = read_metadata_fields()
        self.assertEqual(fields.get("experimental"), "False")
        self.assertEqual(fields.get("deprecated"), "False")

    def test_minimum_qgis_version_parsable(self):
        fields = read_metadata_fields()
        self.assertRegex(fields["qgisMinimumVersion"], r"^\d+\.\d+$")

    def test_icon_file_referenced_exists(self):
        fields = read_metadata_fields()
        icon = fields.get("icon", "icon.png")
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, icon)), icon)


class TestConfigValues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.config = support.import_mod("config")
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest("无法导入 config.py: %s" % exc)

    def test_database_name(self):
        self.assertEqual(self.config.DB_NAME, "QGIS_Agent.db")

    def test_plugin_name(self):
        self.assertIn("Agent", self.config.PLUGIN_NAME)

    def test_debug_mode_is_boolean(self):
        self.assertIn(self.config.DEBUG_MODE, [True, False])

    def test_load_env_file_skips_comments_and_no_equals(self):
        import tempfile
        original = os.environ.get("QGIS_AGENT_TEST_KEY")
        fd, path = tempfile.mkstemp(suffix=".env", text=True)
        try:
            with os.fdopen(fd, "w") as f:
                f.write("# comment line\n")
                f.write("QGIS_AGENT_TEST_KEY=hello\n")
                f.write("  NO_EQUALS_LINE  \n")
            self.config.load_env_file(path)
            self.assertEqual(os.environ.get("QGIS_AGENT_TEST_KEY"), "hello")
        finally:
            os.unlink(path)
            if original is None:
                os.environ.pop("QGIS_AGENT_TEST_KEY", None)
            else:
                os.environ["QGIS_AGENT_TEST_KEY"] = original

    def test_load_env_file_missing_path_is_noop(self):
        self.config.load_env_file(os.path.join(PROJECT_ROOT, "definitely-not-here.env"))


class TestIconGeneration(unittest.TestCase):
    def test_generate_icon_returns_png_bytes(self):
        generate_icon = support.import_mod("generate_icon").generate_icon
        png = generate_icon()
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 100)

    def test_icon_file_exists(self):
        icon_path = os.path.join(PROJECT_ROOT, "icon.png")
        self.assertTrue(os.path.exists(icon_path))
        self.assertGreater(os.path.getsize(icon_path), 100)


class TestPackageManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.PackageManager = support.import_mod("package_manager").PackageManager

    def test_only_whitelisted_modules_kept(self):
        pm = self.PackageManager(["os", "sys", "langchain_core"])
        self.assertEqual(pm.required_modules, ["langchain_core"])

    def test_unknown_modules_are_dropped(self):
        """白名单外的模块绝不进入安装流程（安全护栏）"""
        pm = self.PackageManager(["requests", "evil-pkg; rm -rf /"])
        self.assertEqual(pm.required_modules, [])

    def test_check_dependencies_reports_missing(self):
        pm = self.PackageManager(["langchain_openai"])
        missing = pm.check_dependencies()
        # check_dependencies 以「能否 __import__」为唯一判据；若测试环境已被
        # tests/support 桩注入 langchain_openai，桩模块同样可 import，检查器
        # 如实报告已满足（[]）。用同一判据断言，避免桩污染导致的误报。
        try:
            __import__("langchain_openai")
            importable = True
        except ImportError:
            importable = False
        self.assertEqual(missing, [] if importable else ["langchain_openai"])

    def test_install_missing_with_nothing_to_do(self):
        pm = self.PackageManager(["langchain_core"])
        pm.check_dependencies()
        pm.missing = []
        self.assertTrue(pm.install_missing())


if __name__ == "__main__":
    unittest.main()
