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
from datetime import datetime

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt, QElapsedTimer
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QSizePolicy, QSpacerItem, QWidget, QPlainTextEdit,
    QLineEdit, QToolButton, QStackedWidget, QGridLayout, QApplication,
    QComboBox
)
from qgis.PyQt.QtGui import QFont, QPalette, QTextDocument

from .utils import handle_none_conversation, pack, unpack, format_description, create_markdown, set_font_color
from .qgis_agent_dockwidget_base_ui import Ui_QGISAgentDockWidget
from .thinking_display import ThinkingManager

logger = logging.getLogger(__name__)

# 思考块定位标记：注释便于阅读，锚点用于定位（QTextBrowser 会丢弃注释，但会保留锚点）
THINKING_MARKER = '<!-- THINKING_BLOCK -->'
THINKING_ANCHOR = '<a name="THINKING_BLOCK"></a>'

# 输入框自适应高度范围（px）
MESSAGE_INPUT_MIN_HEIGHT = 40
MESSAGE_INPUT_MAX_HEIGHT = 140


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

        self.messagesLayout.setStretch(3, 1)

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
        self._thinking_base_html = ""
        self._last_thinking_text = ""

        # 输入框高度随内容自适应（40–140px）
        # 注意：Qt6 已移除 QTextDocument.sizeChanged 信号，改用 QTextEdit.textChanged（Qt5/Qt6 通用）
        self.ptMessage.textChanged.connect(self._adjust_message_input_height)
        self._adjust_message_input_height()

        # 思考块内的「复制」入口（锚点 #copy-thinking）
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
        """
        # ---- U13 搜索条（默认隐藏，Ctrl+F 唤起）----
        self.searchBar = QLineEdit()
        self.searchBar.setPlaceholderText("搜索对话内容… (Enter 下一处 / Shift+Enter 上一处 / Esc 关闭)")
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
        self.searchFrameWidget = QWidget()
        self.searchFrameWidget.setLayout(search_layout)
        self.searchFrameWidget.setVisible(False)

        # ---- U13 复制回复按钮（面板顶部标题行右侧）----
        self.btnCopyReply = QToolButton()
        self.btnCopyReply.setText("📋 复制回复")
        self.btnCopyReply.setToolTip("复制最近一条 AI 回复到剪贴板")
        self.btnCopyReply.clicked.connect(self._on_copy_reply)
        self.titleLayout.addWidget(self.btnCopyReply, 0, Qt.AlignmentFlag.AlignRight)

        # ---- U18 底部状态条 ----
        self.statusLabel = QLabel("就绪")
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px; padding: 2px 0;")
        self.statusLabel.setWordWrap(False)
        self._status_base = "就绪"

        # ---- D5 空状态示例卡片 ----
        self.emptyStateWidget = QWidget()
        egrid = QGridLayout(self.emptyStateWidget)
        egrid.setContentsMargins(8, 8, 8, 8)
        egrid.setSpacing(6)
        examples = [
            "加载一个矢量文件",
            "列出当前所有图层",
            "把图层重投影到 WGS84",
            "统计各行政区面积",
            "生成分级设色地图",
            "缓冲区分析 100 米",
            "按属性筛选要素",
            "导出当前图层为 GeoPackage",
        ]
        cols = 2
        for i, text in enumerate(examples):
            btn = QPushButton(text)
            btn.setStyleSheet("QPushButton { text-align: left; padding: 8px 10px; }")
            btn.clicked.connect(lambda _checked=False, t=text: self._on_example_clicked(t))
            egrid.addWidget(btn, i // cols, i % cols)

        # ---- 聊天区堆叠：历史 / 空状态 二选一显示 ----
        self.chatStack = QStackedWidget()
        self.chatStack.addWidget(self.txHistory)          # 页 0：历史消息区
        self.chatStack.addWidget(self.emptyStateWidget)   # 页 1：空状态示例

        # 把 txHistory 在原布局位置替换为 chatStack，再把搜索条插到其上方
        tx_index = self.messagesLayout.indexOf(self.txHistory)
        self.messagesLayout.replaceWidget(self.txHistory, self.chatStack)
        self.messagesLayout.insertWidget(tx_index, self.searchFrameWidget)
        # 状态条置于面板最底部
        self.messagesLayout.addWidget(self.statusLabel)
        # 仅聊天区拉伸填充，搜索条/状态条保持自然高度（不抢空间）
        chat_index = self.messagesLayout.indexOf(self.chatStack)
        self.messagesLayout.setStretch(tx_index, 0)    # 搜索条
        self.messagesLayout.setStretch(chat_index, 1)  # 聊天区

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

        # 初始按当前（空）内容决定显示哪一页
        self._update_empty_state()

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
        self.lbTitle.setText(conversation.title)
        self.lbDescription.setText(format_description(conversation.description))
        self.lbMetadata.setText(conversation.get_metadata())

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
        font_color = set_font_color(self.txHistory.palette().color(QPalette.ColorRole.Base))

        for interaction in interaction_history:
            msg_dict = pack(interaction, "interaction")
            if msg_dict["typeMessage"] == "input":
                safe_text = html_module.escape(msg_dict["requestText"])
                new_msg = f'''
                    <div style="margin: 8px 0; padding: 8px 12px; text-align: right;">
                        <div style="margin: 0 0 4px 0; font-size: 11px; color: #6baad1;">
                            👤 用户 · {msg_dict["requestTime"]}
                        </div>
                        <div style="margin: 0; color: {font_color}; line-height: 1.5;">
                            {safe_text}
                        </div>
                    </div>
                '''
                current_html += new_msg

            if msg_dict["typeMessage"] == "return":
                new_msg = f'''
                    <div style="margin: 8px 0; padding: 8px 12px;">
                        <div style="margin: 0 0 4px 0; font-size: 11px; color: #FD8A8A;">
                            🤖 QGIS Agent · {msg_dict["responseTime"]}
                        </div>
                        <div style="margin: 0; color: {font_color}; line-height: 1.5;">
                            {create_markdown(msg_dict["responseText"])}
                        </div>
                    </div>
                '''
                current_html += new_msg
                # U13：记录最近一条 assistant 回复的原始文本（供复制按钮使用）
                self._last_assistant_text = msg_dict["responseText"]

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

        # U18：最终回复已到达，停止计时并报告耗时
        if self._timer.isValid():
            self._set_status(f"✅ 完成（耗时 {self._format_elapsed()}）")

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

            # 保留最后一次完整思考内容，供块内「复制」入口使用
            self._last_thinking_text = self._thinking_buffer

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
        self._thinking_base_html = ""
        self._thinking_manager.clear()

    def _start_thinking(self, response_time=""):
        """开启新一轮思考：清空缓冲，并记录思考块之前的聊天内容"""
        self._thinking_buffer = ""
        self._thinking_active = True
        # 只保留当前这一轮思考，避免历史思考块被重复渲染
        self._thinking_manager.clear()
        self._thinking_manager.start(response_time)
        self._thinking_base_html = self.txHistory.toHtml() if hasattr(self.txHistory, "toHtml") else ""

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
        wrapped = f"{THINKING_MARKER}{THINKING_ANCHOR}{block_html}{THINKING_ANCHOR}{THINKING_MARKER}"
        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, "toHtml") else ""

        start, end = self._locate_thinking_block(current_html)
        if start is not None:
            # 原地替换：思考块之后追加的内容（如工具状态）保持不动
            current_html = current_html[:start] + wrapped + current_html[end:]
        else:
            # 兜底：标记被富文本引擎丢弃时，插到「思考开始前的内容」末尾
            base_html = self._thinking_base_html
            if "</body>" in base_html:
                current_html = base_html.replace("</body>", wrapped + "</body>", 1)
            else:
                current_html = base_html + wrapped

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def _on_history_anchor_clicked(self, url):
        """处理历史区链接点击：#copy-thinking 复制思考全文，其余链接保持默认行为"""
        try:
            if url is not None and url.fragment() == "copy-thinking":
                from qgis.PyQt.QtWidgets import QApplication
                text = self._last_thinking_text or self._thinking_buffer
                if text:
                    QApplication.clipboard().setText(text)
        except Exception as e:
            logger.debug("复制思考内容失败: %s", e, exc_info=True)

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

    def _adjust_message_input_height(self, _size=None):
        """让输入框高度随内容自适应，限制在 40–140px 之间"""
        try:
            doc_height = int(self.ptMessage.document().size().height())
            margins = self.ptMessage.contentsMargins()
            frame_height = self.ptMessage.frameWidth() * 2
            new_height = doc_height + margins.top() + margins.bottom() + frame_height + 4
            new_height = max(MESSAGE_INPUT_MIN_HEIGHT, min(MESSAGE_INPUT_MAX_HEIGHT, new_height))
            if self.ptMessage.height() != new_height:
                self.ptMessage.setFixedHeight(new_height)
        except Exception as e:
            logger.debug("自适应输入框高度失败: %s", e, exc_info=True)

    def showToolStatus(self, status_text):
        """在聊天框中显示工具调用状态"""
        # U18：工具/代码执行阶段提示
        self._set_status("⚙ 执行工具…")
        status_html = f'''
            <div style="margin: 4px 0; padding: 4px 10px; border-left: 3px solid #4A90D9; border-radius: 4px; font-family: Consolas, monospace; font-size: 12px;">
                <span style="color: #4A90D9;">🔧 {html_module.escape(status_text)}</span>
            </div>
        '''
        self.txHistory.append(status_html)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

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
        if event.type() == QEvent.Type.KeyPress:
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
            self.txHistory.append(
                f'<div style="margin: 4px 0; padding: 4px 10px; '
                f'border-left: 3px solid #C0392B; color: #C0392B; font-size: 12px;">'
                f'⚠ {safe}</div>'
            )
            self.txHistory.verticalScrollBar().setValue(
                self.txHistory.verticalScrollBar().maximum()
            )
        except Exception:
            pass

    def _append_chat_info(self, text):
        """在聊天框以蓝字追加信息提示。"""
        try:
            safe = html_module.escape(str(text))
            self.txHistory.append(
                f'<div style="margin: 4px 0; padding: 4px 10px; '
                f'border-left: 3px solid #2980B9; color: #2980B9; font-size: 12px;">'
                f'ℹ {safe}</div>'
            )
            self.txHistory.verticalScrollBar().setValue(
                self.txHistory.verticalScrollBar().maximum()
            )
        except Exception:
            pass

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
