import os
from typing import Tuple

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt
from qgis.PyQt.QtWidgets import QLineEdit

from .utils import nested_dict_to_list
from .dialog_new_conversation_ui import Ui_NewConversationDialog


class NewConversationDialog(QtWidgets.QDialog, Ui_NewConversationDialog):
    def __init__(self, llm_full_dict, config_full_list, title=None, description=None, llm_id=None, parent=None):
        super().__init__(parent)
        self.config_full_list = config_full_list
        self.setupUi(self)

        self.llm_full_list = nested_dict_to_list(llm_full_dict)
        for llm_item in self.llm_full_list:
            if llm_item == "default::default":
                continue
            self.cbLLM.addItem(llm_item)

        if title:
            self.ptName.setText(title)
        if description:
            self.ptDescription.setPlainText(description)
        if llm_id:
            index = self.cbLLM.findText(llm_id)
            if index >= 0:
                self.cbLLM.setCurrentIndex(index)
                self.cbLLM.setEnabled(False)

        self._update_api_info(self.cbLLM.currentIndex())

        self.pbOkay.clicked.connect(self._handle_okay)
        self.pbCancel.clicked.connect(self.close)
        self.cbLLM.currentIndexChanged.connect(self._on_index_changed)

    def _update_api_info(self, index):
        current_llm_id = self.cbLLM.itemText(index)
        for info in self.config_full_list:
            if current_llm_id == info[0]:
                endpoint, api_key = info[-2], info[-1]
                self.leAPIEndpoint.setText(endpoint)
                self.leAPIKey.setText(api_key)
                break

    def get_metadata(self) -> Tuple[str, str, str, str, str]:
        name = self.ptName.text()
        description = self.ptDescription.toPlainText()
        llm_id = self.cbLLM.currentText()
        endpoint = self.leAPIEndpoint.text()
        api_key = self.leAPIKey.text()
        return name, description, llm_id, endpoint, api_key

    def _handle_okay(self):
        self.accept()

    def _on_index_changed(self, index):
        self._update_api_info(index)
