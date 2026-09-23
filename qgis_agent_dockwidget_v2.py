# -*- coding: utf-8 -*-
"""
增强版 DockWidget - 集成可折叠思考显示

主要改进:
1. 思考内容可折叠/展开
2. 更美观的 UI 样式
3. 流式更新支持
"""

import html as html_module
import logging
import math
from datetime import datetime

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt, QElapsedTimer
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QSizePolicy, QSpacerItem, QWidget, QPlainTextEdit,
    QLineEdit, QToolButton, QStackedWidget, QGridLayout, QApplication,
    QComboBox, QFrame
)
from qgis.PyQt.QtGui import QFont, QTextDocument, QTextLayout, QTextOption

from .utils import (handle_none_conversation, pack, unpack, format_description,
                    create_markdown, chat_colors)
from .qgis_agent_dockwidget_base_ui import Ui_QGISAgentDockWidget
from .thinking_display import ThinkingManager, create_thinking_block

logger = logging.getLogger(__name__)

# 思考块定位标记：注释便于阅读，锚点用于定位（QTextBrowser 会丢弃注释，但会保留锚点）
THINKING_MARKER = '<!-- THINKING_BLOCK -->'
THINKING_ANCHOR = '<a name="THINKING_BLOCK"></a>'

# 输入框自适应高度范围（px）
MESSAGE_INPUT_MIN_HEIGHT = 44
MESSAGE_INPUT_MAX_HEIGHT = 140

# 气泡宽度（占聊天区百分比）：
#   Qt 富文本对 `margin-left: 百分比` 支持不稳，实测 `<table width="N%" align="...">`
#   是 Qt5/Qt6 上都可靠的做法，故气泡一律用单列表格实现。
USER_BUBBLE_WIDTH = "78%"
AI_BUBBLE_WIDTH = "88%"


class QGISAgentDockWidgetV2(QtWidgets.QDockWidget, Ui_QGISAgentDockWidget):
    """
    增强版 DockWidget

    改进:
    1. 可折叠的思考内容显示
    2. 更美观的样式设计
    3. 流式更新支持
    """

    closingPlugin = pyqtSignal()
    enterPressed = pyqtSignal(str)
    searchPressed = pyqtSignal(str)
    switchClearMode = pyqtSignal(str)
    stopRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.conversationCards = {}
        self.scrollAreaWidget = None
        self.scrollAreaLayout = None
        self.setupUi(self)

        # 拉伸权重统一由 _init_chat_enhancements 在装配完聊天区后设置
        # （此处不再 setStretch(3, 1)：那个索引在装配后会变成搜索条，造成聊天区拿不到拉伸）

        self.pbStop.clicked.connect(self.stopRequested.emit)

        # 当前对话引用（来自 updateConversation/updateGeneralInfo），用于拿到 processor
        self._live_conversation = None
        # 工作流录制状态（绑定到当前 processor）
        self._is_recording = False

        self.ptMessage.installEventFilter(self)
        self.ptSearchConversationCard.installEventFilter(self)

        self.twTabs.setCurrentWidget(self.tbMessages)

        # 思考内容管理器与累积缓冲
        self._thinking_manager = ThinkingManager()
        self._thinking_buffer = ""
        self._thinking_active = False
        self._last_thinking_text = ""
        # 思考块折叠态（<details> 在 QTextDocument 里不生效，折叠由本类自己实现）
        self._thinking_final_collapsed = True
        self._last_thinking_time = ""

        # 输入框高度随内容自适应（44–140px）
        # 注意：Qt6 已移除 QTextDocument.sizeChanged 信号，改用 QTextEdit.textChanged（Qt5/Qt6 通用）
        self.ptMessage.textChanged.connect(self._adjust_message_input_height)
        self._adjust_message_input_height()

        # 思考块内的「复制 / 展开收起」入口（#copy-thinking / #toggle-thinking）
        if hasattr(self.txHistory, "anchorClicked"):
            self.txHistory.anchorClicked.connect(self._on_history_anchor_clicked)

        # 最近一条 assistant 回复纯文本（U13 复制按钮数据来源）
        self._last_assistant_text = ""
        # 状态条计时器（U18）
        self._timer = QElapsedTimer()

        # ── U11/U13/U18/D5 聊天区增强控件 ──
        self._init_chat_enhancements()

        # ── 工作流录制 / 回放控件 ──
        self._init_workflow_controls()

    def _init_chat_enhancements(self):
        """构建搜索条 / 复制回复 / 状态条 / 空状态示例卡片，并接入现有布局。

        所有新增控件都在 __init__ 内创建，信号连接使用作用域枚举，
        不引用任何不存在的变量；不改动 processor 与其它文件。

        ⚠️ 装配必须用 removeWidget + insertWidget，**不能用 replaceWidget**：
        PyQt5 的 `QLayout.replaceWidget()` 把被替换下来的 QWidgetItem 交给 Python
        持有，调用方不保留返回值时该对象随即被 GC，而 C++ 侧布局项仍指向它 ——
        结果是「要插入的控件」从未真正进入布局（实测 chatStack 变成 640x480 的
        隐藏孤儿窗口，整个对话历史与空状态都看不见，而布局里那一格是空的）。
        装配末尾用 _chat_layout_ok 记录自检结果，供测试与排障使用。
        """
        # ---- 搜索条（默认隐藏，Ctrl+F 唤起）----
        self.searchBar = QLineEdit()
        self.searchBar.setPlaceholderText("搜索对话内容…")
        self.searchBar.setToolTip("Enter 下一处 / Shift+Enter 上一处 / Esc 关闭")
        self.searchBar.textChanged.connect(self._on_search_text_changed)
        self.searchBar.returnPressed.connect(self._on_search_next)
        self.searchBar.installEventFilter(self)

        self.btnSearchPrev = QToolButton()
        self.btnSearchPrev.setText("↑")
        self.btnSearchPrev.setToolTip("上一个匹配")
        self.btnSearchPrev.clicked.connect(self._on_search_prev)
        self.btnSearchNext = QToolButton()
        self.btnSearchNext.setText("↓")
        self.btnSearchNext.setToolTip("下一个匹配")
        self.btnSearchNext.clicked.connect(self._on_search_next)
        self.btnSearchClose = QToolButton()
        self.btnSearchClose.setText("✕")
        self.btnSearchClose.setToolTip("关闭搜索")
        self.btnSearchClose.clicked.connect(self._hide_search_bar)

        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(4)
        search_layout.addWidget(self.searchBar, 1)
        search_layout.addWidget(self.btnSearchPrev)
        search_layout.addWidget(self.btnSearchNext)
        search_layout.addWidget(self.btnSearchClose)
        self.searchFrameWidget = QFrame()
        self.searchFrameWidget.setObjectName("qaSearchBar")
        self.searchFrameWidget.setLayout(search_layout)
        self.searchFrameWidget.setVisible(False)

        # ---- 复制回复按钮（扁平无边框；不用 emoji，该环境下 📋 会渲染成空心方块）----
        self.btnCopyReply = QToolButton()
        self.btnCopyReply.setObjectName("qaCopyReply")
        self.btnCopyReply.setText("复制回复")
        self.btnCopyReply.setToolTip("复制最近一条回复到剪贴板")
        self.btnCopyReply.setAutoRaise(True)
        self.btnCopyReply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btnCopyReply.clicked.connect(self._on_copy_reply)
        self.titleLayout.addWidget(self.btnCopyReply, 0, Qt.AlignmentFlag.AlignRight)

        # ---- 底部状态条（细 footer，与内容区用一条分隔线隔开）----
        self.statusLabel = QLabel("就绪")
        self.statusLabel.setObjectName("qaStatus")
        self.statusLabel.setWordWrap(False)
        self.statusLabel.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._status_base = "就绪"

        self.footerBar = QFrame()
        self.footerBar.setObjectName("qaFooterBar")
        footer_layout = QHBoxLayout(self.footerBar)
        footer_layout.setContentsMargins(2, 0, 2, 0)
        footer_layout.setSpacing(6)
        footer_layout.addWidget(self.statusLabel, 1)

        # ---- 空状态示例卡片 ----
        # 单列而非双列：双列会把空状态区最小宽度撑到 460+，窄 dock 下直接溢出。
        # 只保留 4 条最高频指令，避免空状态一屏塞满按钮抢走输入框的注意力。
        self.emptyStateWidget = QWidget()
        self.emptyStateWidget.setObjectName("qaEmptyState")
        elay = QVBoxLayout(self.emptyStateWidget)
        elay.setContentsMargins(2, 10, 2, 10)
        elay.setSpacing(6)

        self.lblEmptyHint = QLabel("试试这样问：")
        self.lblEmptyHint.setObjectName("qaEmptyHint")
        elay.addWidget(self.lblEmptyHint)

        examples = [
            "列出当前所有图层",
            "统计各行政区面积并生成分级设色地图",
            "把图层重投影到 WGS84",
            "导出当前图层为 GeoPackage",
        ]
        for text in examples:
            btn = QPushButton(text)
            btn.setObjectName("qaExampleBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip("点击填入输入框")
            btn.clicked.connect(lambda _checked=False, t=text: self._on_example_clicked(t))
            elay.addWidget(btn)
        elay.addStretch(1)

        # ---- 聊天区堆叠：历史 / 空状态 二选一显示 ----
        self.chatStack = QStackedWidget()
        self.chatStack.setObjectName("qaChatStack")

        # ---- 装配：removeWidget + insertWidget（见方法说明，勿改回 replaceWidget）----
        # ⚠️ 顺序有讲究：必须**先**取索引、**先**摘出 txHistory，再把它收进 chatStack。
        #    因为 QStackedWidget.addWidget() 会给 txHistory 换父对象，而换父会让 Qt
        #    自动把它从原布局里摘掉 —— 等收完再 indexOf 就得到 -1，插入位置会退化成
        #    「追加到末尾」，结果是输入框与底部栏排在消息区**上面**。
        tx_index = self.messagesLayout.indexOf(self.txHistory)
        if tx_index < 0:
            tx_index = max(0, self.messagesLayout.count() - 1)
        self.messagesLayout.removeWidget(self.txHistory)

        self.chatStack.addWidget(self.txHistory)          # 页 0：历史消息区
        self.chatStack.addWidget(self.emptyStateWidget)   # 页 1：空状态示例

        self.messagesLayout.insertWidget(tx_index, self.chatStack)
        self.messagesLayout.insertWidget(tx_index, self.searchFrameWidget)
        self.messagesLayout.addWidget(self.footerBar)

        # 拉伸权重：只有聊天区吃掉剩余高度，其余按自然高度排布
        chat_index = self.messagesLayout.indexOf(self.chatStack)
        for i in range(self.messagesLayout.count()):
            self.messagesLayout.setStretch(i, 0)
        if chat_index >= 0:
            self.messagesLayout.setStretch(chat_index, 1)

        # 自检顺序不变式：搜索条 → 聊天区 → 输入区 → 底部栏 → 状态条。
        # 顺序错了界面就会「输入框在消息上面」，所以这里把顺序也纳入自检。
        i_search = self.messagesLayout.indexOf(self.searchFrameWidget)
        i_input = self.messagesLayout.indexOf(self.messageFrame)
        i_bar = self.messagesLayout.indexOf(self.bottomBarLayout)
        i_footer = self.messagesLayout.indexOf(self.footerBar)
        self._chat_layout_ok = (
            i_search >= 0 and chat_index >= 0 and i_input >= 0
            and i_search < chat_index < i_input < i_bar < i_footer
        )
        if not self._chat_layout_ok:
            logger.warning("聊天区装配自检失败：search=%s chat=%s input=%s bar=%s footer=%s",
                           i_search, chat_index, i_input, i_bar, i_footer)

        # 监测 txHistory 内容变化以切换空状态/历史视图
        self._txhistory_orig_append = self.txHistory.append
        self._txhistory_orig_sethtml = self.txHistory.setHtml

        def _wrap_append(*args, **kwargs):
            result = self._txhistory_orig_append(*args, **kwargs)
            self._update_empty_state()
            return result

        def _wrap_sethtml(*args, **kwargs):
            result = self._txhistory_orig_sethtml(*args, **kwargs)
            self._update_empty_state()
            return result

        self.txHistory.append = _wrap_append
        self.txHistory.setHtml = _wrap_sethtml

        # 聊天区当前 HTML（原始串，不改道 toHtml()）。
        # 思考流是「原地替换」实现，若每帧都走 toHtml()→setHtml() 往返，Qt 会把
        # 表格单元格的左/右边框、底色等属性在导出时降级，气泡样式会逐帧变淡。
        # 因此这里自己持有原始 HTML，替换只在字符串上做。
        self._chat_html = ""

        # 聊天区整套配色与控件样式（明暗主题自适应）
        self._apply_chat_style()

        # 初始按当前（空）内容决定显示哪一页
        self._update_empty_state()

    # ── 聊天区样式 ──────────────────────────────────────────────────

    def _apply_chat_style(self):
        """按当前调色板给聊天区控件上样式；任何异常都不影响功能。

        配色一律走 utils.chat_colors()（字面色值），因为 QTextDocument 不解析
        CSS 自定义属性，`var(--x)` 会被整条丢弃。
        """
        try:
            c = chat_colors()
        except Exception:
            logger.debug("取聊天区配色失败", exc_info=True)
            return
        self._chat_colors = c

        def _qss(widget, css):
            try:
                widget.setStyleSheet(css)
            except Exception:
                logger.debug("设置样式失败", exc_info=True)

        _qss(self.txHistory, (
            "QTextBrowser { background: %(chat_bg)s; border: 1px solid %(border)s;"
            " border-radius: 4px; padding: 2px; font-size: 13px; }"
            "QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }"
            "QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px;"
            " min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }"
        ) % c)
        # 输入区：容器带边框，聚焦时边框变强调色（由 eventFilter 切动态属性）
        _qss(self.messageFrame, (
            "#messageFrame { background: %(ai_bg)s; border: 1px solid %(border)s;"
            " border-radius: 6px; }"
            "#messageFrame[qaFocus=\"true\"] { border: 1px solid %(user_edge)s; }"
            "#ptMessage { background: transparent; border: none; padding: 2px 4px;"
            " color: %(fg)s; font-size: 13px; }"
            "QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }"
            "QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px;"
            " min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        ) % c)
        _qss(self.statusLabel, "color: %(muted)s; font-size: 11px;" % c)
        _qss(self.footerBar, (
            "#qaFooterBar { border-top: 1px solid %(border)s; }"
        ) % c)
        _qss(self.btnCopyReply, (
            "QToolButton { color: %(muted)s; font-size: 11px; padding: 1px 4px;"
            " border: none; background: transparent; }"
            "QToolButton:hover { color: %(user_edge)s; }"
        ) % c)
        _qss(self.lblEmptyHint, "color: %(muted)s; font-size: 11px;" % c)
        _qss(self.emptyStateWidget, (
            "QPushButton#qaExampleBtn { text-align: left; padding: 8px 10px;"
            " font-size: 12px; border: 1px solid %(border)s; border-radius: 5px;"
            " background: %(ai_bg)s; color: %(fg)s; }"
            "QPushButton#qaExampleBtn:hover { border-color: %(user_edge)s;"
            " color: %(user_edge)s; }"
        ) % c)
        _qss(self.searchBar, (
            "QLineEdit { border: 1px solid %(border)s; border-radius: 4px;"
            " padding: 2px 6px; font-size: 12px; }"
        ) % c)

        # ── 以下是「对话页之外」的通用配色 ──
        # base_ui 里那些写死的 #666 / #888 / #d8dce2 是浅色主题值，深色主题下会暗到
        # 几乎看不见；这里用调色板派生值覆盖，两套主题都不吃亏。
        _qss(self.lbTitle, "font-size: 14px; font-weight: bold; color: %(fg)s;" % c)
        _qss(self.lbDescription, "font-size: 12px; color: %(muted)s;" % c)
        _qss(self.lbMetadata, "font-size: 11px; color: %(muted)s;" % c)
        for widget in (self.lblModel, self.lblTemperature, self.lblTempValue):
            _qss(widget, "font-size: 12px; color: %(muted)s;" % c)
        _qss(self.cbSkipConfirm, "QCheckBox { font-size: 11px; color: %(muted)s; }" % c)
        _qss(self.twTabs, (
            "QTabBar::tab { padding: 5px 8px; font-size: 12px; color: %(fg)s;"
            " background: %(ai_bg)s; }"
            "QTabBar::tab:selected { background: %(chat_bg)s; border-bottom: 2px solid"
            " %(user_edge)s; }"
            "QTabWidget::pane { border: 1px solid %(border)s; }"
        ) % c)

    def _set_chat_html(self, body_html):
        """把聊天区正文写进 QTextBrowser（自动带上公共 CSS），并同步原始缓冲。"""
        self._chat_html = body_html or ""
        self.txHistory.setHtml(self._chat_css() + self._chat_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(
            self.txHistory.verticalScrollBar().maximum()
        )

    def _set_composer_focused(self, focused):
        """输入区聚焦时把容器边框换成强调色（QSS 动态属性 + 重新 polish）。"""
        try:
            self.messageFrame.setProperty("qaFocus", "true" if focused else "false")
            style = self.messageFrame.style()
            style.unpolish(self.messageFrame)
            style.polish(self.messageFrame)
            self.messageFrame.update()
        except Exception:
            logger.debug("切换输入区聚焦样式失败", exc_info=True)

    # ── 消息气泡 ────────────────────────────────────────────────────

    def _chat_css(self):
        """聊天区公共 CSS（字面色值，供气泡内部元素复用）。

        注意：`pre` / `code` 的底色必须用 code_bg（与气泡底色 ai_bg 不同），
        否则代码块和气泡同色，看起来像「没有代码块」。
        """
        c = getattr(self, "_chat_colors", None) or chat_colors()
        return (
            "<style>"
            "body, p { margin: 0; }"
            "a { color: %(user_edge)s; }"
            "pre { background-color: %(code_bg)s; padding: 8px 10px;"
            " border-left: 3px solid %(border)s; white-space: pre-wrap;"
            " word-break: break-word;"
            " font-family: \"SF Mono\", Menlo, Consolas, Monaco, monospace;"
            " font-size: 12px; }"
            "code { background-color: %(code_bg)s; padding: 1px 3px; }"
            "blockquote { border-left: 3px solid %(border)s; margin: 4px 0;"
            " padding: 2px 10px; color: %(muted)s; }"
            "table { border-collapse: collapse; }"
            "th, td { border: 1px solid %(border)s; padding: 4px 8px; }"
            "th { background-color: %(code_bg)s; }"
            "</style>"
        ) % c

    def _bubble_html(self, side, role_text, body_html, is_plain=True):
        """生成一条消息气泡。

        Qt 富文本不支持 border-radius，所以气泡用「底色块 + 关键侧色条」表达，
        配合角色标签与浅色分隔，视觉上仍是一张清晰的卡片。
        """
        c = getattr(self, "_chat_colors", None) or chat_colors()
        if side == "user":
            width, align = USER_BUBBLE_WIDTH, "right"
            bg, edge, bar = c["user_bg"], c["user_edge"], "border-right"
        else:
            width, align = AI_BUBBLE_WIDTH, "left"
            bg, edge, bar = c["ai_bg"], c["ai_edge"], "border-left"

        return (
            '<table width="%(w)s" align="%(align)s" cellpadding="0" cellspacing="0"'
            ' style="margin-top: 6px; margin-bottom: 6px;">'
            '<tr><td style="background-color: %(bg)s; %(bar)s: 3px solid %(edge)s;'
            ' padding: 7px 11px 9px 11px;">'
            '<div style="color: %(muted)s; font-size: 11px; margin-bottom: 3px;">'
            '%(role)s</div>'
            '<div style="color: %(fg)s; line-height: 1.55;">%(body)s</div>'
            '</td></tr></table>'
        ) % {
            "w": width, "align": align, "bg": bg, "bar": bar, "edge": edge,
            "muted": c["muted"], "fg": c["fg"], "role": html_module.escape(role_text or ""),
            "body": body_html,
        }

    def _tool_status_html(self, status_text):
        """工具调用状态：做成一条窄的状态条，而不是与消息同等分量的气泡。"""
        c = getattr(self, "_chat_colors", None) or chat_colors()
        return (
            '<table width="%(w)s" cellpadding="0" cellspacing="0"'
            ' style="margin-top: 2px; margin-bottom: 2px;">'
            '<tr><td style="background-color: %(bg)s; border-left: 3px solid %(edge)s;'
            ' padding: 3px 9px;">'
            '<span style="color: %(edge)s; font-size: 11px;">%(txt)s</span>'
            '</td></tr></table>'
        ) % {"w": AI_BUBBLE_WIDTH, "bg": c["tool_bg"], "edge": c["tool_edge"],
             "txt": html_module.escape(status_text or "")}

    def set_sending_state(self, is_sending):
        """切换发送/停止状态"""
        self.pbSend.setVisible(not is_sending)
        self.pbStop.setVisible(is_sending)
        self.cbModelSelector.setDisabled(is_sending)
        if is_sending:
            # U18：发送开始即启动计时，并给出首个阶段提示
            self._timer.start()
            self._set_status("🤔 思考中…")

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def displayConversationCard(self, dataloader, slots_functions,
                                search_filter=lambda x: True,
                                highlight_rule=lambda x: x):
        self.scrollAreaLayout = QVBoxLayout()
        self.scrollAreaWidget = QWidget()
        self.saConversationCard.setWidget(self.scrollAreaWidget)
        self.scrollAreaWidget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        meta_table = dataloader.select_conversation_info()
        meta_table.sort(key=lambda info: datetime.strptime(info['modified'], "%m %d %Y %H:%M:%S"))

        for meta_info in meta_table:
            if search_filter(meta_info):
                self.addConversationCard(meta_info, slots_functions, highlight=highlight_rule)

        self.scrollAreaWidget.setLayout(self.scrollAreaLayout)

    def updateConversationCard(self, conversation_meta_info, slots_functions):
        conversation_id = conversation_meta_info['ID']
        self.removeConversationCard(conversation_id)
        self.addConversationCard(conversation_meta_info, slots_functions)

    def addConversationCard(self, meta_info, slots_functions, order=0, highlight=lambda x: x):
        on_load, on_delete, on_edit = slots_functions
        card = QGroupBox()
        layout = QVBoxLayout()

        conv_id, llm_id, title, desc, created, modified, msg_count, wf_count, user_id = unpack(meta_info, "conversation")

        title_label = QLabel(highlight(title))
        font = QFont()
        font.setBold(True)
        title_label.setFont(font)

        desc_label = QLabel(highlight(desc))
        desc_label.setWordWrap(True)

        metadata = f"创建: {created} | 模型: {llm_id} | 消息: {msg_count}"
        meta_label = QLabel(metadata)
        meta_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        btn_layout = QHBoxLayout()
        spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        btn_edit = QPushButton("编辑")
        btn_edit.setStyleSheet("QPushButton { background-color: #9DDE8B; }")
        btn_edit.clicked.connect(lambda: on_edit(conv_id))

        btn_delete = QPushButton("删除")
        btn_delete.setStyleSheet("QPushButton { background-color: #FA7070; }")
        btn_delete.clicked.connect(lambda: on_delete(conv_id))

        btn_open = QPushButton("打开")
        btn_open.clicked.connect(lambda: on_load(conv_id))

        btn_layout.addSpacerItem(spacer)
        btn_layout.addWidget(btn_edit)
        btn_layout.addWidget(btn_delete)
        btn_layout.addWidget(btn_open)

        layout.addWidget(title_label)
        layout.addWidget(desc_label)
        layout.addWidget(meta_label)
        layout.addLayout(btn_layout)
        card.setLayout(layout)

        self.scrollAreaLayout.insertWidget(order, card)
        self.conversationCards[conv_id] = card

    def removeConversationCard(self, conversation_id):
        if conversation_id in self.conversationCards:
            card = self.conversationCards[conversation_id]
            self.scrollAreaLayout.removeWidget(card)
            card.deleteLater()
            del self.conversationCards[conversation_id]

    @handle_none_conversation
    def updateGeneralInfo(self, conversation):
        self._live_conversation = conversation
        title = conversation.title or ""
        self.lbTitle.setText(title)
        self.lbTitle.setToolTip(title)
        description = conversation.description or ""
        self.lbDescription.setText(format_description(description))
        description_full = description.strip()
        self.lbDescription.setToolTip(description_full)
        # 描述为空时不要留一行空标签占高度
        self.lbDescription.setVisible(bool(description_full))
        metadata = conversation.get_metadata()
        self.lbMetadata.setText(metadata)
        # 可折行（宽 dock 下仍是一行；窄 dock 下折行，避免顶高 dock 最小宽度）
        self.lbMetadata.setToolTip(metadata)

    @handle_none_conversation
    def updateConversation(self, conversation):
        """更新对话历史显示"""
        # 历史被整体重建，思考缓冲与思考块一并重置
        self.resetThinking()
        # 记录当前对话引用，供工作流录制 / 回放按钮访问 processor
        self._live_conversation = conversation
        # 切换到新对话时复位录制 UI（录制状态绑定到当前 processor）
        self._reset_recording_ui()
        current_html = ""
        self._last_assistant_text = ""  # U13：重置，循环后取最后一条 assistant
        interaction_history = conversation.fetch()
        self._apply_chat_style()

        for interaction in interaction_history:
            msg_dict = pack(interaction, "interaction")
            if msg_dict["typeMessage"] == "input":
                safe_text = html_module.escape(msg_dict["requestText"] or "")
                # 用户输入保留换行（富文本会把 \n 折叠成空格）
                safe_text = safe_text.replace("\n", "<br>")
                current_html += self._bubble_html(
                    "user", "你 · %s" % msg_dict["requestTime"], safe_text
                )

            if msg_dict["typeMessage"] == "return":
                current_html += self._bubble_html(
                    "ai",
                    "QGIS Agent · %s" % msg_dict["responseTime"],
                    create_markdown(msg_dict["responseText"] or ""),
                )
                # U13：记录最近一条 assistant 回复的原始文本（供复制按钮使用）
                self._last_assistant_text = msg_dict["responseText"]

        self._set_chat_html(current_html)

        # U18：最终回复已到达，停止计时并报告耗时
        if self._timer.isValid():
            self._set_status("完成 · 耗时 %s" % self._format_elapsed())

    def showThinking(self, partial_text, response_time=""):
        """
        实时显示思考内容（累积渲染）

        processor 的思考回调每次只传「新增的一小片」文本，这里先累积到
        self._thinking_buffer，再渲染累积后的全文，用户看到的是完整的
        思考流，而不是不断闪烁的最后一行碎片。
        """
        try:
            if not self._thinking_active:
                self._start_thinking(response_time)
                # U18：思考阶段提示
                self._set_status("🤔 思考中…")

            # 累积新增片段（partial_text 可能是 None）
            self._thinking_buffer += partial_text or ""

            # 复用 ThinkingManager：它负责保存累积全文并生成思考块 HTML
            block_html = self._thinking_manager.update(self._thinking_buffer)
            self._render_thinking_block(block_html)
        except Exception as e:
            logger.debug("渲染思考内容失败: %s", e, exc_info=True)

    def finalizeThinking(self):
        """
        完成思考：输出真正的可折叠思考块（默认折叠，可展开，并可复制全文）

        不再截断到 200 字，思考内容完整保留。
        """
        try:
            if not self._thinking_active:
                return

            # ThinkingManager 内部保存的是累积后的完整思考文本
            block_html = self._thinking_manager.finalize()

            # 保留最后一次完整思考内容与时间戳，供块内「复制 / 展开收起」使用
            self._last_thinking_text = self._thinking_buffer
            self._last_thinking_time = getattr(
                self._thinking_manager, "_current_timestamp", "") or ""
            self._thinking_final_collapsed = True

            self._render_thinking_block(block_html)
            self.resetThinking()
        except Exception as e:
            logger.debug("完成思考块渲染失败: %s", e, exc_info=True)

    def resetThinking(self):
        """
        重置思考状态（发送新消息、切换/重建会话时调用）
        """
        self._thinking_buffer = ""
        self._thinking_active = False
        self._thinking_manager.clear()

    def _start_thinking(self, response_time=""):
        """开启新一轮思考：清空缓冲，并准备思考块"""
        self._thinking_buffer = ""
        self._thinking_active = True
        # 只保留当前这一轮思考，避免历史思考块被重复渲染
        self._thinking_manager.clear()
        self._thinking_manager.start(response_time)

    def _locate_thinking_block(self, current_html):
        """
        在完整 HTML 中定位当前思考块

        Returns:
            (start, end) 命中时返回切片区间；未找到返回 (None, None)
        """
        start = current_html.find(THINKING_ANCHOR)
        if start < 0:
            return None, None
        end = current_html.find(THINKING_ANCHOR, start + len(THINKING_ANCHOR))
        if end < 0:
            return None, None
        return start, end + len(THINKING_ANCHOR)

    def _render_thinking_block(self, block_html):
        """把思考块渲染进历史区：原地替换旧块，而不是每次追加一个新块"""
        if not block_html:
            # 思考块生成失败时宁可不渲染，也不能把聊天区冲掉
            logger.debug("思考块 HTML 为空，跳过渲染")
            return
        wrapped = f"{THINKING_MARKER}{THINKING_ANCHOR}{block_html}{THINKING_ANCHOR}{THINKING_MARKER}"
        current_html = self._chat_html

        start, end = self._locate_thinking_block(current_html)
        if start is not None:
            # 原地替换：思考块之后追加的内容（如工具状态）保持不动
            current_html = current_html[:start] + wrapped + current_html[end:]
        else:
            # 找不到旧块就追加到「当前内容」末尾。
            # ⚠️ 不能退回到「思考开始前的快照」：那份快照一旦过期（例如快照还是在
            # 空聊天区时抓的），用它做基线会把期间新增的消息全部抹掉 —— 实测会把
            # 整段对话历史冲成空白。
            current_html = current_html + wrapped

        self._set_chat_html(current_html)

    def _on_history_anchor_clicked(self, url):
        """处理历史区链接点击：

        #copy-thinking  复制思考全文
        #toggle-thinking 展开/收起思考块
        其余链接保持默认行为。
        """
        try:
            if url is None:
                return
            fragment = url.fragment()
            if fragment == "copy-thinking":
                text = self._last_thinking_text or self._thinking_buffer
                if text:
                    QApplication.clipboard().setText(text)
                    self._set_status("已复制思考内容")
            elif fragment == "toggle-thinking":
                self._toggle_thinking_block()
        except Exception as e:
            logger.debug("处理历史区链接失败: %s", e, exc_info=True)

    def _toggle_thinking_block(self):
        """展开 / 收起最后一个思考块（Qt 不支持 <details>，折叠由这里实现）。"""
        if not self._last_thinking_text:
            return
        start, end = self._locate_thinking_block(self._chat_html)
        if start is None:
            return
        self._thinking_final_collapsed = not self._thinking_final_collapsed
        block = create_thinking_block(
            self._last_thinking_text,
            self._last_thinking_time,
            is_final=True,
            collapsed=self._thinking_final_collapsed,
        )
        wrapped = f"{THINKING_MARKER}{THINKING_ANCHOR}{block}{THINKING_ANCHOR}{THINKING_MARKER}"
        self._set_chat_html(self._chat_html[:start] + wrapped + self._chat_html[end:])
        self._set_status("思考内容已收起" if self._thinking_final_collapsed
                         else "思考内容已展开")

    # ── U13/U18/D5 聊天区增强辅助方法 ──

    def _set_status(self, text):
        """U18：写入底部状态条（轻量反馈，不弹窗）。"""
        try:
            self.statusLabel.setText(text)
        except Exception:
            pass

    def _format_elapsed(self):
        """U18：把 QElapsedTimer 耗时格式化为友好字符串。"""
        ms = self._timer.elapsed()
        sec = ms / 1000.0
        if sec < 60:
            return f"{sec:.1f}s"
        minutes = int(sec // 60)
        seconds = int(sec % 60)
        return f"{minutes}m{seconds}s"

    def _update_empty_state(self):
        """D5：根据 txHistory 是否含内容，在「历史视图 / 空状态示例」间切换。"""
        try:
            has_content = bool(self.txHistory.toPlainText().strip())
            target = self.txHistory if has_content else self.emptyStateWidget
            if self.chatStack.currentWidget() is not target:
                self.chatStack.setCurrentWidget(target)
        except Exception:
            pass

    def _show_search_bar(self):
        """U13：显示并聚焦搜索条。"""
        try:
            self.searchFrameWidget.setVisible(True)
            self.searchBar.setFocus()
            self.searchBar.selectAll()
        except Exception:
            pass

    def _hide_search_bar(self):
        """U13：隐藏搜索条并清除高亮。"""
        try:
            self.searchFrameWidget.setVisible(False)
            # 清空搜索高亮：把光标移回起点后做一次空查找
            cursor = self.txHistory.textCursor()
            cursor.setPosition(0)
            self.txHistory.setTextCursor(cursor)
        except Exception:
            pass

    def _on_search_text_changed(self, text):
        """U13：文本变化时从顶部重新定位第一个匹配并高亮。"""
        try:
            if not text:
                return
            cursor = self.txHistory.textCursor()
            cursor.setPosition(0)
            self.txHistory.setTextCursor(cursor)
            self.txHistory.find(text)
        except Exception:
            pass

    def _on_search_next(self):
        """U13：定位下一个匹配（Enter 触发）。"""
        try:
            text = self.searchBar.text()
            if text:
                self.txHistory.find(text)
        except Exception:
            pass

    def _on_search_prev(self):
        """U13：定位上一个匹配。"""
        try:
            text = self.searchBar.text()
            if text:
                self.txHistory.find(text, QTextDocument.FindFlag.FindBackward)
        except Exception:
            pass

    def _get_last_assistant_text(self):
        """U13：返回最近一条 assistant 回复纯文本，取不到返回空串。"""
        return getattr(self, "_last_assistant_text", "") or ""

    def _on_copy_reply(self):
        """U13：复制最近一条 AI 回复到剪贴板。

        不用 HTML inline <button onclick>：QTextBrowser 不执行 JavaScript，
        点内联按钮无效；这里用真正的 QToolButton 触发，复制成功仅做轻量反馈。
        """
        try:
            text = self._get_last_assistant_text()
            if text:
                QApplication.clipboard().setText(text)
                self._set_status("📋 已复制最近回复")
                return
            # 兜底：取 txHistory 纯文本最后一段
            plain = self.txHistory.toPlainText().strip()
            if plain:
                parts = [p for p in plain.split("\n") if p.strip()]
                text = parts[-1] if parts else plain
                QApplication.clipboard().setText(text)
                logger.debug("未取到 assistant 消息，复制 txHistory 末段作为兜底")
                self._set_status("📋 已复制（末段）")
            else:
                self._set_status("暂无回复可复制")
        except Exception as e:
            logger.debug("复制回复失败: %s", e, exc_info=True)

    def _on_example_clicked(self, text):
        """D5：示例指令填入输入框并聚焦，让用户可改后再发。"""
        try:
            self.ptMessage.setPlainText(text)
            self.ptMessage.setFocus()
        except Exception:
            pass

    def _message_input_text_height(self):
        """输入框内文本排版后的**像素高度**（已含折行）。

        为什么不用现成的几种「行数 / 高度」：
          · ``document().size().height()`` —— QPlainTextDocumentLayout 是惰性的，文档
            未参与绘制时 ``textWidth`` 恒为 -1，返回的是**块数**（1.0 / 2.0 …）而非像素高；
          · ``QFontMetrics.boundingRect(..., TextWordWrap)`` —— 实测 Qt6 下高度翻倍
            （单行算 2 行、两行算 4 行），Qt5 下却正确，跨版本不可靠；
          · ``QFontMetrics.lineSpacing()`` —— 只是标称行距，Qt6 的真实行高比它大，
            按它算会少给一行，出现假滚动条。
        这里用 ``QTextLayout`` 逐块精确排版、累加每行真实高度，Qt5 / Qt6 都对。
        """
        doc = self.ptMessage.document()
        fm = self.ptMessage.fontMetrics()
        fallback_line = float(fm.lineSpacing() or 16)
        blocks = max(1, doc.blockCount())
        if self.ptMessage.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap:
            return fallback_line * blocks
        if blocks > 200:
            # 粘贴超长文本时直接顶到上限，避免为每个块建一次 layout
            return 10 ** 6

        margins = self.ptMessage.contentsMargins()
        frame = self.ptMessage.frameWidth() * 2
        avail = self.ptMessage.width() - margins.left() - margins.right() - frame
        if avail <= 40:
            # 宽度尚未确定（控件还没显示/布局），退化为按块数估算
            return fallback_line * blocks

        font = self.ptMessage.font()
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        total = 0.0
        block = doc.begin()
        while block.isValid():
            text = block.text()
            if text:
                layout = QTextLayout(text, font)
                layout.setTextOption(option)
                layout.beginLayout()
                while True:
                    line = layout.createLine()
                    if not line.isValid():
                        break
                    line.setLineWidth(avail)
                    total += line.height()
                layout.endLayout()
            else:
                total += fallback_line
            block = block.next()
        return max(fallback_line, total)

    def _adjust_message_input_height(self, _size=None):
        """让输入框高度随内容自适应，限制在 44–140px 之间"""
        if getattr(self, "_adjusting_input", False):
            return
        self._adjusting_input = True
        try:
            text_height = self._message_input_text_height()
            margins = self.ptMessage.contentsMargins()
            chrome = (margins.top() + margins.bottom()
                      + self.ptMessage.frameWidth() * 2)
            # +6 余量：排版高度是浮点值，取整后少给一点就会被判成「放不下」并弹滚动条
            new_height = int(math.ceil(text_height)) + chrome + 6
            new_height = max(MESSAGE_INPUT_MIN_HEIGHT,
                             min(MESSAGE_INPUT_MAX_HEIGHT, new_height))
            if self.ptMessage.height() != new_height:
                self.ptMessage.setFixedHeight(new_height)
        except Exception as e:
            logger.debug("自适应输入框高度失败: %s", e, exc_info=True)
        finally:
            self._adjusting_input = False

    def showToolStatus(self, status_text):
        """在聊天框中显示工具调用状态"""
        # U18：工具/代码执行阶段提示
        self._set_status("执行工具…")
        try:
            self._set_chat_html(self._chat_html + self._tool_status_html(status_text))
        except Exception as e:
            logger.debug("渲染工具状态失败: %s", e, exc_info=True)

    def disableAllButtons(self):
        """U11：发送进行中只禁用「发送按钮」+「模型切换下拉」，停止按钮保持可用。

        不再遍历全部 QPushButton / QPlainTextEdit，从而避免锁死输入框
        （用户仍可预写下一条）与切到其它标签页。
        """
        self.pbSend.setDisabled(True)
        self.cbModelSelector.setDisabled(True)
        # pbStop（停止）保持可用，不在此禁用

    def enableAllButtons(self):
        """恢复上面禁用的控件，并收起停止按钮（可见性由 set_sending_state 控制）。"""
        self.pbSend.setDisabled(False)
        self.cbModelSelector.setDisabled(False)
        self.pbStop.setVisible(False)

    def disableAllTextEdit(self):
        """U11：不再禁用输入框，允许用户预写下一条消息。"""
        return

    def enableAllTextEdit(self):
        """U11：与上面配套，保持输入框始终可编辑。"""
        return

    def eventFilter(self, obj, event):
        etype = event.type()
        # 输入区聚焦时高亮容器边框（视觉上明确「在哪儿打字」）
        if obj is self.ptMessage and etype in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._set_composer_focused(etype == QEvent.Type.FocusIn)
        # 宽度变化会改变折行数，需要重新算输入框高度
        if obj is self.ptMessage and etype == QEvent.Type.Resize:
            self._adjust_message_input_height()
        if etype == QEvent.Type.KeyPress:
            # U13：Ctrl+F 唤起消息区搜索条
            if event.key() == Qt.Key.Key_F and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._show_search_bar()
                return True
            # U13：搜索条内 Esc 关闭
            if obj is self.searchBar and event.key() == Qt.Key.Key_Escape:
                self._hide_search_bar()
                return True
            if obj is self.ptMessage:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    # Shift/Ctrl + Enter 换行，单独的 Enter 发送
                    modifiers = event.modifiers()
                    if (modifiers & Qt.KeyboardModifier.ShiftModifier
                            or modifiers & Qt.KeyboardModifier.ControlModifier):
                        self.ptMessage.insertPlainText("\n")
                        return True
                    # 发送新消息前重置思考缓冲，避免与上一轮思考内容串在一起
                    self.resetThinking()
                    self.enterPressed.emit(self.ptMessage.toPlainText())
                    return True
            if obj is self.ptSearchConversationCard:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.searchPressed.emit(self.ptSearchConversationCard.toPlainText())
                    return True
        return super().eventFilter(obj, event)

    # ── 工作流录制 / 回放控件 ──

    def _init_workflow_controls(self):
        """构建工作流录制 / 回放控件，并接入 workflowLayout（tbWorkflow 标签页）。

        控件均在 __init__ 内创建，信号连接使用作用域枚举；不改动 processor 与其它文件。
        """
        # 录制 / 回放容器（插到工作流标题下方）
        wf_group = QGroupBox("工作流录制 / 回放")
        wf_layout = QVBoxLayout(wf_group)
        wf_layout.setContentsMargins(6, 6, 6, 6)
        wf_layout.setSpacing(4)

        # 第一行：录制切换按钮 + 录制状态标签
        rec_row = QHBoxLayout()
        rec_row.setSpacing(6)
        self.btnRecordToggle = QPushButton("● 开始录制")
        self.btnRecordToggle.setObjectName("btnRecordToggle")
        self.btnRecordToggle.setToolTip("开启后将本次对话操作录制为一个可回放的工作流")
        self.btnRecordToggle.clicked.connect(self._on_toggle_recording)

        self.lblRecStatus = QLabel("状态: 未录制")
        self.lblRecStatus.setObjectName("lblRecStatus")
        self.lblRecStatus.setStyleSheet("color: #888; font-size: 11px;")

        rec_row.addWidget(self.btnRecordToggle)
        rec_row.addWidget(self.lblRecStatus, 1)
        wf_layout.addLayout(rec_row)

        # 第二行：回放下拉 + 回放按钮 + 刷新按钮
        pb_row = QHBoxLayout()
        pb_row.setSpacing(6)
        self.cmbWorkflow = QComboBox()
        self.cmbWorkflow.setObjectName("cmbWorkflow")
        self.cmbWorkflow.setInsertPolicy(QComboBox.InsertPolicy.InsertAtBottom)
        self.cmbWorkflow.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.btnPlaybackWorkflow = QPushButton("▶ 回放工作流")
        self.btnPlaybackWorkflow.setObjectName("btnPlaybackWorkflow")
        self.btnPlaybackWorkflow.setToolTip("回放选中的工作流")
        self.btnPlaybackWorkflow.clicked.connect(self._on_playback_workflow)

        self.btnRefreshWorkflows = QPushButton("⟳")
        self.btnRefreshWorkflows.setObjectName("btnRefreshWorkflows")
        self.btnRefreshWorkflows.setToolTip("刷新工作流列表")
        self.btnRefreshWorkflows.setFixedWidth(32)
        self.btnRefreshWorkflows.clicked.connect(self._refresh_workflow_list)

        pb_row.addWidget(self.cmbWorkflow, 1)
        pb_row.addWidget(self.btnPlaybackWorkflow)
        pb_row.addWidget(self.btnRefreshWorkflows)
        wf_layout.addLayout(pb_row)

        # 插到工作流标题下方（lblWorkflowTitle 之后）
        title_index = self.workflowLayout.indexOf(self.lblWorkflowTitle)
        self.workflowLayout.insertWidget(title_index + 1, wf_group)

        # 切到工作流标签页时自动刷新列表（another connection to currentChanged，互不干扰）
        self.twTabs.currentChanged.connect(self._on_workflow_tab_shown)

        # 初始按当前（无对话）状态刷新一次列表
        self._refresh_workflow_list()

    def _reset_recording_ui(self):
        """复位录制按钮与状态标签到「未录制」。"""
        try:
            self._is_recording = False
            self.btnRecordToggle.setText("● 开始录制")
            self.btnRecordToggle.setStyleSheet("")
            self.lblRecStatus.setText("状态: 未录制")
            self.lblRecStatus.setStyleSheet("color: #888; font-size: 11px;")
        except Exception:
            pass

    def _get_processor(self):
        """从当前对话引用取出 processor；取不到返回 None。"""
        conv = getattr(self, "_live_conversation", None)
        if conv is not None and getattr(conv, "processor", None) is not None:
            return conv.processor
        return None

    def _on_workflow_tab_shown(self, index):
        """切到工作流标签页时刷新列表。"""
        try:
            if index == self.twTabs.indexOf(self.tbWorkflow):
                self._refresh_workflow_list()
        except Exception:
            pass

    def _refresh_workflow_list(self):
        """调用 processor.list_workflows() 填充下拉框；列表为空时禁用回放按钮。"""
        try:
            self.cmbWorkflow.clear()
            processor = self._get_processor()
            if processor is None:
                self.btnPlaybackWorkflow.setDisabled(True)
                return
            workflows = processor.list_workflows()
            if not workflows:
                self.btnPlaybackWorkflow.setDisabled(True)
                return
            self.cmbWorkflow.addItems(workflows)
            self.btnPlaybackWorkflow.setDisabled(False)
        except Exception as e:
            logger.debug("刷新工作流列表失败: %s", e, exc_info=True)
            self._append_chat_error(f"刷新工作流列表失败: {e}")

    def _on_toggle_recording(self):
        """开始 / 停止录制切换按钮的槽函数。"""
        processor = self._get_processor()
        if processor is None:
            self._append_chat_error("无法访问 processor：请先创建或打开一个对话。")
            return
        try:
            if not self._is_recording:
                processor.start_recording()
                self._is_recording = True
                self.btnRecordToggle.setText("■ 停止录制")
                self.btnRecordToggle.setStyleSheet(
                    "QPushButton { background-color: #FA7070; color: white; font-weight: bold; }"
                )
                self.lblRecStatus.setText("状态: 录制中…")
                self.lblRecStatus.setStyleSheet("color: #C0392B; font-size: 11px; font-weight: bold;")
                self._set_status("⏺ 录制中…")
            else:
                processor.stop_recording()
                self._reset_recording_ui()
                self._set_status("⏹ 录制已停止")
                # 新录制的工作流会出现在列表中，刷新一次
                self._refresh_workflow_list()
        except Exception as e:
            logger.debug("切换录制状态失败: %s", e, exc_info=True)
            self._append_chat_error(f"录制操作失败: {e}")

    def _on_playback_workflow(self):
        """回放选中的工作流。"""
        name = self.cmbWorkflow.currentText()
        if not name:
            self._append_chat_error("请先在下拉框中选择一个要回放的工作流。")
            return
        processor = self._get_processor()
        if processor is None:
            self._append_chat_error("无法访问 processor：请先创建或打开一个对话。")
            return
        try:
            result = processor.run_workflow(name)
            self._set_status(f"▶ 正在回放工作流: {name}")
            if isinstance(result, dict) and result.get("error"):
                self._append_chat_error(f"回放工作流失败: {result.get('error')}")
            else:
                self._append_chat_info(f"已触发工作流回放: {name}")
        except Exception as e:
            logger.debug("回放工作流失败: %s", e, exc_info=True)
            self._append_chat_error(f"回放工作流失败: {e}")

    def _append_chat_error(self, text):
        """在聊天框以红字追加错误提示（不静默吞异常）。"""
        try:
            safe = html_module.escape(str(text))
            self._set_chat_html(self._chat_html + self._notice_html(safe, "error"))
        except Exception:
            logger.debug("追加错误提示失败", exc_info=True)

    def _append_chat_info(self, text):
        """在聊天框以蓝字追加信息提示。"""
        try:
            safe = html_module.escape(str(text))
            self._set_chat_html(self._chat_html + self._notice_html(safe, "info"))
        except Exception:
            logger.debug("追加信息提示失败", exc_info=True)

    def _notice_html(self, safe_text, kind):
        """聊天区里的系统提示条（错误 / 信息）。

        走 _set_chat_html 而不是 txHistory.append()：append 只往文档里塞一段，
        下一次 _set_chat_html 整体重写时这段就没了；统一走同一个缓冲才不会丢。
        """
        c = getattr(self, "_chat_colors", None) or chat_colors()
        if kind == "error":
            color, mark = "#C0392B", "错误"
        else:
            color, mark = c["tool_edge"], "提示"
        return (
            '<table width="%(w)s" cellpadding="0" cellspacing="0"'
            ' style="margin-top: 4px;"><tr>'
            '<td style="border-left: 3px solid %(color)s; padding: 3px 9px;">'
            '<span style="color: %(color)s; font-size: 12px;">%(mark)s：%(txt)s</span>'
            '</td></tr></table>'
        ) % {"w": AI_BUBBLE_WIDTH, "color": color, "mark": mark, "txt": safe_text}

    # ── 工作流可视化方法 ──

    def update_workflow_display(self, workflow_data):
        """
        更新工作流标签页的显示内容（参考SpatialAnalysisAgent的方式）

        Args:
            workflow_data: 工作流数据字典
        """
        try:
            # U18：工作流阶段提示（携带步数则报「第 N 步」，否则「规划中」）
            steps = workflow_data.get("steps", []) if isinstance(workflow_data, dict) else []
            if steps:
                self._set_status(f"📋 第 {len(steps)} 步")
            else:
                self._set_status("📋 规划中…")
            # 生成HTML文件并保存到磁盘
            html_path = self._generate_workflow_html_file(workflow_data)

            # 加载HTML文件到QWebView
            if hasattr(self.workflowWebView, 'load'):
                # QWebView - 使用load方法
                from qgis.PyQt.QtCore import QUrl
                self.workflowWebView.load(QUrl.fromLocalFile(html_path))
            else:
                # QTextBrowser - 使用setHtml方法
                with open(html_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                self.workflowWebView.setHtml(html_content)

            # 更新摘要
            if "summary" in workflow_data:
                self.lblWorkflowSummary.setText(workflow_data["summary"])

        except Exception as e:
            print(f"Error updating workflow display: {e}")
            import traceback
            traceback.print_exc()

    def _generate_workflow_html_file(self, workflow_data):
        """
        生成工作流HTML文件并保存到磁盘

        Returns:
            str: HTML文件路径
        """
        import tempfile
        import os

        name = workflow_data.get("name", "未命名工作流")
        steps = workflow_data.get("steps", [])

        # 状态颜色映射
        status_colors = {
            "pending": "#cccccc",
            "running": "#ffcc00",
            "completed": "#66cc66",
            "failed": "#cc6666"
        }

        status_icons = {
            "pending": "⏳",
            "running": "⚙️",
            "completed": "✅",
            "failed": "❌"
        }

        # 生成HTML内容
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{
    font-family: "Microsoft YaHei", Arial, sans-serif;
    padding: 10px;
    font-size: 12px;
    margin: 0;
    background-color: #f5f5f5;
}}
.workflow-container {{
    background: white;
    padding: 15px;
    border-radius: 10px;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}}
.workflow-title {{
    font-size: 16px;
    font-weight: bold;
    color: #2c3e50;
    margin-bottom: 10px;
}}
.workflow-status {{
    display: inline-block;
    padding: 5px 15px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: bold;
    color: white;
    margin-bottom: 15px;
}}
.steps-container {{
    display: flex;
    flex-direction: column;
    align-items: center;
}}
.step-node {{
    display: flex;
    align-items: center;
    padding: 12px 20px;
    border-radius: 8px;
    font-weight: bold;
    color: white;
    min-width: 250px;
    margin: 5px 0;
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.2);
}}
.step-icon {{
    font-size: 20px;
    margin-right: 15px;
}}
.step-info {{
    flex: 1;
}}
.step-name {{
    font-size: 14px;
    margin-bottom: 3px;
}}
.step-tool {{
    font-size: 11px;
    opacity: 0.9;
}}
.arrow {{
    font-size: 24px;
    color: #3498db;
    margin: 8px 0;
}}
</style>
</head>
<body>
<div class="workflow-container">
    <div class="workflow-title">🔄 {name}</div>
    <div class="workflow-status" style="background-color: {status_colors.get(workflow_data.get('status', 'pending'), '#cccccc')}">
        {status_icons.get(workflow_data.get('status', 'pending'), '❓')} {workflow_data.get('status', 'pending').upper()}
    </div>
    <div class="steps-container">
        <div class="step-node" style="background: linear-gradient(135deg, #4CAF50, #45a049); border-radius: 50px;">
            <span class="step-icon">▶</span>
            <span class="step-info">开始</span>
        </div>
"""

        for i, step in enumerate(steps):
            step_status = step.get("status", "pending")
            step_icon = status_icons.get(step_status, "❓")
            step_name = step.get('name', f'步骤 {i + 1}')
            tool_name = step.get('tool', 'unknown')
            step_color = status_colors.get(step_status, "#cccccc")

            html += f"""
        <div class="arrow">↓</div>
        <div class="step-node" style="background: linear-gradient(135deg, {step_color}, {step_color}dd);">
            <span class="step-icon">{step_icon}</span>
            <span class="step-info">
                <div class="step-name">{step_name}</div>
                <div class="step-tool">🔧 {tool_name}</div>
            </span>
        </div>
"""

        html += """
        <div class="arrow">↓</div>
        <div class="step-node" style="background: linear-gradient(135deg, #9C27B0, #7B1FA2); border-radius: 50px;">
            <span class="step-icon">⏹</span>
            <span class="step-info">结束</span>
        </div>
    </div>
</div>
</body>
</html>
"""

        # 保存HTML文件到临时目录
        temp_dir = tempfile.gettempdir()
        html_path = os.path.join(temp_dir, "qgis_agent_workflow.html")

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)

        return html_path

    def _generate_workflow_html_pyvis(self, workflow_data):
        """使用pyvis生成交互式工作流graph"""
        try:
            from pyvis.network import Network
            import networkx as nx
            import tempfile
            import os

            steps = workflow_data.get("steps", [])

            # 创建NetworkX图
            G = nx.DiGraph()

            # 添加起始节点
            G.add_node("Start", label="开始", node_type="start")

            # 添加步骤节点和边
            prev_node = "Start"
            for i, step in enumerate(steps):
                step_id = step.get("id", f"step_{i + 1}")
                step_name = step.get("name", f"步骤 {i + 1}")
                step_status = step.get("status", "pending")
                tool_name = step.get("tool", "unknown")

                # 节点标签
                label = f"{step_name}\n({tool_name})"

                # 添加节点
                G.add_node(step_id, label=label, node_type="operation", status=step_status)

                # 添加边
                G.add_edge(prev_node, step_id)

                prev_node = step_id

            # 添加结束节点
            G.add_node("End", label="结束", node_type="end")
            G.add_edge(prev_node, "End")

            # 创建pyvis网络
            net = Network(notebook=False, height="500px", width="100%", directed=True)
            net.from_nx(G)

            # 设置节点颜色和形状
            status_colors = {
                "pending": "#cccccc",
                "running": "#ffcc00",
                "completed": "#66cc66",
                "failed": "#cc6666"
            }

            node_colors = []
            for node in net.nodes:
                node_type = node.get("node_type", "operation")
                status = node.get("status", "pending")

                if node_type == "start":
                    node_colors.append("#4CAF50")  # 绿色
                    node["shape"] = "diamond"
                elif node_type == "end":
                    node_colors.append("#9C27B0")  # 紫色
                    node["shape"] = "diamond"
                else:
                    node_colors.append(status_colors.get(status, "#cccccc"))
                    node["shape"] = "box"

            # 应用颜色
            for i, color in enumerate(node_colors):
                net.nodes[i]["color"] = color
                net.nodes[i]["font"] = {"size": 14}

            # 保存为HTML文件
            temp_dir = tempfile.gettempdir()
            html_path = os.path.join(temp_dir, "workflow_graph.html")
            net.save_graph(html_path)

            # 读取HTML内容
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()

            return html_content

        except ImportError as e:
            # pyvis或networkx未安装，使用简化版本
            print(f"pyvis/networkx not available: {e}")
            return self._generate_workflow_html_simple(workflow_data)
        except Exception as e:
            print(f"Error generating pyvis graph: {e}")
            import traceback
            traceback.print_exc()
            # 回退到简单版本
            return self._generate_workflow_html_simple(workflow_data)

    def _generate_workflow_html(self, workflow_data):
        """生成工作流HTML（纯CSS实现，不依赖JavaScript）"""
        name = workflow_data.get("name", "未命名工作流")
        status = workflow_data.get("status", "pending")
        steps = workflow_data.get("steps", [])

        # 状态颜色映射
        status_colors = {
            "pending": "#cccccc",
            "running": "#ffcc00",
            "completed": "#66cc66",
            "failed": "#cc6666"
        }

        status_icons = {
            "pending": "⏳",
            "running": "⚙️",
            "completed": "✅",
            "failed": "❌"
        }

        html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{
    font-family: "Microsoft YaHei", Arial, sans-serif;
    padding: 10px;
    font-size: 12px;
    margin: 0;
}}
.workflow-container {{
    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
    padding: 15px;
    border-radius: 10px;
    margin: 10px 0;
}}
.workflow-title {{
    font-size: 16px;
    font-weight: bold;
    color: #2c3e50;
    margin-bottom: 10px;
}}
.workflow-status {{
    display: inline-block;
    padding: 5px 15px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: bold;
    color: white;
    margin-bottom: 15px;
}}
.graph-container {{
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 10px;
}}
.node {{
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 12px 20px;
    border-radius: 8px;
    font-weight: bold;
    color: white;
    min-width: 200px;
    text-align: center;
    box-shadow: 0 3px 8px rgba(0, 0, 0, 0.2);
}}
.node-start {{
    background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%);
    border-radius: 50%;
    width: 80px;
    height: 80px;
    display: flex;
    align-items: center;
    justify-content: center;
}}
.node-end {{
    background: linear-gradient(135deg, #9C27B0 0%, #7B1FA2 100%);
    border-radius: 50%;
    width: 80px;
    height: 80px;
    display: flex;
    align-items: center;
    justify-content: center;
}}
.node-operation {{
    background: linear-gradient(135deg, #2196F3 0%, #1976D2 100%);
    border-radius: 10px;
}}
.node-pending {{
    background: linear-gradient(135deg, #9E9E9E 0%, #757575 100%);
}}
.node-running {{
    background: linear-gradient(135deg, #FF9800 0%, #F57C00 100%);
    animation: pulse 1.5s infinite;
}}
.node-completed {{
    background: linear-gradient(135deg, #4CAF50 0%, #388E3C 100%);
}}
.node-failed {{
    background: linear-gradient(135deg, #f44336 0%, #D32F2F 100%);
}}
@keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.7; }}
}}
.arrow {{
    font-size: 24px;
    color: #3498db;
    margin: 8px 0;
    font-weight: bold;
}}
.node-label {{
    font-size: 12px;
    margin-top: 5px;
}}
</style>
</head>
<body>
<div class="workflow-container">
    <div class="workflow-title">🔄 {name}</div>
    <div class="workflow-status" style="background-color: {status_colors.get(status, '#cccccc')}">
        {status_icons.get(status, '❓')} {status.upper()}
    </div>
    <div class="graph-container">
        <div class="node node-start">▶ 开始</div>
"""

        for i, step in enumerate(steps):
            step_status = step.get("status", "pending")
            step_icon = status_icons.get(step_status, "❓")
            step_name = step.get('name', f'步骤 {i + 1}')
            tool_name = step.get('tool', 'unknown')

            html += f"""
        <div class="arrow">↓</div>
        <div class="node node-operation node-{step_status}">
            {step_icon} {step_name}
            <div class="node-label">🔧 {tool_name}</div>
        </div>
"""

        html += """
        <div class="arrow">↓</div>
        <div class="node node-end">⏹ 结束</div>
    </div>
</div>
</body>
</html>
"""
        return html

    def clear_workflow_display(self):
        """清空工作流显示"""
        self.workflowWebView.setHtml("""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {
    font-family: "Microsoft YaHei", Arial, sans-serif;
    padding: 20px;
    text-align: center;
    color: #666;
}
.icon { font-size: 48px; margin-bottom: 15px; }
.title { font-size: 16px; font-weight: bold; color: #333; margin-bottom: 10px; }
.desc { font-size: 12px; }
</style>
</head>
<body>
<div class="icon">🔄</div>
<div class="title">等待任务执行...</div>
<div class="desc">执行任务后，工作流将在此可视化展示。</div>
</body>
</html>
""")
        self.lblWorkflowSummary.setText("")

    # ── 报告页签方法 ──

    def update_code_editor(self, code):
        """更新代码编辑器"""
        self.codeEditor.setPlainText(code)

    def update_execution_log(self, log_text):
        """更新执行日志"""
        self.executionLog.setPlainText(log_text)

    def append_execution_log(self, log_text):
        """追加执行日志"""
        self.executionLog.appendPlainText(log_text)
        # 自动滚动到底部
        self.executionLog.verticalScrollBar().setValue(
            self.executionLog.verticalScrollBar().maximum()
        )
        # U18：错误日志（以 ❌ 开头）触发「出错」状态并停止计时
        if isinstance(log_text, str) and log_text.lstrip().startswith("❌"):
            if self._timer.isValid():
                self._set_status(f"⚠ 出错（耗时 {self._format_elapsed()}）")
            else:
                self._set_status("⚠ 出错")

    def show_debug_analysis(self, analysis):
        """显示错误分析"""
        self.lblDebugAnalysis.setVisible(True)
        self.debugAnalysisText.setVisible(True)

        html = f"""
<div style="font-family: Arial; font-size: 12px;">
    <p><strong>错误类型:</strong> {analysis.get('error_category', 'unknown')}</p>
    <p><strong>置信度:</strong> {analysis.get('confidence', 0) * 100:.1f}%</p>
    <p><strong>建议:</strong></p>
    <ul>
"""
        for suggestion in analysis.get('suggestions', []):
            html += f"        <li>{suggestion}</li>\n"

        html += """    </ul>
</div>
"""
        self.debugAnalysisText.setHtml(html)

    def hide_debug_analysis(self):
        """隐藏错误分析"""
        self.lblDebugAnalysis.setVisible(False)
        self.debugAnalysisText.setVisible(False)

    def copy_code_to_clipboard(self):
        """复制代码到剪贴板"""
        from qgis.PyQt.QtWidgets import QApplication
        code = self.codeEditor.toPlainText()
        if code:
            QApplication.clipboard().setText(code)
            return True
        return False

    def save_code_to_file(self):
        """保存代码到文件"""
        from qgis.PyQt.QtWidgets import QFileDialog
        code = self.codeEditor.toPlainText()
        if not code:
            return None

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存代码", "", "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)
            return file_path
        return None

    def load_code_from_file(self):
        """从文件加载代码"""
        from qgis.PyQt.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开代码文件", "", "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            with open(file_path, 'r', encoding='utf-8') as f:
                code = f.read()
            self.codeEditor.setPlainText(code)
            return code
        return None

    def clear_code_editor(self):
        """清空代码编辑器"""
        self.codeEditor.clear()
        self.executionLog.clear()
        self.hide_debug_analysis()
