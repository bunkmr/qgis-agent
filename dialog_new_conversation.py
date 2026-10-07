from typing import Tuple

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import QCoreApplication

from .dialog_new_conversation_ui import Ui_NewConversationDialog

#: 界面文案翻译入口（写法与 base_ui 一致，pylupdate 只认这种字面量形式）。
_translate = QCoreApplication.translate


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
            self.lblModelInfo.setText(
                _translate("QGISAgent", "当前模型: %s  (%s)") % (name, endpoint)
                if endpoint else _translate("QGISAgent", "当前模型: %s") % name)
        else:
            self.lblModelInfo.setText(_translate("QGISAgent", "当前模型: 未选择（请在底部模型选择器中选取）"))

        self.pbOkay.clicked.connect(self._handle_okay)
        self.pbCancel.clicked.connect(self.close)

    def get_metadata(self) -> Tuple[str, str, str]:
        """返回 (title, description, api_key)"""
        name = self.ptName.text()
        description = self.ptDescription.toPlainText()
        return name, description, ""

    def _handle_okay(self):
        self.accept()
