import os
import html as html_module
from datetime import datetime

from qgis.PyQt import QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QSizePolicy, QSpacerItem, QScrollArea, QWidget, QPlainTextEdit, QToolButton
)
from qgis.PyQt.QtGui import QFont, QPalette, QIcon

from .utils import handle_none_conversation, pack, unpack, format_description, create_markdown, set_font_color
from .qgis_agent_dockwidget_base_ui import Ui_QGISAgentDockWidget


class QGISAgentDockWidget(QtWidgets.QDockWidget, Ui_QGISAgentDockWidget):
    closingPlugin = pyqtSignal()
    enterPressed = pyqtSignal(str)
    searchPressed = pyqtSignal(str)
    switchClearMode = pyqtSignal(str)
    modelClicked = pyqtSignal(int)
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
        current_html = ""
        interaction_history = conversation.fetch()
        font_color = set_font_color(self.txHistory.palette().color(QPalette.Base))

        for interaction in interaction_history:
            msg_dict = pack(interaction, "interaction")
            if msg_dict["typeMessage"] == "input":
                safe_text = html_module.escape(msg_dict["requestText"])
                new_msg = f"""
                    <div style="margin:0;padding:0;line-height:1;text-align:right;color:#6baad1;">
                        用户 {msg_dict["requestTime"]}
                    </div>
                    <div style="margin:0;padding:0;line-height:1;text-align:right;color:{font_color};">
                        {safe_text}
                    </div>
                """
                current_html += new_msg

            if msg_dict["typeMessage"] == "return":
                new_msg = f"""
                    <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;text-align:left;">
                        QGIS Agent {msg_dict["responseTime"]}
                    </div>
                    <div style="margin:0;padding:0;line-height:1;color:{font_color};text-align:left;">
                        {create_markdown(msg_dict["responseText"])}
                    </div>
                    <div><br></div>
                """
                current_html += new_msg

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def showThinking(self, partial_text, response_time=""):
        """实时显示模型思考/生成中的内容"""
        font_color = set_font_color(self.txHistory.palette().color(QPalette.Base))
        # 构建当前完整 HTML（保留历史 + 追加流式内容）
        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, 'toHtml') else ""

        # 用标记来定位流式内容块
        marker = '<!-- STREAMING_MARKER -->'
        thinking_html = f"""
            <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;text-align:left;">
                QGIS Agent 思考中... {response_time}
            </div>
            <div style="margin:0;padding:0;line-height:1;color:{font_color};text-align:left;">
                {create_markdown(partial_text)}
            </div>
            <span style="color:#FD8A8A;">▌</span>
            <div><br></div>
            {marker}
        """

        # 如果已有流式标记，替换掉旧内容；否则追加
        if marker in current_html:
            # 找到标记位置，替换标记之前到上一个消息之后的所有流式内容
            marker_pos = current_html.find(marker)
            # 找到流式块开始位置（倒数第二个 <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;">）
            search_start = current_html.rfind(
                '<div style="margin:0;padding:0;line-height:1;color:#FD8A8A;">',
                0, marker_pos
            )
            if search_start >= 0:
                current_html = current_html[:search_start] + thinking_html
            else:
                current_html = current_html[:marker_pos] + thinking_html
        else:
            # 先移除可能残留的旧标记
            if marker in current_html:
                current_html = current_html.replace(marker, "")
            current_html += thinking_html

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def finalizeThinking(self):
        """清除流式标记，准备显示最终结果"""
        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, 'toHtml') else ""
        marker = '<!-- STREAMING_MARKER -->'
        current_html = current_html.replace(marker, "")
        self.txHistory.setHtml(current_html)

    def showToolStatus(self, status_text):
        """在聊天框中显示工具调用状态"""
        status_html = f"""
            <div style="margin:4px 0;padding:4px 8px;background-color:#2d2d2d;border-left:3px solid #4A90D9;border-radius:2px;color:#b0b0b0;font-size:12px;font-family:Consolas,monospace;">
                {html_module.escape(status_text)}
            </div>
        """
        self.txHistory.append(status_html)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def disableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            if btn is self.pbStop:
                continue  # 停止按钮始终可用
            btn.setDisabled(True)

    def enableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            if btn is self.pbStop:
                btn.setVisible(False)  # 隐藏停止按钮
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
