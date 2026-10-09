# -*- coding: utf-8 -*-
"""界面宽度与折行控件的回归守卫。

守的是两个**真机上肉眼可见**的事故（2026-10-09 报障）：

1. **切换语言后面板最小宽度跳变**：中文下能拉到 690px 的面板，切成英文后
   最小宽度变成 931px，再也拉不回原来的宽度。根因是几处长文案控件
   （复选框 / 示例按钮 / 挤在一行的按钮组）**不支持折行**，英文文案更长
   就顺着布局把最小宽度一路顶上去。

2. **点 × 关闭面板再点图标重开，设置页的栏目越开越多**：点 × 只调了
   ``removeDockWidget``（控件对象没销毁），而 ``run()`` 会再次走
   ``_init_plugin()`` → 又 insert 一遍分组、又 connect 一遍信号。

这里分两层守：

* **源码级**（不需要 Qt，任何环境都跑）：控件选型、布局拆分、常量取值 ——
  这些是「结构对不对」，改坏了立刻红。
* **真 Qt 级**（``REQUIRES_QT``，无真 Qt 自动跳过）：折行控件的尺寸提示、
  热区、字体颜色同步 —— 这些是「行为对不对」，只能实测。
"""

import ast
import os
import unittest

try:  # 既支持包方式导入，也支持 `unittest discover -s tests` 的顶层导入
    from . import support
except ImportError:
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT
BASE_UI = os.path.join(PROJECT_ROOT, "qgis_agent_dockwidget_base_ui.py")
DOCK_V2 = os.path.join(PROJECT_ROOT, "qgis_agent_dockwidget_v2.py")
AGENT = os.path.join(PROJECT_ROOT, "qgis_agent.py")
QT_WIDGETS = os.path.join(PROJECT_ROOT, "qt_widgets.py")
BUILD_PLUGIN = os.path.join(PROJECT_ROOT, "build_plugin.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _parse(path):
    return ast.parse(_read(path), filename=path)


def _calls(tree, func_name):
    """收集所有 ``<func_name>(...)`` 调用节点（按函数名匹配，忽略接收者）。"""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name == func_name:
            found.append(node)
    return found


def _is_translate_call(node):
    """判断实参是不是 ``_translate("ctx", "text")`` / ``tr(...)`` 这类翻译调用。"""
    if not isinstance(node, ast.Call):
        return False
    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
    return name in ("_translate", "translate", "tr")


def _layout_items(src, layout_name):
    """按源码顺序列出某条布局 ``addWidget`` 进去的控件名。"""
    import re
    pattern = r"self\.%s\.addWidget\(\s*(self\.\w+)" % layout_name
    return re.findall(pattern, src)


# ────────────────────── 1. 折行控件本身 ──────────────────────


class TestWrappingWidgetsExist(unittest.TestCase):
    """``qt_widgets`` 必须随包分发，且两个折行控件都在。"""

    def test_file_shipped(self):
        self.assertTrue(os.path.exists(QT_WIDGETS), "qt_widgets.py 不存在")

    def test_build_plugin_includes_it(self):
        """打包脚本必须收下这个文件（``*.py`` 白名单）—— 漏了插件就 import 失败。"""
        sys_path = [PROJECT_ROOT, os.path.dirname(PROJECT_ROOT)]
        saved = list(__import__("sys").path)
        try:
            __import__("sys").path[:0] = sys_path
            import importlib
            build_plugin = importlib.import_module("build_plugin")
        except Exception as exc:  # noqa: BLE001
            self.skipTest("build_plugin 无法导入：%s" % exc)
        finally:
            __import__("sys").path[:] = saved
        should_include = getattr(build_plugin, "should_include", None)
        if should_include is None:
            self.skipTest("build_plugin.should_include 不存在")
        self.assertTrue(should_include("qt_widgets.py"),
                        "qt_widgets.py 没被包含进发布包")

    def test_classes_defined(self):
        names = {
            node.name for node in ast.walk(_parse(QT_WIDGETS))
            if isinstance(node, ast.ClassDef)
        }
        for want in ("_WrappedTextMixin", "WrappingCheckBox", "WrappingPushButton"):
            self.assertIn(want, names)
        src = _read(QT_WIDGETS)
        # 折行控件最容易漏的三件事，每一件都踩过
        self.assertIn("def hitButton", src, "复选框丢了整矩形热区")
        self.assertIn("WA_TransparentForMouseEvents", src, "标签没开鼠标穿透")
        self.assertIn("def sizeHint", src, "没实现 sizeHint，横排布局里会塌成 19px")
        self.assertIn("QWidget.minimumSizeHint(self)", src,
                      "minimumSizeHint 必须向 QWidget 要，不能走 QCheckBox（它会转调 sizeHint）")


# ────────────────────── 2. 界面文案控件选型 ──────────────────────


class TestNoBareCheckBoxWithTranslatedText(unittest.TestCase):
    """所有「文案来自翻译」的复选框都必须是折行复选框。

    规则很硬但很好用：**译文会比原文长**（英文普遍比中文长 30%~70%），
    而 ``QCheckBox`` 不折行，一行长文案就是一条最小宽度。曾经现场：
    TLS 说明 888px、MCP 特权说明 883px、跳过确认 135px。
    """

    def test_shipped_code(self):
        offenders = []
        for path in _shipped_python_files():
            for call in _calls(_parse(path), "QCheckBox"):
                if any(_is_translate_call(arg) for arg in call.args):
                    offenders.append("%s:%d" % (os.path.basename(path), call.lineno))
        self.assertEqual(
            offenders, [],
            "这些复选框的文案来自翻译却没折行：%s\n"
            "改用 qt_widgets.WrappingCheckBox（否则英文下会把面板顶宽）" % offenders)


def _shipped_python_files():
    """发布包里的 .py（排除 tests / build_translations / 打包脚本自身）。

    ⚠️ 必须跳过 ``._`` 开头的 macOS AppleDouble 元数据文件：它们也以 ``.py``
    结尾（``._qgis_agent.py``），但是**二进制**，按文本读会 UnicodeDecodeError。
    打包脚本的 ``EXCLUDE_PATTERNS`` 里已经有 ``._*``，这里保持一致。
    """
    skip = {"build_translations.py", "build_plugin.py", "install_v2.py",
            "generate_icon.py", "test_official_docs.py", "import_tool_docs.py"}
    out = []
    for name in sorted(os.listdir(PROJECT_ROOT)):
        if not name.endswith(".py") or name.startswith("._") or name in skip:
            continue
        out.append(os.path.join(PROJECT_ROOT, name))
    return out


# ────────────────────── 3. 布局拆分 ──────────────────────


class TestChatBottomBars(unittest.TestCase):
    """对话页底部两条栏：模型一行、温度+跳过确认一行。

    曾经六件套（模型 + 下拉 + 温度 + 滑杆 + 数值 + 跳过确认）挤一行，
    英文下该行 440px、中文 324px —— 页最小宽度直接跟着语言走。
    """

    def setUp(self):
        self.tree = _parse(BASE_UI)
        self.src = _read(BASE_UI)

    def test_two_bars_defined(self):
        self.assertIn("self.bottomBarLayout = QtWidgets.QHBoxLayout()", self.src)
        self.assertIn("self.tempBarLayout = QtWidgets.QHBoxLayout()", self.src)

    def test_both_bars_added_in_order(self):
        idx_bar = self.src.index("self.messagesLayout.addLayout(self.bottomBarLayout)")
        idx_temp = self.src.index("self.messagesLayout.addLayout(self.tempBarLayout)")
        self.assertLess(idx_bar, idx_temp, "温度栏必须在模型栏之后")

    def test_model_bar_holds_only_model(self):
        """模型栏只放「模型 + 下拉」；温度相关控件必须在温度栏里。"""
        self.assertEqual(_layout_items(self.src, "bottomBarLayout"),
                         ["self.lblModel", "self.cbModelSelector"])
        self.assertEqual(_layout_items(self.src, "tempBarLayout"),
                         ["self.lblTemperature", "self.sliderTemperature",
                          "self.lblTempValue", "self.cbSkipConfirm"])

    def test_dock_v2_checks_temp_bar_in_order(self):
        """v2 的装配自检要认得温度栏，否则顺序错了没人报警。"""
        src = _read(DOCK_V2)
        self.assertIn("tempBarLayout", src)
        self.assertIn("i_bar <= i_temp < i_footer", src)


class TestMcpTokenRowSplit(unittest.TestCase):
    """MCP 页「访问令牌」三个按钮另起一行（英文下五件套一行 = 447px）。"""

    def test_buttons_not_in_token_row(self):
        src = _read(AGENT)
        row = src.split("row_token = QHBoxLayout()")[1]
        row = row.split("outer.addLayout(row_token)")[0]
        for name in ("self.btnMcpTokenReveal", "self.btnMcpRegen",
                     "self.btnMcpCopyToken"):
            self.assertNotIn(name, row, "%s 还在令牌行里" % name)
        btns = src.split("row_token_btns = QHBoxLayout()")[1]
        btns = btns.split("outer.addLayout(row_token_btns)")[0]
        for name in ("self.btnMcpTokenReveal", "self.btnMcpRegen",
                     "self.btnMcpCopyToken"):
            self.assertIn(name, btns)


# ────────────────────── 4. 语言无关的最小宽度 ──────────────────────


class TestPageMinWidthFloor(unittest.TestCase):
    """``_DockTabWidget.PAGE_MIN_W``：让面板最小宽度与界面语言无关。

    做法是给**每一页**一个固定下限（``setMinimumWidth``）。只要各页内容都
    不超过它，面板最小宽度就恒等于同一个值，中英文一模一样。
    """

    def setUp(self):
        self.src = _read(BASE_UI)

    def test_constant_declared(self):
        self.assertRegex(self.src, r"PAGE_MIN_W\s*=\s*340")

    def test_applied_on_add_tab(self):
        """必须在 addTab 里统一装，而不是逐个页面手写（新页会自动享受）。"""
        block = self.src.split("def addTab(self, widget, *args)")[1]
        block = block.split("def ", 1)[0]
        self.assertIn("setMinimumWidth(self.PAGE_MIN_W)", block)

    def test_dock_declared_minimum_still_360(self):
        """dock 的 ``setMinimumSize(360, 500)`` 是那条下限回路的关键一环：
        PAGE_MIN_W(340) + 页签边框(4) + 两侧边距(12) = 356 ≤ 360，于是面板的
        最小宽度由这个**常量**决定，而不是由内容决定。"""
        self.assertIn("setMinimumSize(360, 500)", self.src)


# ────────────────────── 5. 点 × 重开不叠加 ──────────────────────


class TestDockUiBuiltOnce(unittest.TestCase):
    """控件构建与信号连接**整进程只做一次**，否则重开面板栏目会叠加。"""

    def setUp(self):
        self.src = _read(AGENT)

    def test_gate_exists(self):
        self.assertIn("_dock_ui_ready", self.src)
        self.assertIn("def _setup_dock_ui", self.src)

    def test_init_plugin_gated(self):
        block = self.src.split("def _init_plugin(self)")[1].split("\n    def ", 1)[0]
        self.assertIn("_dock_ui_ready", block,
                      "_init_plugin 里没有闸门，重开面板又会长出一套控件")

    def test_settings_tab_init_inside_setup(self):
        """``_init_settings_tab`` 必须只出现在 ``_setup_dock_ui`` 里（闸门内）。"""
        setup = self.src.split("def _setup_dock_ui(self)")[1].split("\n    def ", 1)[0]
        self.assertIn("_init_settings_tab", setup)
        init_plugin = self.src.split("def _init_plugin(self)")[1].split("\n    def ", 1)[0]
        self.assertNotIn("_init_settings_tab", init_plugin)


# ────────────────────── 6. 真 Qt 行为（无真 Qt 跳过）──────────────────────


def _qt_widgets_or_skip():
    try:
        support.install_runtime_stubs()
        from qgis.PyQt.QtWidgets import QApplication  # noqa: F401
    except Exception:  # noqa: BLE001
        return None
    return __import__("qgis_agent.qt_widgets", fromlist=["qt_widgets"])


QT_MOD = None
try:
    support.ensure_project_path()
    QT_MOD = _qt_widgets_or_skip()
except Exception:  # noqa: BLE001
    QT_MOD = None

HAVE_QT = QT_MOD is not None and not str(
    getattr(QT_MOD, "QCheckBox", object)).startswith("<MagicMock")
REQUIRES_QT = unittest.skipUnless(
    HAVE_QT, "需要真 Qt（桩环境下 QLabel 是 MagicMock，量不出真实尺寸）")


@REQUIRES_QT
class TestWrappingCheckBoxBehaviour(unittest.TestCase):
    """真 Qt 下的尺寸 / 热区 / 字体同步。"""

    LONG_EN = ("Allow external agents to call privileged tools (run PyQGIS code / "
               "processing algorithms / delete layers / run skills)")
    SHORT = "Skip confirmation"

    def setUp(self):
        from qgis.PyQt.QtWidgets import QApplication
        # 裸跑单测时可能还没有 QApplication（真机 QGIS 里一定已经有）。
        self.app = QApplication.instance() or QApplication([])
        self.box = QT_MOD.WrappingCheckBox(self.LONG_EN)

    def test_size_hint_is_single_line_width(self):
        """偏好宽度 = 整行文案宽度（放得下就一行，放不下才折）。"""
        fm = self.box.wrapped_label().fontMetrics()
        self.assertGreaterEqual(
            self.box.sizeHint().width(),
            fm.horizontalAdvance(self.LONG_EN),
            "sizeHint 太窄，横排布局里会立刻折行")

    def test_minimum_hint_is_not_the_whole_text(self):
        """最小宽度必须远小于整行文案（否则等于没修）。"""
        fm = self.box.wrapped_label().fontMetrics()
        self.assertLess(self.box.minimumSizeHint().width(),
                        fm.horizontalAdvance(self.LONG_EN) // 2)
        self.assertGreater(self.box.minimumSizeHint().width(), 0)

    def test_minimum_hint_is_not_delegated_to_size_hint(self):
        """``QCheckBox::minimumSizeHint`` 会转调 ``sizeHint`` —— 不能被它带跑。"""
        self.assertNotEqual(self.box.minimumSizeHint().width(),
                            self.box.sizeHint().width())

    def test_whole_rect_is_hot(self):
        from qgis.PyQt.QtCore import QPoint
        for pos in (QPoint(1, 1), QPoint(self.box.width() - 1, 1)):
            self.assertTrue(self.box.hitButton(pos))

    def test_label_text_roundtrip(self):
        self.assertEqual(self.box.text(), self.LONG_EN)
        self.box.setText(self.SHORT)
        self.assertEqual(self.box.text(), self.SHORT)
        self.assertEqual(self.box.wrapped_label().text(), self.SHORT)

    def test_font_and_palette_follow_stylesheet(self):
        """样式表写的颜色/字号必须落到内部标签上（标签是子控件，不会自动继承）。"""
        self.box.setStyleSheet("QCheckBox { font-size: 11px; color: #737373; }")
        self.app.processEvents()
        self.box.show()
        self.app.processEvents()
        label = self.box.wrapped_label()
        self.assertEqual(label.font(), self.box.font())
        self.assertEqual(label.palette().color(label.foregroundRole()),
                         self.box.palette().color(self.box.foregroundRole()))
        self.box.hide()


if __name__ == "__main__":
    unittest.main()
