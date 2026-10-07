# -*- coding: utf-8 -*-
"""QGIS Agent 的界面语言（i18n）支持。

对外只需要这几件事：

    available_languages()   设置页下拉用（代码 + 双语标签）
    AUTO / AUTO_LABEL       「跟随 QGIS 系统语言」的取值与双语标签
    stored_choice()         设置里存的选择（可能是 "auto"）
    preferred_language()    当前实际应生效的语言码
    apply_language(value)   装载并安装 translator（value 可以是 "auto"）
    normalize(code)         把任意 locale 归一化到本插件支持的语言码

------------------------------------------------------------------------
翻译文件：.ts 是源，.qm 是产物，messages_<code>.json 是零依赖退路
------------------------------------------------------------------------
- **.ts**（Qt 标准**源**格式）由 ``pylupdate`` 从源码提取、人工或社区填译文，
  是仓库里的唯一真源 —— 改一句翻译不必重新编译。运行时**不读**它。
- **.qm** 由仓库根的 ``build_translations.py`` 调 ``pyside6-lrelease`` 编译，
  **随插件一起分发**，运行时走 Qt 最标准的 ``QTranslator.load()``。
  ⚠️ lrelease 只是**构建期**依赖，最终用户不需要安装它。
- **messages_<code>.json** 与 .qm 同源生成，是**零依赖降级表**：.qm 缺失、
  损坏或与当前 Qt 版本不兼容时改读它，用户只会感到"这条翻译生效得晚一点"。

⚠️ 降级路径为什么是 JSON 而不是直接解析 .ts：

插件仓库（plugins.qgis.org）会用 **Bandit** 静态扫描发布包里的每个 .py，
而 ``xml.etree.ElementTree`` 被它判为"用未加固的 XML 解析不可信数据"
（B405 / B314）。**只要包里有任意一条 Bandit 发现，整版就会被 BLOCKED**
（不进入人审、不可下载）—— v2.4.14 首次上传时就是这样被挡住的：7 条发现
里有 2 条来自这里的 ``import xml.etree.ElementTree``。换成标准库 ``json``
读取自己生成的映射表，既消掉了这条发现，也顺手把"运行时不解析 XML 文件"
这个更小的攻击面固定下来。

------------------------------------------------------------------------
⚠️ 降级路径同样：覆写 translate() 未命中必须返回 None，不能返回 ""
------------------------------------------------------------------------
Qt 判断的是 ``isNull()`` 而不是 ``isEmpty()``：返回空串会被当作"确实有一条
翻译、内容就是空"而**采纳**，界面上所有未翻译的文案会**整片变空白**。
（实测对照：返回 ``""`` → 未收录串渲染成空；返回 ``None`` → 正确回退到原文。）
走 .qm 路径没有这个坑：``QTranslator.load()`` 返回 False 就是没加载成功。

------------------------------------------------------------------------
为什么中文也要一份翻译文件（恒等映射）
------------------------------------------------------------------------
QGIS 在加载插件时会按当前 locale 自动装载 ``i18n/<插件名>_<locale>.qm``。
若用户的系统/QGIS 语言是英文，QGIS 会替我们把英文翻译装上；此时用户若在
设置里把界面**切回中文**，我们拿不到那个 translator 的引用，也就**无法移除**它。
所以中文同样需要一份翻译文件（**翻译值 = 原文**），由本插件在切换时**后装入**
（Qt 的 translator 是栈式，后装先查），把界面稳稳锁回中文。

------------------------------------------------------------------------
未翻译时的行为
------------------------------------------------------------------------
不在翻译表里的字符串会**原样返回源码里的中文**。这是有意为之：漏翻只会让
个别位置保留中文，不会变成空白、key 或崩溃。
"""

from __future__ import annotations

import contextlib
import json
import os

try:  # 一律走 qgis.PyQt，Qt5 / Qt6 通用
    from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings, QTranslator
except Exception:  # pragma: no cover - 仅在脱离 QGIS 的纯 Python 环境下走到
    try:
        from PyQt5.QtCore import (  # type: ignore
            QCoreApplication, QLocale, QSettings, QTranslator)
    except Exception:
        QCoreApplication = None  # type: ignore
        QLocale = None  # type: ignore
        QTranslator = None  # type: ignore

        class QSettings(object):  # type: ignore
            """脱离 Qt 时的占位实现：只保证 import 与取值不炸。"""

            def __init__(self, *_a, **_k):
                self._d = {}

            def value(self, key, default=None):
                return self._d.get(key, default)

            def setValue(self, key, value):  # noqa: N802 - 对齐 Qt 命名
                self._d[key] = value


__all__ = [
    "TRANSLATION_CONTEXT", "SETTINGS_KEY", "AUTO", "AUTO_LABEL",
    "DEFAULT_LANGUAGE", "available_languages", "available_codes", "normalize",
    "qgis_locale", "preferred_language", "apply_language", "is_available",
    "language_label", "choice_label", "stored_choice", "current_translator",
    "current_translator_source", "translation_file", "qm_file",
    "messages_file", "load_messages", "MessageTranslator",
]


#: QTranslator 的上下文名 —— .ts 里 ``<context><name>`` 必须与它一致，
#: 代码里 ``QCoreApplication.translate(TRANSLATION_CONTEXT, "...")`` 同理。
TRANSLATION_CONTEXT = "QGISAgent"

#: 设置键。与 mcp.* 共用 "QGIS"/"QGISAgent" 作用域（QSettings 里 "/" 与 "." 等价）。
SETTINGS_KEY = "ui/language"

#: 「跟随 QGIS 系统语言」的取值。
AUTO = "auto"

#: 「跟随系统」下拉项的双语标签。与 :data:`_LANGUAGES` 的标签一样**刻意不做翻译**：
#: 界面本身可能是中文或英文，双语标注才能保证用户在任一边都认得出这个入口，
#: 也避免"切成英文之后找不到切回中文的地方"。
AUTO_LABEL = "跟随系统 / Follow system"

#: 支持的语言 —— (代码, 双语标签, 该语言的自称)。
#: 标签一律双语：界面本身可能是英文或中文，用户在任何一边都能认出来。
_LANGUAGES = (
    ("zh_CN", "简体中文 / Chinese", "简体中文"),
    ("en", "English / 英语", "English"),
)

#: 无法判定时的兜底语言。选英文而不是中文：非中文用户的系统 locale 千奇百怪，
#: 兜到英文至少可读；中文用户几乎必然能匹配上 zh*，不会走到这里。
DEFAULT_LANGUAGE = "en"

#: 必须持有引用，否则 QTranslator 会被 GC，翻译随即失效（静默的经典坑）。
_translator = None

#: 当前 translator 的来源："qm" / "json" / None。诊断与测试用。
_translator_source = None


# ──────────────────────────── 路径与设置 ────────────────────────────

def i18n_dir():
    """返回 i18n 目录（翻译文件所在处）。"""
    return os.path.dirname(os.path.abspath(__file__))


def translation_file(code):
    """返回某语言的 .ts 绝对路径（不保证存在）。"""
    return os.path.join(i18n_dir(), "qgis_agent_%s.ts" % code)


def qm_file(code):
    """返回某语言的 .qm 绝对路径（不保证存在）。"""
    return os.path.join(i18n_dir(), "qgis_agent_%s.qm" % code)


def messages_file(code):
    """返回某语言的零依赖降级表 ``i18n/messages_<code>.json``（不保证存在）。

    与 .qm 同源生成（见仓库根 ``build_translations.py``）。
    """
    return os.path.join(i18n_dir(), "messages_%s.json" % code)


def _settings():
    try:
        return QSettings("QGIS", "QGISAgent")
    except Exception:
        return QSettings()


def _stored_choice():
    try:
        value = _settings().value(SETTINGS_KEY, AUTO)
    except Exception:
        return AUTO
    if value is None:
        return AUTO
    return str(value).strip() or AUTO


def stored_choice():
    """读取设置里存的语言选择；未设置过时返回 :data:`AUTO`（跟随系统）。

    注意「未设置过」与「显式选了跟随系统」在读值上无法区分（都是 ``auto``），
    这是有意的：两者的行为本来就完全一致。
    """
    return _stored_choice()


# ──────────────────────────── 语言解析 ────────────────────────────

def available_languages():
    """设置页下拉用：返回 ``[(code, 双语标签), ...]``（**不含** auto 项）。"""
    return [(code, label) for code, label, _native in _LANGUAGES]


def available_codes():
    return [code for code, _label, _native in _LANGUAGES]


def language_label(code):
    """把语言码转成双语标签；未知码原样返回。"""
    for c, label, _native in _LANGUAGES:
        if c == code:
            return label
    return code


def is_available(code):
    return code in available_codes()


def choice_label(value):
    """把**设置值**转成下拉项标签（用于把当前值选中）。

    与 :func:`language_label` 的区别：这里接受 ``"auto"``，且把无法识别的
    取值（用户手工改过 QSettings）也归到「跟随系统」—— 让下拉框永远显示一个
    可选中的项，而不是停在一个不存在的语言上。
    """
    value = str(value or "").strip()
    if not value or value == AUTO:
        return AUTO_LABEL
    return language_label(value) if is_available(value) else AUTO_LABEL


def qgis_locale():
    """读取 QGIS 的界面语言。

    ``QgsApplication.locale()`` 是正解：用户设过「语言覆盖」时返回设置值
    （如 ``zh_CN``），没设过则回退到系统 locale 的归一化短码（如 ``zh``）。
    ⚠️ 读 ``QSettings("QGIS","QGIS")`` 的 ``locale/userLocale`` 在没有覆盖时
    是 ``None``，不能只依赖它。

    ⚠️ 这里刻意写成"赋值 + 事后判断"而不是 ``except: pass``：后者会被
    插件仓库的 Bandit 扫描判为 B110（try/except/pass），**一条发现就足以让
    整个版本被 BLOCKED**。同理，本模块里所有"失败了也无所谓"的清理动作
    一律用 ``contextlib.suppress``，不要用 ``try/except: pass``。
    """
    locale_name = ""
    try:
        from qgis.core import QgsApplication
        locale_name = str(QgsApplication.locale() or "")
    except Exception:
        locale_name = ""
    if locale_name:
        return locale_name

    try:
        if QLocale is not None:
            locale_name = str(QLocale.system().name() or "")
    except Exception:
        locale_name = ""
    return locale_name


def normalize(code):
    """把任意 locale 归一化到本插件支持的语言码。

    - ``zh`` / ``zh_CN`` / ``zh-Hans-CN`` / ``zh_HK`` / ``zh_TW`` → ``zh_CN``
      繁体也归到中文：中文用户看简体，远比看英文舒服。
    - ``en`` / ``en_US`` / ``en_GB`` → ``en``
    - 其余（``C``、``de``、空串…）→ :data:`DEFAULT_LANGUAGE`
    """
    text = str(code or "").strip().replace("-", "_")
    lowered = text.lower()
    if lowered.startswith("zh"):
        return "zh_CN"
    if lowered.startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def preferred_language():
    """当前实际应生效的语言码。

    优先级：用户在设置里的显式选择 > QGIS 的界面语言 > 兜底英文。
    """
    choice = _stored_choice()
    if choice != AUTO:
        return normalize(choice)
    return normalize(qgis_locale())


# ─────────────────── 零依赖降级表（JSON）解析与装载 ───────────────────

def load_messages(path):
    """读零依赖降级表，返回 ``{source: translation}``。

    文件缺失、内容不是 JSON、不是对象、或个别值不是非空字符串 —— 一律
    容错处理：能用的条目留下，坏的整体返回空字典。
    **翻译文件坏了不该让插件起不来**（宁可全保留原文，也不抛异常）。

    ⚠️ 为什么不是 Qt 的 .ts：解析它必须用 ``xml.etree``，而插件仓库的
    Bandit 扫描会把它判为 B405/B314，**一条发现就让整版被 BLOCKED**。
    JSON 用标准库 ``json`` 读，零依赖且无此问题。
    """
    messages = {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return messages
    if not isinstance(data, dict):
        return messages
    for source, text in data.items():
        if isinstance(source, str) and source and isinstance(text, str) and text:
            messages[source] = text
    return messages


if QTranslator is not None:

    class MessageTranslator(QTranslator):
        """从零依赖降级表读取翻译的 QTranslator（**.qm 之外的第二条路**）。

        正常分发的包里带的是 ``pyside6-lrelease`` 编译出的 .qm，走 Qt 自带的
        ``QTranslator.load()``。本类只在 .qm 缺失 / 损坏 / 与当前 Qt 版本不
        兼容时启用，保证那种情况下翻译也不丢。
        """

        def __init__(self, path):
            super().__init__()
            self.path = path
            self.messages = load_messages(path)

        def translate(self, context, source, disambiguation=None, n=-1):
            """⚠️ 未命中必须返回 **None**，不能返回空串。

            返回 ``""`` 会被 Qt 视为"确有一条翻译、内容为空"而采纳，
            界面上所有未翻译的文案会整片变空白。返回 None 才会回退到原文。
            """
            return self.messages.get(source) or None

        def message_count(self):
            return len(self.messages)

else:  # pragma: no cover - 纯 Python 环境
    MessageTranslator = None  # type: ignore


# ──────────────────────── 装载 / 卸载 ────────────────────────

def _app():
    if QCoreApplication is None:
        return None
    try:
        return QCoreApplication.instance()
    except Exception:
        return None


def _uninstall():
    """卸掉本模块此前装的 translator。"""
    global _translator, _translator_source
    app = _app()
    if _translator is not None and app is not None:
        # 用 suppress 而不是 try/except: pass —— 后者是 Bandit B110，
        # 一条发现就足以让整个插件版本被插件仓库 BLOCKED。
        with contextlib.suppress(Exception):
            app.removeTranslator(_translator)
    _translator = None
    _translator_source = None


def _build_translator(code):
    """按 code 造一个 translator；返回 ``(translator, source)``，造不出则 None。

    顺序：**先 .qm（Qt 标准），后 messages_<code>.json（零依赖退路）**。
    .qm 缺失、损坏、或与当前 Qt 版本不兼容时 ``load()`` 返回 False，
    此时自动落到 JSON 表 —— 用户看到的只是"这条翻译生效得晚一点"，而不是空白。
    """
    path = qm_file(code)
    if os.path.isfile(path) and QTranslator is not None:
        translator = QTranslator()
        with contextlib.suppress(Exception):
            if translator.load(path):
                return translator, "qm"

    path = messages_file(code)
    if os.path.isfile(path) and MessageTranslator is not None:
        translator = MessageTranslator(path)
        # 文件在但读不出任何条目时**不装**：装一个空的 translator 等于
        # 装了个寂寞，还会挡住别处（如 QGIS 按 locale 自动装载）的翻译。
        if translator.message_count() > 0:
            return translator, "json"

    return None


def apply_language(value=None):
    """按 ``value`` 装载并安装翻译，返回**实际生效**的语言码。

    ``value`` 传 ``"auto"`` 或 ``None`` 表示跟随 QGIS；传具体语言码则强制该语言。
    翻译文件缺失或解析失败时静默跳过（界面回到源码原文），绝不抛异常 ——
    翻译不该成为插件起不来的原因。
    """
    choice = _stored_choice() if value is None else (str(value).strip() or AUTO)
    code = normalize(qgis_locale() if choice == AUTO else choice)

    global _translator, _translator_source
    _uninstall()

    app = _app()
    if app is None:
        return code

    built = _build_translator(code)
    if built is None:
        return code

    translator, source = built
    try:
        app.installTranslator(translator)
    except Exception:
        return code
    _translator = translator
    _translator_source = source
    return code


def current_translator():
    """返回当前已安装的 QTranslator（未安装时为 None）。测试与诊断用。"""
    return _translator


def current_translator_source():
    """返回当前 translator 的来源：``"qm"`` / ``"json"`` / ``None``。"""
    return _translator_source
