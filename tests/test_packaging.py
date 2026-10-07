# -*- coding: utf-8 -*-
"""打包与元数据一致性测试（从原 tests/__init__.py 的 TestConfig 拆出并重写）

原 `assertEqual(PLUGIN_VERSION, "1.0.0")` 与实际 2.1.3 不符，属于硬编码过期断言。
这里改成"以 metadata.txt 为唯一真源"的一致性校验，版本号升级无需改测试。
"""

import ast
import builtins
import glob
import json
import os
import re
import unittest
import xml.etree.ElementTree as ET
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


class TestMetadataIsParserSafe(unittest.TestCase):
    """metadata.txt 必须能被 Python 的 configparser 正常解析。

    这不是洁癖：plugins.qgis.org 的检查器与 QGIS 自身都会用 configparser
    读 metadata.txt，而它默认开启 `%` 插值 —— 正文里出现一个**裸 `%`**
    （例如写「100% 失败」）就会让整份元数据解析失败：

        InterpolationSyntaxError: '%' must be followed by '%' or '('

    手工 grep 检查发现不了这类问题，所以在此钉死。写百分号请用「百分之百」
    或写 `%%`。
    """

    def _parsed(self):
        import configparser

        with open(METADATA_PATH, "r", encoding="utf-8-sig") as fh:
            raw = fh.read()
        parser = configparser.ConfigParser()
        parser.read_string(raw)          # 解析失败即测试失败
        return parser

    def test_metadata_parses_with_configparser(self):
        parser = self._parsed()
        self.assertIn("general", parser)
        self.assertRegex(parser["general"]["version"], r"^\d+\.\d+\.\d+$")

    def test_changelog_has_no_raw_percent(self):
        """显式给出可读的错误信息（否则只能看到 configparser 的原始堆栈）"""
        with open(METADATA_PATH, "r", encoding="utf-8-sig") as fh:
            raw = fh.read()
        changelog = raw.split("changelog=", 1)[1]
        offenders = [
            line.strip()[:70]
            for line in changelog.splitlines()
            if "%" in line.replace("%%", "")
        ]
        self.assertFalse(
            offenders,
            "metadata.txt 的 changelog 里有裸百分号，会让 configparser 插值报错：\n  "
            + "\n  ".join(offenders[:3]))

    def test_all_version_headings_survive(self):
        """每个历史版本标题都必须在 —— 新增版本时最容易整段覆盖掉旧的"""
        changelog = self._parsed()["general"]["changelog"]
        versions = re.findall(r"v(\d+\.\d+\.\d+) 更新内容", changelog)
        self.assertGreaterEqual(len(versions), 10, "changelog 版本标题疑似被整段覆盖")
        self.assertEqual(len(versions), len(set(versions)), "changelog 有重复的版本标题")
        self.assertEqual(versions[0], read_metadata_version(),
                         "changelog 首段必须是当前版本")


class TestI18nPackaging(unittest.TestCase):
    """i18n 资源必须真正进包，且 .qm 必须是真的（防回归）。

    历史坑（2026-09-24）：包里那个 .qm 是 **12 字节空文件**，从未生效，
    但没有任何测试发现 —— `*.qm` 一直在白名单里，"文件在包里"这件事
    看起来永远成立。所以这里断言的不只是"在不在"，而是**大小、magic、
    条数、两语言成对**，以及"源串条数够多"。
    """

    I18N_DIR = os.path.join(PROJECT_ROOT, "i18n")
    QM_MAGIC = b"\x3c\xb8\x64\x18"     # Qt 5.15+ 的 .qm 魔数
    MIN_QM_BYTES = 1024                # 12 字节空文件的教训
    MIN_ENTRIES = 150                  # 只导出到几条时立刻报错

    @classmethod
    def setUpClass(cls):
        cls.bp = support.import_mod("build_plugin")
        cls.i18n_init = os.path.join(cls.I18N_DIR, "__init__.py")
        with open(cls.i18n_init, encoding="utf-8") as fh:
            cls._init_tree = ast.parse(fh.read())
        # 语言清单与 context 都从源码 AST 里取 —— 本模块刻意**不** import
        # 插件主模块（那样要装一堆 Qt 替身），保持"纯文本校验"的定位。
        cls.codes = cls._assign_value("_LANGUAGES")
        cls.codes = [item.elts[0].value for item in cls.codes.elts]
        cls.context = cls._assign_value("TRANSLATION_CONTEXT").value

    @classmethod
    def _assign_value(cls, name):
        for node in cls._init_tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        raise AssertionError("i18n/__init__.py 里找不到 %s" % name)

    def ts_path(self, code):
        return os.path.join(self.I18N_DIR, "qgis_agent_%s.ts" % code)

    def qm_path(self, code):
        return os.path.join(self.I18N_DIR, "qgis_agent_%s.qm" % code)

    # ── 打包白名单 ──
    def test_i18n_files_are_packed(self):
        for rel in ["i18n/__init__.py", "i18n/en.json"] + \
                   ["i18n/qgis_agent_%s.%s" % (c, ext)
                    for c in self.codes for ext in ("ts", "qm")] + \
                   ["i18n/messages_%s.json" % c for c in self.codes]:
            self.assertTrue(self.bp.should_include(rel),
                            "i18n 资源必须进包，但被排除了：%s" % rel)

    def test_build_tooling_is_not_packed(self):
        for rel in ("build_translations.py", "build_plugin.py",
                    "tests/test_i18n.py", "i18n/__pycache__/__init__.pyc"):
            self.assertFalse(self.bp.should_include(rel),
                             "开发期文件不该进发布包：%s" % rel)

    # ── 成对与命名（QGIS 按 <插件名>_<locale>.qm 自动装载，名字错了就永远不生效）──
    def test_languages_are_paired_and_correctly_named(self):
        plugin_name = self.bp.PLUGIN_NAME
        self.assertEqual(plugin_name, "qgis_agent")
        for code in self.codes:
            for path in (self.ts_path(code), self.qm_path(code)):
                self.assertTrue(os.path.isfile(path), "缺少 %s" % path)
            base = os.path.basename(self.qm_path(code))
            self.assertEqual(base, "%s_%s.qm" % (plugin_name, code),
                             "文件名不符合 QGIS 自动装载约定")

    def test_no_orphan_translation_files(self):
        """目录里不能有多余的 .ts/.qm（表里没有的语言 = 永远不会被装载）。"""
        allowed = {"qgis_agent_%s.%s" % (c, e) for c in self.codes for e in ("ts", "qm")}
        found = {os.path.basename(p)
                 for p in glob.glob(os.path.join(self.I18N_DIR, "*.ts"))
                 + glob.glob(os.path.join(self.I18N_DIR, "*.qm"))}
        self.assertEqual(found, allowed, "存在孤儿翻译文件：%s" % sorted(found - allowed))

    # ── .qm 必须是真的 ──
    def test_qm_files_are_real_not_empty(self):
        for code in self.codes:
            with open(self.qm_path(code), "rb") as fh:
                blob = fh.read()
            self.assertGreater(len(blob), self.MIN_QM_BYTES,
                               "%s.qm 只有 %d 字节 —— 疑似空壳（历史上就是 12 字节）"
                               % (code, len(blob)))
            self.assertEqual(blob[:4], self.QM_MAGIC,
                             "%s.qm 魔数不对，Qt 会静默拒绝装载" % code)

    def test_qm_files_are_distinct(self):
        """两种语言的 .qm 不能是同一份拷贝。"""
        blobs = {}
        for code in self.codes:
            with open(self.qm_path(code), "rb") as fh:
                blobs[code] = fh.read()
        self.assertGreaterEqual(len(set(blobs.values())), 2,
                                "所有语言的 .qm 内容相同 —— 多半是复制错了")

    # ── 降级表（.qm 不可用时的退路）──
    def messages_path(self, code):
        return os.path.join(self.I18N_DIR, "messages_%s.json" % code)

    def test_degraded_tables_exist_and_are_not_empty(self):
        for code in self.codes:
            path = self.messages_path(code)
            self.assertTrue(os.path.isfile(path), "缺少降级表 %s" % path)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIsInstance(data, dict)
            self.assertGreater(
                len(data), self.MIN_ENTRIES,
                "%s 的降级表只有 %d 条 —— .qm 一旦不可用就等于没有翻译"
                % (code, len(data)))
            self.assertTrue(all(k and isinstance(v, str) and v
                                for k, v in data.items()),
                            "%s 的降级表里有空值 —— 会让界面整片变空白" % code)

    def test_no_orphan_degraded_tables(self):
        allowed = {"messages_%s.json" % c for c in self.codes} | {"en.json"}
        found = {os.path.basename(p)
                 for p in glob.glob(os.path.join(self.I18N_DIR, "*.json"))}
        self.assertEqual(found, allowed,
                         "存在孤儿降级表：%s" % sorted(found - allowed))

    # ── 发布包里的 Python 不得碰"不可信 XML"解析（Bandit 会 BLOCK 整版）──
    # bandit 的黑名单里与 XML 有关的导入名（B405/B406/B407/B408/B409/B410/B411）。
    # 名称必须与 bandit 的 blacklist 同源，否则守卫会比扫描器更严 → 误报。
    XML_IMPORTS = ("xml.etree", "xml.sax", "xml.parsers.expat",
                   "xml.dom.minidom", "xml.dom.pulldom", "lxml", "xmlrpclib")

    @staticmethod
    def _shipped_trees():
        """产出 ``(相对路径, AST)``，只含**会进发布包**的 .py。"""
        for path in glob.glob(os.path.join(PROJECT_ROOT, "**", "*.py"),
                              recursive=True):
            rel = os.path.relpath(path, PROJECT_ROOT)
            if not TestI18nPackaging.bp.should_include(rel):
                continue                      # 开发期文件，不进包，不管
            try:
                with open(path, encoding="utf-8") as fh:
                    yield rel, ast.parse(fh.read(), filename=path)
            except (SyntaxError, UnicodeDecodeError):
                continue

    def test_shipped_code_never_imports_unhardened_xml(self):
        """进包的 .py 不得 import xml.etree / xml.sax / minidom 等。

        插件仓库（plugins.qgis.org）用 Bandit 静态扫描发布包：
        ``xml.etree.ElementTree`` 被判 B405（导入）与 B314（调用），而
        **只要有一条 Bandit 发现，整个版本就会被 BLOCKED** ——
        不进入人审、不可下载。v2.4.14 首次上传正是这样被挡住的
        （7 条发现里有 2 条来自 i18n 里对 .ts 的 XML 解析）。
        这条守卫按"是否进包"判断，正好覆盖发布口径。
        """
        offenders = []
        for rel, tree in self._shipped_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if any(name == m or name.startswith(m + ".")
                           for m in self.XML_IMPORTS):
                        offenders.append("%s:%d import %s"
                                         % (rel, node.lineno, name))
        self.assertFalse(
            offenders,
            "发布包里的代码 import 了 XML 解析模块，会被插件仓库的 Bandit "
            "扫描判为 B405/B406/B407 等并 BLOCK 整个版本：%s" % offenders[:5])

    def test_shipped_code_has_no_bandit_b110_pattern(self):
        """进包的 .py 不得出现 Bandit 会判 B110 的 ``try/except: pass``。

        ⚠️ 判据必须与 bandit **同源**（已读其源码 try_except_pass.py）：
        默认配置 ``check_typed_exception = False`` 时，**只有裸 ``except:``
        或异常类型恰好是 ``Exception``** 才算，写明具体类型
        （``except json.JSONDecodeError:``）一律放过。先前按"任意 except + pass"
        写会多报 3 处（实测 7 vs 扫描器的 4），那种守卫会把人训练成无视它。
        """
        offenders = []
        for rel, tree in self._shipped_trees():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                for handler in node.handlers:
                    if len(handler.body) != 1:
                        continue
                    if not isinstance(handler.body[0], ast.Pass):
                        continue
                    type_node = handler.type
                    # 裸 except:（type 为 None）或 except Exception: 才命中
                    if type_node is not None and \
                            getattr(type_node, "id", None) != "Exception":
                        continue
                    offenders.append("%s:%d" % (rel, handler.lineno))
        self.assertFalse(
            offenders,
            "出现 Bandit B110 模式（try/except[Exception]: pass）—— "
            "一条发现就会 BLOCK 整个版本；改用 contextlib.suppress 或把 "
            "except 体写成有意义的赋值/日志：%s" % offenders[:5])

    # ── .ts 必须完整且不含未完成条目 ──
    @staticmethod
    def _local(tag):
        """取标签的局部名 —— lupdate 产出的 .ts **不带 xmlns**（`<TS version="2.1">`），
        但别人手工加过命名空间时也得能解析，所以一律按局部名匹配。"""
        return tag.rsplit("}", 1)[-1]

    def _entries(self, code):
        root = ET.parse(self.ts_path(code)).getroot()
        out = set()
        for ctx in root.iter():
            if self._local(ctx.tag) != "context":
                continue
            name = None
            messages = []
            for child in ctx:
                local = self._local(child.tag)
                if local == "name":
                    name = child.text
                elif local == "message":
                    messages.append(child)
            for msg in messages:
                for child in msg:
                    if self._local(child.tag) == "source":
                        out.add((name, child.text))
                        break
        return out

    def test_ts_files_are_wellformed_and_complete(self):
        sizes = {}
        for code in self.codes:
            entries = self._entries(code)      # 解析失败会直接抛错
            sizes[code] = len(entries)
            self.assertGreater(len(entries), self.MIN_ENTRIES,
                               "%s.ts 只导出到 %d 条，疑似提取链路断了"
                               % (code, len(entries)))
        self.assertEqual(len(set(sizes.values())), 1,
                         "各语言 .ts 条数不一致：%s" % sizes)

    def test_ts_key_sets_are_identical(self):
        sets = {c: self._entries(c) for c in self.codes}
        ref = sets[self.codes[0]]
        for code, got in sets.items():
            self.assertEqual(got, ref,
                             "%s 与 %s 的条目集合不一致：仅前者 %s / 仅后者 %s"
                             % (code, self.codes[0],
                                sorted(got - ref)[:5], sorted(ref - got)[:5]))

    def test_ts_has_no_unfinished_or_vanished(self):
        """包里的 .ts 不得带 unfinished/vanished —— 等于把没翻的串发出去。"""
        for code in self.codes:
            with open(self.ts_path(code), encoding="utf-8") as fh:
                body = fh.read()
            self.assertNotIn('type="unfinished"', body, "%s.ts 有未翻译条目" % code)
            self.assertNotIn('type="vanished"', body, "%s.ts 有过期条目" % code)

    def test_ts_context_matches_runtime_context(self):
        for code in self.codes:
            self.assertEqual({n for n, _ in self._entries(code)}, {self.context},
                             "%s.ts 的 context 与 TRANSLATION_CONTEXT 不一致 "
                             "（translate() 会查不到）" % code)


if __name__ == "__main__":
    unittest.main()
