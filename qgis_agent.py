# -*- coding: utf-8 -*-

import os
import sys
import re

from qgis.PyQt.QtCore import (
    QSettings, QTranslator, QCoreApplication, Qt, QTimer, pyqtSignal
)
from qgis.PyQt.QtGui import QIcon, QTextCursor, QClipboard, QKeyEvent
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QPushButton, QWidget, QPlainTextEdit,
    QDockWidget, QApplication, QToolButton, QMenu
)
from qgis.utils import iface

from .package_manager import PackageManager

required_modules = [
    "langchain_deepseek",
    "langchain_openai",
    "langchain",
    "requests",
]
package_manager = PackageManager(required_modules)
package_manager.check_dependencies()

from .resources import *
from .qgis_agent_dockwidget import QGISAgentDockWidget
from .dataloader import DataLoader
from .conversation import Conversation
from .dialog_new_conversation import NewConversationDialog
from .utils import (
    generate_unique_id, get_current_timestamp, pack, extract_code,
    get_qgis_version
)
from .config import DB_NAME, PLUGIN_NAME


class QGISAgent:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale = QSettings().value("locale/userLocale")[0:2]
        locale_path = os.path.join(self.plugin_dir, "i18n", f"QGISAgent_{locale}.qm")
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr("&QGIS Agent")
        self.toolbar = self.iface.addToolBar("QGIS Agent")
        self.toolbar.setObjectName("QGISAgentToolbar")

        self.plugin_is_active = False
        self.dockwidget = None
        self.edit_dialog = None
        self.live_conversation_id = None
        self.live_conversation = None
        self.dataloader = DataLoader(DB_NAME)
        self.console_text = ""
        self.console_tracker = QTimer()
        self.new_editor = None

    def tr(self, message):
        return QCoreApplication.translate("QGISAgent", message)

    def add_action(self, icon_path, text, callback, enabled_flag=True,
                   add_to_menu=True, add_to_toolbar=True, status_tip=None,
                   whats_this=None, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        icon_path = ":/plugins/qgis_agent/icon.png"
        self.add_action(
            icon_path,
            text=self.tr("打开 QGIS Agent"),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )

    def onClosePlugin(self):
        self.dockwidget.closingPlugin.disconnect(self.onClosePlugin)
        self.plugin_is_active = False
        self.dataloader.close()

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&QGIS Agent"), action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def run(self):
        if not self.plugin_is_active:
            self.plugin_is_active = True

            if self.dockwidget is None:
                self.dockwidget = QGISAgentDockWidget()

            self.dockwidget.closingPlugin.connect(self.onClosePlugin)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dockwidget)
            self.dockwidget.show()

            self.dataloader.connect()

            self.dockwidget.pbSend.clicked.connect(self._on_new_message_send)
            self.dockwidget.enterPressed.connect(self._on_new_message_send)
            self.dockwidget.pbNew.clicked.connect(self._on_new_conversation)
            self.dockwidget.pbSearchConversationCard.clicked.connect(
                self._on_search_conversation
            )
            self.dockwidget.searchPressed.connect(self._on_search_conversation)
            self.dockwidget.switchClearMode.connect(self._switch_clear_mode)

            slot_funcs = [
                self._on_conversation_load,
                self._on_conversation_delete,
                self._on_conversation_edit,
            ]
            self.dockwidget.displayConversationCard(self.dataloader, slot_funcs)

    def _on_new_message_send(self):
        message = self.dockwidget.ptMessage.toPlainText()
        if not message:
            return

        if self.live_conversation is None:
            self._on_new_conversation()

        self.dockwidget.ptMessage.clear()

        if self.dockwidget.rbtVisualModel.isChecked():
            response_type = "Visual mode"
        elif self.dockwidget.rbtCode.isChecked():
            response_type = "Code"
        else:
            response_type = "Toolbox"

        if self.live_conversation is not None:
            self.live_conversation.llm_response.connect(self._on_response_received)
            self.live_conversation.llm_interrupted.connect(self._on_response_error)
            self.live_conversation.update_user_prompt(message, response_type)
            self.dockwidget.disableAllButtons()
            self.dockwidget.disableAllTextEdit()

    def _on_response_received(self, response, workflow, model_path):
        if self.live_conversation is not None:
            self.dockwidget.enableAllButtons()
            self.dockwidget.enableAllTextEdit()
            self.live_conversation.llm_response.disconnect(self._on_response_received)
            self.live_conversation.llm_interrupted.disconnect(self._on_response_error)

            if workflow == "withCode":
                code = extract_code(response)
                if code:
                    self._run_in_console(code)

            self.dockwidget.updateConversation(self.live_conversation)
            self.dockwidget.updateGeneralInfo(self.live_conversation)

            slot_funcs = [
                self._on_conversation_load,
                self._on_conversation_delete,
                self._on_conversation_edit,
            ]
            self.dockwidget.updateConversationCard(
                self.live_conversation.meta_info, slot_funcs
            )
            self.dataloader.update_conversation_info(self.live_conversation.meta_info)

    def _on_response_error(self, error_message):
        self.dockwidget.enableAllButtons()
        self.dockwidget.enableAllTextEdit()
        self.live_conversation.llm_response.disconnect(self._on_response_received)
        self.live_conversation.llm_interrupted.disconnect(self._on_response_error)
        self.dockwidget.txHistory.append(f"<p style='color:red'>错误: {error_message}</p>")

    def _on_new_conversation(self):
        if self.edit_dialog is None or not self.edit_dialog.isVisible():
            self.edit_dialog = NewConversationDialog(
                self.dataloader.llm_full_dict,
                self.dataloader.fetch_all_config(),
            )
            self.edit_dialog.show()
            if self.edit_dialog.exec_() == QDialog.Accepted:
                title, description, llm_id, endpoint, api_key = (
                    self.edit_dialog.get_metadata()
                )
                created = get_current_timestamp()
                modified = created
                self.live_conversation_id = generate_unique_id()

                meta_info = pack(
                    (
                        self.live_conversation_id, llm_id, title, description,
                        created, modified, 0, 0, "local"
                    ),
                    "conversation",
                )
                self.dataloader.create_conversation(meta_info)
                self.dataloader.update_api_key(api_key, llm_id)

                self.live_conversation = Conversation(
                    self.live_conversation_id, self.dataloader
                )

                self.dockwidget.twTabs.setCurrentWidget(self.dockwidget.tbMessages)
                self.dockwidget.updateConversation(self.live_conversation)
                self.dockwidget.updateGeneralInfo(self.live_conversation)

                slot_funcs = [
                    self._on_conversation_load,
                    self._on_conversation_delete,
                    self._on_conversation_edit,
                ]
                self.dockwidget.addConversationCard(meta_info, slot_funcs)

    def _on_conversation_load(self, conversation_id):
        self.live_conversation_id = conversation_id
        self.live_conversation = Conversation(conversation_id, self.dataloader)
        self.live_conversation.lastEdit = get_current_timestamp()
        self.dataloader.update_conversation_info(self.live_conversation.meta_info)

        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.updateConversationCard(
            self.live_conversation.meta_info, slot_funcs
        )
        self.dockwidget.twTabs.setCurrentWidget(self.dockwidget.tbMessages)
        self.dockwidget.updateConversation(self.live_conversation)
        self.dockwidget.updateGeneralInfo(self.live_conversation)

    def _on_conversation_delete(self, conversation_id: str):
        self.dataloader.delete_conversation(conversation_id)
        if self.live_conversation_id == conversation_id:
            self.live_conversation = None
            self.dockwidget.txHistory.clear()
            self.dockwidget.lbTitle.clear()
            self.dockwidget.lbDescription.clear()
            self.dockwidget.lbMetadata.clear()
            self.dockwidget.removeConversationCard(conversation_id)
        else:
            self.dockwidget.removeConversationCard(conversation_id)

    def _on_conversation_edit(self, conversation_id: str):
        if self.edit_dialog is None or not self.edit_dialog.isVisible():
            edit_conversation = Conversation(conversation_id, self.dataloader)
            self.edit_dialog = NewConversationDialog(
                self.dataloader.llm_full_dict,
                self.dataloader.fetch_all_config(),
                edit_conversation.title,
                edit_conversation.description,
                edit_conversation.llmID,
            )
            self.edit_dialog.show()

            if self.edit_dialog.exec_() == QDialog.Accepted:
                (edit_conversation.title,
                 edit_conversation.description,
                 llm_id, _, api_key) = self.edit_dialog.get_metadata()
                edit_conversation.lastEdit = get_current_timestamp()
                self.dataloader.update_conversation_info(edit_conversation.meta_info)
                self.dataloader.update_api_key(api_key, llm_id)

                if conversation_id == self.live_conversation_id:
                    self.live_conversation = edit_conversation
                    self.dockwidget.updateGeneralInfo(self.live_conversation)

                slot_funcs = [
                    self._on_conversation_load,
                    self._on_conversation_delete,
                    self._on_conversation_edit,
                ]
                self.dockwidget.updateConversationCard(
                    edit_conversation.meta_info, slot_funcs
                )

    def _on_search_conversation(self):
        search_text = self.dockwidget.ptSearchConversationCard.toPlainText()
        if not search_text:
            return

        def search_filter(meta_info, keyword=search_text):
            return (keyword.lower() in meta_info["title"].lower() or
                    keyword.lower() in meta_info["description"].lower())

        def highlight(full_text, keyword=search_text):
            pattern = re.compile(f"({re.escape(keyword)})", re.IGNORECASE)
            return pattern.sub(
                r'<span style="background-color: yellow">\1</span>', full_text
            )

        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.displayConversationCard(
            self.dataloader, slot_funcs, search_filter, highlight
        )

        self.dockwidget.pbSearchConversationCard.clicked.disconnect(
            self._on_search_conversation
        )
        self.dockwidget.searchPressed.disconnect(self._on_search_conversation)
        self.dockwidget.pbSearchConversationCard.setText("取消")
        self.dockwidget.pbSearchConversationCard.clicked.connect(
            self._switch_clear_mode
        )

    def _switch_clear_mode(self):
        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.displayConversationCard(self.dataloader, slot_funcs)
        self.dockwidget.pbSearchConversationCard.clicked.connect(
            self._on_search_conversation
        )
        self.dockwidget.searchPressed.connect(self._on_search_conversation)
        self.dockwidget.pbSearchConversationCard.setText("搜索")

    def _run_in_console(self, code: str):
        console_widget = iface.mainWindow().findChild(QDockWidget, "PythonConsole")
        if not console_widget or not console_widget.isVisible():
            iface.actionShowPythonDialog().trigger()
            console_widget = iface.mainWindow().findChild(QDockWidget, "PythonConsole")

        import console

        python_console = console_widget.findChild(
            console.console.PythonConsoleWidget
        )
        QApplication.clipboard().setText(code)
        python_console.pasteEditor()
