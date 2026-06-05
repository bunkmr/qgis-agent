import os
from datetime import datetime

from qgis.PyQt import QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QSizePolicy, QSpacerItem, QScrollArea, QWidget, QPlainTextEdit
)
from qgis.PyQt.QtGui import QFont, QPalette

from .utils import handle_none_conversation, pack, unpack, format_description, create_markdown, set_font_color
from .qgis_agent_dockwidget_base_ui import Ui_QGISAgentDockWidget


class QGISAgentDockWidget(QtWidgets.QDockWidget, Ui_QGISAgentDockWidget):
    closingPlugin = pyqtSignal()
    enterPressed = pyqtSignal(str)
    searchPressed = pyqtSignal(str)
    switchClearMode = pyqtSignal(str)
    modelClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.conversationCards = {}
        self.scrollAreaWidget = None
        self.scrollAreaLayout = None
        self.setupUi(self)

        self.ptMessage.installEventFilter(self)
        self.ptSearchConversationCard.installEventFilter(self)

        self.twTabs.setCurrentWidget(self.tbMessages)

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
                new_msg = f"""
                    <div style="margin:0;padding:0;line-height:1;text-align:right;color:#6baad1;">
                        用户 {msg_dict["requestTime"]}
                    </div>
                    <div style="margin:0;padding:0;line-height:1;text-align:right;color:{font_color};">
                        {msg_dict["requestText"]}
                    </div>
                """
                current_html += new_msg

            if msg_dict["typeMessage"] == "return":
                new_msg = f"""
                    <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;">
                        QGIS Agent {msg_dict["responseTime"]}
                    </div>
                    <div style="margin:0;padding:0;line-height:1;color:{font_color};">
                        {create_markdown(msg_dict["responseText"])}
                    </div>
                    <div><br></div>
                """
                current_html += new_msg

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def disableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            btn.setDisabled(True)

    def enableAllButtons(self):
        for btn in self.findChildren(QPushButton):
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
