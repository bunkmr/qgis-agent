# -*- coding: utf-8 -*-
"""
增强版 DockWidget - 集成可折叠思考显示

主要改进:
1. 思考内容可折叠/展开
2. 更美观的 UI 样式
3. 流式更新支持
"""

import os
import html as html_module
from datetime import datetime

from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QSizePolicy, QSpacerItem, QScrollArea, QWidget, QPlainTextEdit
)
from qgis.PyQt.QtGui import QFont, QPalette

from .utils import handle_none_conversation, pack, unpack, format_description, create_markdown, set_font_color
from .qgis_agent_dockwidget_base_ui import Ui_QGISAgentDockWidget
from .thinking_display import ThinkingManager


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
        self.ptMessage.setFixedHeight(40)

        self.pbStop.clicked.connect(self.stopRequested.emit)

        self.ptMessage.installEventFilter(self)
        self.ptSearchConversationCard.installEventFilter(self)

        self.twTabs.setCurrentWidget(self.tbMessages)

        # 思考内容管理器
        self._thinking_manager = ThinkingManager()

    def set_sending_state(self, is_sending):
        """切换发送/停止状态"""
        self.pbSend.setVisible(not is_sending)
        self.pbStop.setVisible(is_sending)
        self.cbModelSelector.setDisabled(is_sending)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def displayConversationCard(self, dataloader, slots_functions,
                                search_filter=lambda x: True,
                                highlight_rule=lambda x: x):
        self.scrollAreaLayout = QVBoxLayout()
        self.scrollAreaWidget = QWidget()
        self.saConversationCard.setWidget(self.scrollAreaWidget)
        self.scrollAreaWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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
        meta_label.setAlignment(Qt.AlignRight)

        btn_layout = QHBoxLayout()
        spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

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
        self.lbTitle.setText(conversation.title)
        self.lbDescription.setText(format_description(conversation.description))
        self.lbMetadata.setText(conversation.get_metadata())

    @handle_none_conversation
    def updateConversation(self, conversation):
        """更新对话历史显示"""
        self._thinking_manager.clear()
        current_html = ""
        interaction_history = conversation.fetch()
        font_color = set_font_color(self.txHistory.palette().color(QPalette.Base))

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

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def showThinking(self, partial_text, response_time=""):
        """
        实时显示思考内容

        使用简单的 div 结构，兼容 QTextBrowser，使用 QGIS 主题颜色。
        """
        # 获取 QGIS 主题颜色
        palette = self.txHistory.palette()
        bg_color = palette.color(QPalette.Base).name()
        font_color = set_font_color(palette.color(QPalette.Base))

        # 构建当前完整 HTML
        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, 'toHtml') else ""

        # 思考标记
        thinking_marker = '<!-- THINKING_BLOCK -->'

        # 准备内容（使用 create_markdown 渲染）
        rendered_content = create_markdown(partial_text) if partial_text else "&nbsp;"

        # 使用 QGIS 主题颜色构建思考块（不设置背景色，使用透明）
        new_thinking = f'''{thinking_marker}
<div style="margin: 8px 0; padding: 0;">
<div style="padding: 8px 12px; border-left: 4px solid #6baad1; border-radius: 4px;">
<span style="color: #6baad1; font-weight: bold;">🧠 思考中... {response_time}</span>
</div>
<div style="padding: 10px 12px; border-left: 4px solid #444; margin-top: 2px;">
<div style="color: {font_color}; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; font-family: Consolas, Monaco, monospace;">
{rendered_content}
<span style="color: #6baad1;">▌</span>
</div>
</div>
</div>
{thinking_marker}'''

        if thinking_marker in current_html:
            # 更新现有思考块
            thinking_start = current_html.find(thinking_marker)
            thinking_end = current_html.find(thinking_marker, thinking_start + len(thinking_marker))

            if thinking_end > thinking_start:
                current_html = current_html[:thinking_start] + new_thinking + current_html[thinking_end + len(thinking_marker):]
        else:
            # 添加新的思考块
            current_html += new_thinking

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def finalizeThinking(self):
        """
        完成思考，将思考块设为折叠样式
        """
        # 获取 QGIS 主题颜色
        palette = self.txHistory.palette()
        bg_color = palette.color(QPalette.Base).name()
        font_color = set_font_color(palette.color(QPalette.Base))

        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, 'toHtml') else ""
        thinking_marker = '<!-- THINKING_BLOCK -->'

        if thinking_marker in current_html:
            thinking_start = current_html.find(thinking_marker)
            thinking_end = current_html.find(thinking_marker, thinking_start + len(thinking_marker))

            if thinking_end > thinking_start:
                # 提取思考块内容
                thinking_block = current_html[thinking_start + len(thinking_marker):thinking_end]

                # 从内容 div 中提取文本
                import re
                content_match = re.search(r'<div style="color: [^"]*">(.*?)<span style="color: #6baad1;">▌</span>', thinking_block, re.DOTALL)
                if content_match:
                    content_html = content_match.group(1)
                    # 移除 HTML 标签，保留文本
                    content_text = re.sub(r'<[^>]+>', '', content_html).strip()
                    # 截断过长内容
                    if len(content_text) > 200:
                        content_text = content_text[:200] + "..."
                else:
                    content_text = "..."

                # 生成折叠状态的思考块（使用主题颜色，不设置背景色）
                safe_content = html_module.escape(content_text) if content_text else "&nbsp;"
                finalized_thinking = f'''{thinking_marker}
<div style="margin: 8px 0; padding: 0;">
<div style="padding: 6px 12px; border-left: 4px solid #666; border-radius: 4px;">
<span style="color: {font_color}; font-size: 11px;">💭 思考完成 ▸ {safe_content}</span>
</div>
</div>
{thinking_marker}'''
                current_html = current_html[:thinking_start] + finalized_thinking + current_html[thinking_end + len(thinking_marker):]

        self.txHistory.setHtml(current_html)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def showToolStatus(self, status_text):
        """在聊天框中显示工具调用状态"""
        # 获取 QGIS 主题颜色
        palette = self.txHistory.palette()
        font_color = set_font_color(palette.color(QPalette.Base))

        status_html = f'''
            <div style="margin: 4px 0; padding: 4px 10px; border-left: 3px solid #4A90D9; border-radius: 4px; font-family: Consolas, monospace; font-size: 12px;">
                <span style="color: #4A90D9;">🔧 {html_module.escape(status_text)}</span>
            </div>
        '''
        self.txHistory.append(status_html)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def disableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            if btn is self.pbStop:
                continue
            btn.setDisabled(True)

    def enableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            if btn is self.pbStop:
                btn.setVisible(False)
                continue
            btn.setDisabled(False)

    def disableAllTextEdit(self):
        for te in self.findChildren(QPlainTextEdit):
            te.setDisabled(True)

    def enableAllTextEdit(self):
        for te in self.findChildren(QPlainTextEdit):
            te.setDisabled(False)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if obj is self.ptMessage and event.key() == Qt.Key_Return:
                self.enterPressed.emit(self.ptMessage.toPlainText())
                return True
            if obj is self.ptSearchConversationCard and event.key() == Qt.Key_Return:
                self.searchPressed.emit(self.ptSearchConversationCard.toPlainText())
                return True
        return super().eventFilter(obj, event)
