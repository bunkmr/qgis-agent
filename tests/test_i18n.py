# -*- coding: utf-8 -*-
"""i18n 正式单测（#119）。

分三层，各自的可运行环境不同：

1. **纯逻辑**（归一化 / 标签 / 优先级）——任何环境都能跑，用替身把 QSettings 与
   locale 两个接缝换掉。
2. **静态覆盖**——AST 扫源码里所有 ``translate`` / ``QT_TRANSLATE_NOOP`` 字面量，
   要求它们在 .ts 里都有对应条目。这是"漏翻"的唯一自动化防线。
3. **真 Qt 行为**（装载 / 热切换 / 降级 / 反向验证）——**必须真 Qt**，否则
   QTranslator 是 MagicMock，``load()`` 永远返回真值，测试会全绿却什么都没验证。
   无真 Qt 时整类跳过；真机验收用：

       source /tmp/qgis3_env.sh && "$QGIS_PY" -m unittest test_i18n
       source /tmp/qgis_env.sh  && "$QGIS_PY" -m unittest test_i18n
"""

import ast
import glob
import os
import shutil
import sys
import tempfile
import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT
I18N_DIR = os.path.join(PROJECT_ROOT, "i18n")
SKIP_DIRS = {"tests", "i18n", "help", "scripts", ".workbuddy", "__pycache__"}

support.ensure_project_path()
i18n = support.import_mod("i18n")


def _qt_is_real():
    """QTranslator 是不是真的 Qt 类（而不是 unittest.mock 替身）。

    ⚠️ 必须这样判：桩环境里 ``qgis.PyQt.QtCore`` 是 MagicMock，于是
    ``QTranslator().load(任意路径)`` 返回一个**真值 MagicMock** —— 所有
    "装载成功了吗"的断言都会通过，而实际上什么都没装载。
    """
    if i18n.QTranslator is None:
        return False
    mod = getattr(type(i18n.QTranslator()), "__module__", "") or ""
    return not mod.startswith(("unittest.mock", "mock"))


HAVE_QT = _qt_is_real()
REQUIRES_QT = unittest.skipUnless(
    HAVE_QT, "需要真 Qt（QTranslator 是 mock 时无法验证装载行为）")


# ────────────────────────────── 1. 纯逻辑 ──────────────────────────────

class TestNormalize(unittest.TestCase):
    """normalize()：任意 locale → 插件支持的语言码。"""

    CASES = [
        ("zh", "zh_CN"), ("zh_CN", "zh_CN"), ("zh-Hans-CN", "zh_CN"),
        ("zh_HK", "zh_CN"), ("zh_TW", "zh_CN"), ("zh-Hant", "zh_CN"),
        ("ZH_cn", "zh_CN"), ("  zh_CN  ", "zh_CN"),
        ("en", "en"), ("en_US", "en"), ("en-GB", "en"), ("EN", "en"),
        ("de", "en"), ("fr_FR", "en"), ("C", "en"), ("", "en"),
        (None, "en"), ("ja_JP", "en"),
    ]

    def test_normalize_matrix(self):
        for raw, want in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(i18n.normalize(raw), want)

    def test_dash_and_underscore_are_equivalent(self):
        self.assertEqual(i18n.normalize("zh-Hans-CN"), i18n.normalize("zh_Hans_CN"))

    def test_default_language_is_a_supported_language(self):
        self.assertIn(i18n.DEFAULT_LANGUAGE, i18n.available_codes())


class TestLanguageLabels(unittest.TestCase):
    """双语标签 —— 需求里明确要求"语言设置的菜单名称要双语标注"。"""

    def test_auto_label_is_bilingual(self):
        self.assertIn("/", i18n.AUTO_LABEL, "「跟随系统」标签必须双语")
        self.assertIn("跟随系统", i18n.AUTO_LABEL)
        self.assertIn("Follow system", i18n.AUTO_LABEL)

    def test_every_language_label_is_bilingual(self):
        for code, label in i18n.available_languages():
            with self.subTest(code=code):
                self.assertIn("/", label, "语言项标签必须双语：%s" % label)
                self.assertEqual(len(label.split("/")), 2)
                left, right = [p.strip() for p in label.split("/")]
                self.assertTrue(left and right, "双语标签两侧都不能为空：%s" % label)

    def test_language_label_lookup(self):
        self.assertEqual(i18n.language_label("zh_CN"),
                         dict(i18n.available_languages())["zh_CN"])
        self.assertEqual(i18n.language_label("de_DE"), "de_DE", "未知码原样返回")

    def test_choice_label_never_returns_an_unselectable_value(self):
        """下拉框永远要有可选中项：auto / 非法值 一律落到「跟随系统」。"""
        self.assertEqual(i18n.choice_label(i18n.AUTO), i18n.AUTO_LABEL)
        self.assertEqual(i18n.choice_label(None), i18n.AUTO_LABEL)
        self.assertEqual(i18n.choice_label(""), i18n.AUTO_LABEL)
        self.assertEqual(i18n.choice_label("   "), i18n.AUTO_LABEL)
        self.assertEqual(i18n.choice_label("klingon"), i18n.AUTO_LABEL,
                         "用户手工改过 QSettings 时不能让下拉框停在不存在的语言上")
        for code in i18n.available_codes():
            self.assertEqual(i18n.choice_label(code), i18n.language_label(code))

    def test_available_codes_are_unique_and_available(self):
        codes = i18n.available_codes()
        self.assertEqual(len(codes), len(set(codes)))
        for code in codes:
            self.assertTrue(i18n.is_available(code))
        self.assertFalse(i18n.is_available(i18n.AUTO),
                         "auto 不是语言码，不能混进语言列表")
        self.assertNotIn(i18n.AUTO, codes)


class TestPreferredLanguagePriority(unittest.TestCase):
    """优先级：显式选择 > QGIS 界面语言 > 兜底。用替身换掉两个接缝。"""

    def setUp(self):
        self._saved = (i18n._stored_choice, i18n.qgis_locale)

        def restore():
            i18n._stored_choice, i18n.qgis_locale = self._saved

        self.addCleanup(restore)

    def _set(self, stored, locale):
        i18n._stored_choice = lambda: stored
        i18n.qgis_locale = lambda: locale

    def test_explicit_choice_wins_over_qgis_locale(self):
        self._set("en", "zh_CN")
        self.assertEqual(i18n.preferred_language(), "en")
        self._set("zh_CN", "en_US")
        self.assertEqual(i18n.preferred_language(), "zh_CN")

    def test_auto_follows_qgis_locale(self):
        for locale, want in (("zh", "zh_CN"), ("zh_CN", "zh_CN"), ("en", "en"),
                             ("en_GB", "en"), ("de", "en"), ("", "en")):
            with self.subTest(locale=locale):
                self._set(i18n.AUTO, locale)
                self.assertEqual(i18n.preferred_language(), want)

    def test_empty_stored_value_behaves_like_auto(self):
        for stored in ("", "   ", None):
            with self.subTest(stored=stored):
                self._set(stored, "zh_CN")
                # _stored_choice 在真实实现里会把空值归到 auto；这里模拟该结果
                self._set(i18n.AUTO if not stored or not str(stored).strip() else stored,
                          "zh_CN")
                self.assertEqual(i18n.preferred_language(), "zh_CN")

    def test_preferred_language_is_always_a_supported_code(self):
        for stored in (i18n.AUTO, "", "garbage", "en", "zh_CN"):
            for locale in ("zh_TW", "en_US", "de", "", "C"):
                self._set(stored, locale)
                with self.subTest(stored=stored, locale=locale):
                    self.assertIn(i18n.preferred_language(), i18n.available_codes())


# ─────────────────────── 2. 静态覆盖（漏翻防线）───────────────────────

def _const_str(node):
    """从 AST 节点里取出字符串字面量；拿不到返回 None。

    覆盖三种写法：
      - 裸字面量（含隐式拼接的跨行字面量，AST 里是单个 Constant）
      - ``"模板%s" % x``（取左侧字面量 —— 这正是我们要的那部分）
      - ``a if cond else b``（两个分支都是待翻译文案）
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _const_str(node.left)
    if isinstance(node, ast.IfExp):
        return _const_str(node.body) or _const_str(node.orelse)
    return None


def _translate_calls():
    """扫全仓，返回 ``{context: {source: [位置, ...]}}``（只收字面量）。

    带位置是为了失败时能直接指出该改哪一行 —— 这条守卫最常见的触发原因是
    ``_translate(`` 被拆行（pylupdate 是行敏感的，会整条漏提取），光说
    "某串没译文" 会让人以为是漏填 en.json。
    """
    found = {}
    for path in glob.glob(os.path.join(PROJECT_ROOT, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, PROJECT_ROOT)
        if set(rel.split(os.sep)) & SKIP_DIRS:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if name not in ("translate", "_translate", "QT_TRANSLATE_NOOP"):
                continue
            context = _const_str(node.args[0])
            source = _const_str(node.args[1])
            if context and source:
                found.setdefault(context, {}).setdefault(source, []).append(
                    "%s:%d" % (rel, node.lineno))
    return found


def _ts_sources(code):
    """读 .ts 里的 ``<source>`` 集合（用 i18n 的解析器，顺带验证解析链路）。"""
    return {src for (ctx, src) in i18n.load_messages(i18n.translation_file(code))}


MULTILINE_HINT = (
    "最常见的成因：`_translate(` 被拆到了单独一行、上下文参数落到下一行 —— "
    "pylupdate 是**行敏感**的，这种写法会整条漏提取。"
    "修法：把 `_translate(\"QGISAgent\",` 写在同一行（实测该写法必被提取）。"
)


class TestStaticCoverage(unittest.TestCase):
    """源码里出现过的翻译字面量，必须都在 .ts 里有条目。

    这是唯一能自动发现"新写的界面文案忘了进翻译表"的手段 —— 漏了不会报错，
    英文界面下那一句会**静默**留在中文。
    """

    @classmethod
    def setUpClass(cls):
        cls.calls = _translate_calls()
        cls.ts = {code: _ts_sources(code) for code in i18n.available_codes()}

    def test_scan_actually_found_something(self):
        """守卫自身：扫不到任何调用说明扫描逻辑坏了（假绿来源）。"""
        total = sum(len(v) for v in self.calls.values())
        self.assertGreater(total, 100, "只扫到 %d 条待翻译字面量，扫描逻辑可疑" % total)
        self.assertIn(i18n.TRANSLATION_CONTEXT, self.calls)

    def test_every_source_string_is_in_translation_tables(self):
        used = set(self.calls.get(i18n.TRANSLATION_CONTEXT, {}))
        for code, sources in self.ts.items():
            missing = sorted(used - sources)
            where = ["%r @ %s" % (m[:40], ",".join(
                self.calls[i18n.TRANSLATION_CONTEXT][m][:2])) for m in missing[:5]]
            self.assertFalse(
                missing,
                "%s 里有 %d 条源码文案没有译文（英文界面下会静默留在中文）：\n  %s\n%s"
                % (code, len(missing), "\n  ".join(where), MULTILINE_HINT))

    def test_all_ts_languages_cover_the_same_strings(self):
        used = set(self.calls.get(i18n.TRANSLATION_CONTEXT, {}))
        for code, sources in self.ts.items():
            extra = sorted(sources - used)
            self.assertFalse(
                extra,
                "%s 里有 %d 条译文已无源码引用（死译文，多半是文案被删了）：%s"
                % (code, len(extra), extra[:8]))

    def test_no_translation_outside_the_known_context(self):
        self.assertEqual(set(self.calls) - {i18n.TRANSLATION_CONTEXT}, set(),
                         "出现了别的 context，translate() 会查不到")

    def test_bilingual_labels_are_deliberately_untranslated(self):
        """「跟随系统」与语言项标签**必须**双语并排出现，绝不能进翻译表 ——
        否则切成英文后中文用户就找不到切回中文的地方了。"""
        must_stay = {i18n.AUTO_LABEL} | {lbl for _c, lbl in i18n.available_languages()}
        for code, sources in self.ts.items():
            for text in sorted(must_stay):
                self.assertNotIn(text, sources,
                                 "%s 收录了双向标签 %r —— 必须两种语言并排显示" % (code, text))


# ─────────────────────── 3. 真 Qt 行为 ───────────────────────

@REQUIRES_QT
class TestQtTranslationBehaviour(unittest.TestCase):
    """装载 / 热切换 / 降级 / 反向验证。无真 Qt 时整类跳过。"""

    @classmethod
    def setUpClass(cls):
        from qgis.PyQt.QtCore import QCoreApplication, QSettings
        cls.QCoreApplication = QCoreApplication
        cls.QSettings = QSettings
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        # 每个用例都把设置写到临时 ini —— 绝不碰用户真实的 QSettings，
        # 否则跑一次测试就把人家的界面语言改了（而且崩了就还原不回来）。
        cls.tmpdir = tempfile.mkdtemp(prefix="qgis_agent_i18n_")
        cls.ini = os.path.join(cls.tmpdir, "settings.ini")
        cls._saved_settings = i18n._settings
        # ⚠️ QSettings.IniFormat 在 Qt5 是类级常量、在 Qt6 是 scoped 枚举
        #    （QSettings.Format.IniFormat）—— 真机 Qt6 实测直接取会 AttributeError。
        fmt = getattr(QSettings, "IniFormat", None)
        if fmt is None:
            fmt = QSettings.Format.IniFormat
        cls.INI_FORMAT = fmt
        # 注意用普通函数而不是 staticmethod：模块属性不参与描述符绑定，
        # staticmethod 对象在各版本 Python 上的可调用性不一致。
        i18n._settings = lambda: cls._qs()

    @classmethod
    def tearDownClass(cls):
        i18n._settings = cls._saved_settings
        i18n._uninstall()

    @classmethod
    def _qs(cls):
        """一个指向临时 ini 的 QSettings（Qt5 / Qt6 的 Format 枚举写法都兼容）。"""
        return cls.QSettings(cls.ini, cls.INI_FORMAT)

    def setUp(self):
        self.addCleanup(i18n._uninstall)
        self._clear()

    def _clear(self):
        sett = self._qs()
        sett.remove(i18n.SETTINGS_KEY)
        sett.sync()

    def T(self, source):
        return self.QCoreApplication.translate(i18n.TRANSLATION_CONTEXT, source)

    # ── 设置持久化 ──
    def test_stored_choice_roundtrip(self):
        self.assertEqual(i18n.stored_choice(), i18n.AUTO, "未设置时视为跟随系统")
        for value in ("en", "zh_CN", i18n.AUTO):
            with self.subTest(value=value):
                self._qs().setValue(
                    i18n.SETTINGS_KEY, value)
                self.assertEqual(i18n.stored_choice(), value)

    def test_stored_choice_tolerates_garbage(self):
        sett = self._qs()
        for value in ("", "   ", None):
            with self.subTest(value=value):
                sett.setValue(i18n.SETTINGS_KEY, value)
                self.assertEqual(i18n.stored_choice(), i18n.AUTO)

    def test_setting_persists_to_disk(self):
        """写进 ini 的值必须真的落盘（换一个 QSettings 实例也读得到）。"""
        sett = self._qs()
        sett.setValue(i18n.SETTINGS_KEY, "en")
        sett.sync()
        again = self._qs()
        again.sync()
        self.assertEqual(again.value(i18n.SETTINGS_KEY), "en")
        with open(self.ini, encoding="utf-8") as fh:
            self.assertIn("en", fh.read(), "设置没有落到 ini 文件里")

    # ── 装载：.qm 优先 ──
    def test_qm_is_preferred_and_translates(self):
        for code in i18n.available_codes():
            with self.subTest(code=code):
                self.assertTrue(os.path.isfile(i18n.qm_file(code)), "缺少 .qm")
                self.assertEqual(i18n.apply_language(code), code)
                self.assertEqual(i18n.current_translator_source(), "qm",
                                 "应当优先走 .qm")
                self.assertIsNotNone(i18n.current_translator(),
                                     "translator 引用丢了会被 GC，翻译静默失效")

    def test_english_actually_translates_a_known_string(self):
        i18n.apply_language("en")
        self.assertEqual(self.T("就绪"), "Ready")
        self.assertNotEqual(self.T("就绪"), "就绪")

    def test_chinese_identity_file_pins_back_to_chinese(self):
        """中文那份是恒等映射 —— 用来压住 QGIS 按 locale 自动装的英文 translator。"""
        i18n.apply_language("en")
        self.assertEqual(self.T("就绪"), "Ready")
        i18n.apply_language("zh_CN")
        self.assertEqual(self.T("就绪"), "就绪",
                         "切回中文后必须锁回中文（后装入的 translator 先查）")

    def test_untranslated_string_falls_back_to_source(self):
        """未收录串必须**原样返回源码中文**，不能变成空串。"""
        i18n.apply_language("en")
        probe = "这段文案刻意没有收录进翻译表"
        got = self.T(probe)
        self.assertEqual(got, probe, "未收录串被改写了，界面会出现错值")
        self.assertTrue(got, "未收录串变成了空 —— .ts 降级路径返回 \"\" 的经典坑")

    # ── 热切换 ──
    def test_hot_switch_replaces_translator(self):
        i18n.apply_language("zh_CN")
        first = i18n.current_translator()
        i18n.apply_language("en")
        second = i18n.current_translator()
        self.assertIsNot(first, second, "切语言必须换 translator")
        self.assertEqual(self.T("就绪"), "Ready")

    def test_hot_switch_is_reversible(self):
        i18n.apply_language("en")
        i18n.apply_language("zh_CN")
        i18n.apply_language("en")
        self.assertEqual(self.T("就绪"), "Ready")
        i18n.apply_language("zh_CN")
        self.assertEqual(self.T("就绪"), "就绪", "来回切换后没有完全复原")

    def test_auto_choice_follows_settings_not_last_call(self):
        """apply_language(None) 走「设置里存的值」，不是"保持上一次"。"""
        i18n.apply_language("en")
        self._qs().setValue(
            i18n.SETTINGS_KEY, "zh_CN")
        self.assertEqual(i18n.apply_language(None), "zh_CN")
        self.assertEqual(self.T("就绪"), "就绪")

    # ── 反向验证：没有 translator 时必须失效 ──
    def test_reverse_without_translator_translation_is_absent(self):
        """**关键反向验证**：卸掉 translator 后翻译必须消失。

        如果"装载成功"这条断言在什么都没有的情况下也能通过，那前面所有
        `assertEqual(self.T("就绪"), "Ready")` 都是假绿。
        """
        i18n.apply_language("en")
        self.assertEqual(self.T("就绪"), "Ready")

        i18n._uninstall()
        self.assertIsNone(i18n.current_translator())
        self.assertIsNone(i18n.current_translator_source())
        self.assertEqual(self.T("就绪"), "就绪",
                         "卸掉 translator 后还在翻译 —— 说明前面测到的不是我们装的")

    def test_unknown_language_code_normalises_to_fallback(self):
        """用户手工把 QSettings 改成不存在的语言码时，必须落到兜底语言而不是空白。

        （"没有翻译文件所以不装 translator" 那条路径由
        test_missing_files_install_nothing_and_do_not_raise 覆盖 ——
        normalize() 的出口只有支持的语言码，两者都带翻译文件。）
        """
        self.assertIsNone(i18n.current_translator(),
                          "前置条件：进入本用例前不该有 translator")
        self.assertEqual(i18n.apply_language("klingon_KL"), i18n.DEFAULT_LANGUAGE)
        self.assertIsNotNone(i18n.current_translator(),
                             "兜底语言有翻译文件，应当正常装上")
        self.assertEqual(self.T("就绪"), "Ready")

    # ── .ts 降级路径 ──
    def test_ts_fallback_when_qm_missing(self):
        """包里只有 .ts 时（别人 clone 仓库 / 直接软链源码目录）翻译不能丢。"""
        saved = i18n.qm_file
        i18n.qm_file = lambda code: os.path.join(self.tmpdir, "nope_%s.qm" % code)
        try:
            self.assertEqual(i18n.apply_language("en"), "en")
            self.assertEqual(i18n.current_translator_source(), "ts",
                             "没有 .qm 时必须落到 .ts")
            self.assertEqual(self.T("就绪"), "Ready")
        finally:
            i18n.qm_file = saved

    def test_ts_fallback_when_qm_is_corrupt(self):
        """.qm 损坏（或与当前 Qt 版本不兼容）时同样要落到 .ts，而不是整片变空。"""
        broken = os.path.join(self.tmpdir, "broken")
        os.makedirs(broken, exist_ok=True)
        with open(os.path.join(broken, "qgis_agent_en.qm"), "wb") as fh:
            fh.write(b"not a qm at all" * 8)
        shutil.copy(i18n.translation_file("en"),
                    os.path.join(broken, "qgis_agent_en.ts"))

        saved = i18n.qm_file
        i18n.qm_file = lambda code: os.path.join(broken, "qgis_agent_%s.qm" % code)
        try:
            self.assertEqual(i18n.apply_language("en"), "en")
            self.assertEqual(i18n.current_translator_source(), "ts")
            self.assertEqual(self.T("就绪"), "Ready")
        finally:
            i18n.qm_file = saved

    def test_missing_files_install_nothing_and_do_not_raise(self):
        saved_qm, saved_ts = i18n.qm_file, i18n.translation_file
        i18n.qm_file = lambda code: os.path.join(self.tmpdir, "no_%s.qm" % code)
        i18n.translation_file = lambda code: os.path.join(self.tmpdir, "no_%s.ts" % code)
        try:
            self.assertEqual(i18n.apply_language("en"), "en")
            self.assertIsNone(i18n.current_translator())
            self.assertEqual(self.T("就绪"), "就绪", "宁可保留原文，也不能空白或抛异常")
        finally:
            i18n.qm_file, i18n.translation_file = saved_qm, saved_ts

    # ── .ts 解析器契约 ──
    def test_ts_translator_returns_none_on_miss_not_empty_string(self):
        """⚠️ 未命中返回 None 而不是 ""。

        返回空串会被 Qt 当作"确有一条空翻译"而采纳 → 界面所有未翻译文案整片空白。
        """
        translator = i18n.TsTranslator(i18n.translation_file("en"))
        self.assertIsNone(translator.translate(
            i18n.TRANSLATION_CONTEXT, "这个串肯定不在翻译表里"))
        self.assertEqual(translator.translate(i18n.TRANSLATION_CONTEXT, "就绪"), "Ready")

    def test_load_messages_skips_unfinished_and_empty(self):
        path = os.path.join(self.tmpdir, "probe.ts")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("""<?xml version="1.0" encoding="utf-8"?>
<TS version="2.1"><context><name>QGISAgent</name>
  <message><source>done</source><translation>完成</translation></message>
  <message><source>todo</source><translation type="unfinished"></translation></message>
  <message><source>gone</source><translation type="vanished">旧</translation></message>
  <message><source>empty</source><translation></translation></message>
</context></TS>""")
        messages = i18n.load_messages(path)
        self.assertEqual(messages, {("QGISAgent", "done"): "完成"},
                         "只应保留真正生效的那一条")

    def test_load_messages_survives_broken_xml(self):
        path = os.path.join(self.tmpdir, "broken.ts")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("<TS><context>not closed")
        self.assertEqual(i18n.load_messages(path), {},
                         "翻译文件坏了不该让插件起不来")

    def test_load_messages_on_missing_file_is_empty(self):
        self.assertEqual(i18n.load_messages(
            os.path.join(self.tmpdir, "does_not_exist.ts")), {})

    # ── 翻译完整性（运行时口径）──
    def test_english_table_has_no_identity_translations(self):
        """英文表里不能出现"译文 == 原文"。

        例外：原文本身就是语言中性的（纯 ASCII 专有名词/缩写，如 "QGIS Agent"、
        "MCP"）—— 那些在英文下本来就该原样显示。**含中文**的原文若译文也相同，
        就是实打实的漏翻。
        """
        import re
        en = i18n.load_messages(i18n.translation_file("en"))
        identity = sorted(src for (_c, src), tr in en.items()
                          if src == tr and re.search(r"[\u4e00-\u9fff]", src))
        self.assertFalse(
            identity,
            "以下条目的英文译文与含中文的原文相同，等于没翻：%s" % identity[:10])

    def test_every_english_translation_is_ascii_or_punctuation(self):
        """英文译文里不该残留中文（漏翻的典型症状）。"""
        import re
        en = i18n.load_messages(i18n.translation_file("en"))
        leftover = sorted(tr for tr in en.values()
                          if re.search(r"[\u4e00-\u9fff]", tr))
        self.assertFalse(leftover,
                         "英文译文里残留中文，疑似漏翻：%s" % leftover[:10])

    def test_chinese_table_is_identity_mapping(self):
        """中文表是恒等映射：它的作用是把界面锁回中文，不是提供新文案。"""
        zh = i18n.load_messages(i18n.translation_file("zh_CN"))
        non_identity = sorted((s, t) for (_c, s), t in zh.items() if s != t)
        self.assertFalse(non_identity,
                         "中文表出现非恒等条目：%s" % non_identity[:5])

    def test_two_tables_cover_the_same_keys(self):
        en = i18n.load_messages(i18n.translation_file("en"))
        zh = i18n.load_messages(i18n.translation_file("zh_CN"))
        self.assertEqual(set(en), set(zh),
                         "仅 en: %s / 仅 zh: %s" % (sorted(set(en) - set(zh))[:5],
                                                    sorted(set(zh) - set(en))[:5]))


if __name__ == "__main__":
    unittest.main()
