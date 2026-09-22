# -*- coding: utf-8 -*-
"""打包与元数据一致性测试（从原 tests/__init__.py 的 TestConfig 拆出并重写）

原 `assertEqual(PLUGIN_VERSION, "1.0.0")` 与实际 2.1.3 不符，属于硬编码过期断言。
这里改成"以 metadata.txt 为唯一真源"的一致性校验，版本号升级无需改测试。
"""

import builtins
import os
import re
import unittest
from unittest import mock

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


class TestPackageManagerBrokenDependencies(unittest.TestCase):
    """依赖「装了但坏了」不得被误判成「没装」，更不能触发自动安装。

    回归背景：QGIS 安装包自带 pydantic 与 pydantic-core 版本错配时，
    `import langchain_core` 抛的是 SystemError 而非 ImportError。
    旧实现只捕获 ImportError，异常直接逃逸出 run()，用户看到的现象是
    「插件启用后毫无反应、只有一条日志 Traceback」。
    """

    @classmethod
    def setUpClass(cls):
        mod = support.import_mod("package_manager")
        cls.PackageManager = mod.PackageManager
        # 模块级函数挂到类上会变成绑定方法，必须包一层 staticmethod
        cls.describe_broken_dependency = staticmethod(mod.describe_broken_dependency)
        cls.broken_dependency_hint = staticmethod(mod.broken_dependency_hint)

    @staticmethod
    def _import_patch(exc_map):
        """构造一个 __import__ 替身：命中 exc_map 的模块名按指定异常抛出，其余走真实导入。"""
        real_import = builtins.__import__

        def _fake(name, *args, **kwargs):
            if name in exc_map:
                raise exc_map[name]
            return real_import(name, *args, **kwargs)

        return _fake

    def test_systemerror_is_classified_as_broken_not_missing(self):
        """版本错配抛 SystemError → 归入 broken，而不是 missing"""
        err = SystemError(
            "The installed pydantic-core version (2.48.0) is incompatible with the "
            "current pydantic version, which requires 2.46.4.")
        pm = self.PackageManager(["langchain_deepseek"])
        with mock.patch("builtins.__import__",
                        side_effect=self._import_patch({"langchain_deepseek": err})):
            missing = pm.check_dependencies()
        self.assertEqual(missing, [], "SystemError 不应被当成「缺失」")
        self.assertEqual([n for n, _ in pm.broken], ["langchain_deepseek"])

    def test_importerror_still_classified_as_missing(self):
        """真正的缺失仍然归入 missing（可自动安装）"""
        pm = self.PackageManager(["langchain_deepseek"])
        with mock.patch("builtins.__import__",
                        side_effect=self._import_patch(
                            {"langchain_deepseek": ImportError("No module named 'x'")})):
            missing = pm.check_dependencies()
        self.assertEqual(missing, ["langchain_deepseek"])
        self.assertEqual(pm.broken, [])

    def test_oserror_is_classified_as_broken(self):
        """动态库加载失败抛 OSError → 同样归入 broken"""
        pm = self.PackageManager(["langchain_core"])
        with mock.patch("builtins.__import__",
                        side_effect=self._import_patch(
                            {"langchain_core": OSError("dlopen failed")})):
            missing = pm.check_dependencies()
        self.assertEqual(missing, [])
        self.assertEqual(len(pm.broken), 1)

    def test_check_dependencies_never_raises(self):
        """check_dependencies 本身绝不向外抛异常（GUI 调用方不再需要防御）"""
        pm = self.PackageManager(["langchain_core", "langchain_openai"])
        boom = {"langchain_core": SystemError("x"), "langchain_openai": ValueError("y")}
        with mock.patch("builtins.__import__", side_effect=self._import_patch(boom)):
            try:
                pm.check_dependencies()
            except Exception as exc:  # noqa: BLE001
                self.fail("check_dependencies 抛出异常: %r" % (exc,))

    def test_broken_modules_never_auto_installed(self):
        """broken 里的模块不得进退安装流程（重装只会把环境改得更乱）"""
        pm = self.PackageManager(["langchain_core"])
        pm.missing = []
        pm.broken = [("langchain_core", SystemError("version mismatch"))]
        with mock.patch("pip.main") as fake_pip:
            self.assertTrue(pm.install_missing())
        self.assertFalse(fake_pip.called, "broken 模块不应触发 pip 安装")

    def test_broken_report_contains_module_and_exception(self):
        pm = self.PackageManager(["langchain_core"])
        pm.broken = [("langchain_core", SystemError("版本不匹配"))]
        report = pm.broken_report()
        self.assertIn("langchain_core", report)
        self.assertIn("SystemError", report)
        self.assertIn("版本不匹配", report)

    def test_report_is_empty_without_broken(self):
        pm = self.PackageManager(["langchain_core"])
        pm.broken = []
        self.assertEqual(pm.broken_report(), "")
        self.assertEqual(pm.hint_text(), "")

    def test_describe_truncates_long_message(self):
        text = self.describe_broken_dependency("m", RuntimeError("x" * 5000), limit=100)
        self.assertLess(len(text), 200)
        self.assertIn("…", text)

    def test_hint_recognises_pydantic_mismatch(self):
        """pydantic / pydantic-core 错配要解析出两个版本号并给出可执行命令"""
        err = SystemError(
            "The installed pydantic-core version (2.48.0) is incompatible with the "
            "current pydantic version, which requires 2.46.4. If you encounter this "
            "error, make sure that you haven't upgraded pydantic-core manually.")
        hint = self.broken_dependency_hint([("langchain_core", err)])
        self.assertIn("pydantic", hint)
        self.assertIn("并非插件缺陷", hint)
        self.assertIn("2.48.0", hint)
        self.assertIn('pydantic-core==2.46.4"', hint)   # 命令里的版本号不得带尾随点

    def test_hint_pydantic_without_versions(self):
        """只有 pydantic 字样但没版本号时，也不能崩，要退化为通用建议"""
        hint = self.broken_dependency_hint(
            [("langchain_core", SystemError("pydantic-core is incompatible"))])
        self.assertIn("pydantic", hint)
        self.assertIn("虚拟环境", hint)

    def test_hint_generic_for_other_errors(self):
        hint = self.broken_dependency_hint([("langchain_core", OSError("dlopen failed"))])
        self.assertIn("手动 import", hint)


if __name__ == "__main__":
    unittest.main()
