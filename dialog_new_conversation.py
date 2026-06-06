import os
from typing import Tuple

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt
from qgis.PyQt.QtWidgets import QLineEdit

from .dialog_new_conversation_ui import Ui_NewConversationDialog


class NewConversationDialog(QtWidgets.QDialog, Ui_NewConversationDialog):
    def __init__(self, dataloader, title=None, description=None, llm_id=None, parent=None):
        super().__init__(parent)
        self.dataloader = dataloader
        self.setupUi(self)

        if title:
            self.ptName.setText(title)
        if description:
            self.ptDescription.setPlainText(description)

        # 显示当前模型信息
        if llm_id:
            name, endpoint, _ = self.dataloader.fetch_llm_info(llm_id)
            self.lblModelInfo.setText(f"当前模型: {name}  ({endpoint})" if endpoint else f"当前模型: {name}")
        else:
            self.lblModelInfo.setText("当前模型: 未选择（请在底部模型选择器中选取）")

        self.pbOkay.clicked.connect(self._handle_okay)
        self.pbCancel.clicked.connect(self.close)

    def get_metadata(self) -> Tuple[str, str, str]:
        """返回 (title, description, api_key)"""
        name = self.ptName.text()
        description = self.ptDescription.toPlainText()
        return name, description, ""

    def _handle_okay(self):
        self.accept()
