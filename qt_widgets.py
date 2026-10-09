# -*- coding: utf-8 -*-
"""本插件自用的小控件。

两个「文案能自动折行」的控件 —— :class:`WrappingCheckBox` 与
:class:`WrappingPushButton`。它们解决的是同一个问题：**Qt 的按钮类控件
一律不支持折行**，一行长文案会把最小宽度顺着布局一路顶到 dock 上，
表现为「面板拉不小」，且**切换语言后宽度会跳变**。
"""

from qgis.PyQt.QtCore import QEvent, QSize, Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

__all__ = ["WrappingCheckBox", "WrappingPushButton"]


def _advance(font_metrics, text):
    """一行文本的排版宽度（Qt 5.11+ 用 ``horizontalAdvance``，老版本退回 boundingRect）。"""
    advance = getattr(font_metrics, "horizontalAdvance", None)
    if advance is not None:
        return advance(text)
    return font_metrics.boundingRect(text).width()


class _WrappedTextMixin(object):
    """把「一行长文案」换成「内部折行 QLabel」的公共实现。

    三个要点：

    1. C++ 侧文本保持**空串**，否则控件会自己再画一行（不折行、与标签重叠）；
    2. 标签打开 ``WA_TransparentForMouseEvents``，点击直接穿透到控件本身，
       不必自己转发鼠标事件；
    3. 标签带 ``heightForWidth``，而 ``QWidget::hasHeightForWidth()`` 会问布局，
       于是控件高度随折行自动增长，不会裁掉文字。
    """

    #: 左侧留给指示器 / 内边距的宽度。
    GUTTER_LEFT = 22
    GUTTER_RIGHT = 4
    GUTTER_TOP = 0
    GUTTER_BOTTOM = 0

    def _init_wrapped_text(self):
        self._wrapped = QLabel(self)
        self._wrapped.setWordWrap(True)
        self._wrapped.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._wrapped.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction)
        self._wrapped.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 横向可缩（能被压窄才会折行）；纵向交给 heightForWidth 长高
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            self.GUTTER_LEFT, self.GUTTER_TOP,
            self.GUTTER_RIGHT, self.GUTTER_BOTTOM)
        layout.setSpacing(0)
        layout.addWidget(self._wrapped)
        self._sync_wrapped_appearance()   # 起点先对齐一次，之后随 changeEvent 走

    # ── 尺寸提示 ─────────────────────────────────────────────
    #
    # ⚠️ 这两个**必须自己实现**，否则整个控件在**横排布局**里会塌成 19px、
    #    一个字都看不见 —— 这是本控件最容易踩的坑，记录如下：
    #
    #    ``QAbstractButton::sizeHint()``（QCheckBox / QPushButton 都用它）走样式表
    #    计算，**完全不看子布局**：C++ 文本被我们置空了，它按「空文案 + 指示器」
    #    报出 ~19px。而 ``QWidget::minimumSizeHint()`` 虽然会问布局，但折行 QLabel
    #    的 ``minimumSizeHint().width()`` 是 0。
    #
    #    结果：放进 QVBoxLayout（竖排，宽度由父控件给满）一切正常 —— 所以我们先做的
    #    几个复选框都是好的；一旦放进 QHBoxLayout（横排，宽度按 sizeHint 分配），
    #    控件只拿到 19px，标签宽度为 0，**文案整片消失**（实测现场：底部栏的
    #    「跳过确认 / Skip confirmation」变成了一个空方框）。

    def sizeHint(self):  # noqa: N802 - 对齐 Qt 命名
        """偏好尺寸 = 文案**不折行**时的宽度（放得下就一行，放不下才折）。"""
        if getattr(self, "_wrapped", None) is None:   # 初始化过程中的兜底查询
            return super().sizeHint()
        fm = self._wrapped.fontMetrics()
        lines = (self._wrapped.text() or "").split("\n") or [""]
        # ⚠️ 用 horizontalAdvance（排版推进宽度）而不是 boundingRect（墨迹外框）：
        #    后者会略窄。实测「Skip confirmation」按墨迹算 122px 的偏好宽度，
        #    标签实际可用 96px 却仍被折成两行 —— 差的就是这几像素。
        #    再额外留 6px：标签拿到的宽度是「本控件宽度 - 左侧内边距(22) - 右侧(4)」，
        #    正好等于 advance 时仍可能因舍入被折行（实测中文「跳过确认」只余 2px）。
        width = max([_advance(fm, line) for line in lines]) + 6
        height = fm.height() * len(lines)
        return QSize(width + self.GUTTER_LEFT + self.GUTTER_RIGHT,
                     height + self.GUTTER_TOP + self.GUTTER_BOTTOM)

    def minimumSizeHint(self):  # noqa: N802 - 对齐 Qt 命名
        """最小尺寸 = 布局算出来的值，但宽度留出「几个字符」的余量。

        ⚠️ **不能用 ``super().minimumSizeHint()``**：``QCheckBox::minimumSizeHint()``
        内部把自己**委托给 ``sizeHint()``**（Qt5 源码即 ``return sizeHint();``），
        于是上面刚覆盖的 ``sizeHint()``（整行文案宽度）会原样变成最小宽度 ——
        实测四个复选框的最小宽度从 19px 直接涨到 875px，比不修还糟、整页又回到
        900+。「布局算出来的最小尺寸」要向 ``QWidget`` 那一份要（即
        ``layout().totalMinimumSize()``，实测 110px = 最长单词 + 内边距）。

        下限取「3 个平均字符宽」而不是「最长单词」：中文文案**没有空格**，
        按空格切词会把整句当成一个词（实测该页最小宽度飙到 400px+，正好又回到
        我们要修的那个毛病上）。
        """
        if getattr(self, "_wrapped", None) is None:   # 初始化过程中的兜底查询
            return QWidget.minimumSizeHint(self)
        base = QWidget.minimumSizeHint(self)
        fm = self._wrapped.fontMetrics()
        floor_w = 3 * fm.averageCharWidth()
        return QSize(
            max(base.width(), floor_w + self.GUTTER_LEFT + self.GUTTER_RIGHT),
            max(base.height(), fm.height() + self.GUTTER_TOP + self.GUTTER_BOTTOM))

    # ── 字体 / 颜色同步 ─────────────────────────────────────────
    #
    # ⚠️ 不能指望「父控件的样式表自动传到子标签」：实测同一句
    #    ``QCheckBox { font-size: 11px; color: #888; }``，MCP 页的复选框上标签
    #    继承了（灰字 11px），底部栏的「跳过确认」却**没继承**（标签仍是黑字默认
    #    字号）—— 差别在于样式表是构造时设的还是 show 之后设的。与其去猜 Qt 的
    #    传播时机，不如自己同步：字体/调色板一变就抄给标签。

    def changeEvent(self, event):  # noqa: N802 - 对齐 Qt 命名
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.PaletteChange,
                            QEvent.Type.StyleChange):
            self._sync_wrapped_appearance()
        super().changeEvent(event)

    def showEvent(self, event):  # noqa: N802 - 对齐 Qt 命名
        # 样式表里的颜色是在 polish（首次显示）时才落到调色板上的，而这一下落
        # 未必伴随 PaletteChange 事件（实测：``QCheckBox { color: #737373; }``
        # 直接设在复选框上时，标签始终是黑的）。显示时再对齐一次，兜住这种时序。
        self._sync_wrapped_appearance()
        super().showEvent(event)

    def setStyleSheet(self, css):  # noqa: N802 - 对齐 Qt 命名
        super().setStyleSheet(css)
        self._sync_wrapped_appearance()

    def _sync_wrapped_appearance(self):
        """把本控件的字体与调色板抄给内部标签（幂等，可重复调用）。"""
        label = getattr(self, "_wrapped", None)
        if label is None:
            return
        if label.font() != self.font():
            label.setFont(self.font())
        if label.palette() != self.palette():
            label.setPalette(self.palette())

    # ── 文案读写全部代理给内部标签 ──────────────────────────────

    def setText(self, text):  # noqa: N802 - 对齐 Qt 命名
        """设置文案（不调 ``super().setText()``，见类说明第 1 条）。"""
        self._wrapped.setText(text)

    def text(self):
        """返回完整文案（与 ``setText`` 成对，供界面刷新与测试读取）。"""
        return self._wrapped.text()

    def wrapped_label(self):
        """返回内部那个折行标签（验收脚本用来核验折行与可见性）。"""
        return self._wrapped


class WrappingCheckBox(_WrappedTextMixin, QCheckBox):
    """文案能自动折行的复选框。

    —— 为什么需要它 ——

    Qt 的 ``QCheckBox`` **不支持折行**，它的 ``minimumSizeHint().width()``
    就是「指示器 + 整行文字的宽度」。而最小宽度会顺着布局一路往上传
    （控件 → 页面 → ``QTabWidget`` → dock），最后变成**整个面板拉不小**。

    实测（QGIS 3.44.14 / Qt5，``tbSettings`` 页的最小宽度）：

    ============================================================  =======
    文案                                                            最小宽度
    ============================================================  =======
    中文「使用浏览器兼容 TLS（仅当接口连接被网关重置时需要，需 pip install curl_cffi）」   624px
    英文同一句                                                        888px
    ============================================================  =======

    结果是 dock 的最小宽度从 **690px 涨到 931px** —— 用户在英文界面下
    **再也拉不回原来的宽度**（这正是「切到英语后最小宽度变很宽」的真因）。

    改用本控件后，最小宽度变成「**最长单词 + 内边距**」（上面两条从 624/888
    降到 116/127）—— 而文案一字未改，所以**不需要动翻译文件**。
    """

    def __init__(self, text="", parent=None):
        super().__init__("", parent)
        self._init_wrapped_text()
        self.setText(text)

    def hitButton(self, pos):  # noqa: N802 - 对齐 Qt 命名
        """整个控件矩形都算按钮热区。

        ⚠️ **必须自己实现**：``QCheckBox::hitButton`` 是按**文字范围**算热区的
        （``SE_CheckBoxClickRect``），而我们把 C++ 侧文本置空了 —— 不覆盖的话
        只有那个小方框能点，**点文案毫无反应**。（实测踩到：验收脚本里
        「点标签区也能勾选」整组变红。）
        """
        return self.rect().contains(pos)


class WrappingPushButton(_WrappedTextMixin, QPushButton):
    """文案能自动折行的按钮。

    同一个坑的另一处现场：聊天页「空状态」的示例指令按钮。
    英文文案最长一条是 *Compute the area of every administrative region
    and make a classified choropleth map*（中文「统计各行政区面积并生成分级设色地图」），
    实测把 ``tbMessages`` 页的最小宽度从 **324px 顶到 476px** —— 又一处
    「切英文后面板变宽」。

    ``QPushButton`` 的 ``hitButton`` 默认就是「整个矩形」，所以这里不必覆盖；
    但内边距要自己留（样式表里的 ``padding`` 不会自动变成子控件的边距）。
    """

    #: 与 ``QPushButton#qaExampleBtn { padding: 8px 10px; }`` 对齐（外框 1px）
    GUTTER_LEFT = 12
    GUTTER_RIGHT = 12
    GUTTER_TOP = 8
    GUTTER_BOTTOM = 8

    def __init__(self, text="", parent=None):
        super().__init__("", parent)
        self._init_wrapped_text()
        self.setText(text)
