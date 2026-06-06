import uuid

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt

from .settings_dialog_ui import Ui_SettingsDialog


class SettingsDialog(QtWidgets.QDialog, Ui_SettingsDialog):
    def __init__(self, dataloader, parent=None):
        super().__init__(parent)
        self.dataloader = dataloader
        self._row_data = {}  # row_idx -> {"llm_id": str, "name": str}
        self.setupUi(self)

        self._load_config()

        self.btnAdd.clicked.connect(self._add_row)
        self.pbSave.clicked.connect(self._save)
        self.pbCancel.clicked.connect(self.close)

    def _load_config(self):
        rows = self.dataloader.fetch_all_config()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            llm_id, name, endpoint, api_key = row
            self._set_row(i, name, endpoint, api_key, llm_id)

    def _set_row(self, row_idx, name, endpoint, api_key, llm_id=None):
        if llm_id is None:
            llm_id = f"Custom::{uuid.uuid4().hex[:8]}"

        self._row_data[row_idx] = {"llm_id": llm_id, "name": name}

        # 第0列：可编辑的模型名称（显示名，如 "gpt-4o"）
        name_item = QtWidgets.QTableWidgetItem(name)
        self.table.setItem(row_idx, 0, name_item)

        # 第1列：API 端点（可编辑）
        endpoint_item = QtWidgets.QTableWidgetItem(endpoint)
        self.table.setItem(row_idx, 1, endpoint_item)

        # 第2列：API Key（可编辑）
        key_item = QtWidgets.QTableWidgetItem(api_key)
        self.table.setItem(row_idx, 2, key_item)

        # 第3列：删除按钮
        del_btn = QtWidgets.QPushButton("删除")
        del_btn.setStyleSheet(
            "QPushButton { background-color: #FA7070; color: white; border-radius: 3px; padding: 2px 8px; }"
            " QPushButton:hover { background-color: #E05050; }"
        )
        del_btn.clicked.connect(lambda _, r=row_idx: self._delete_row(r))
        self.table.setCellWidget(row_idx, 3, del_btn)

    def _add_row(self):
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self._set_row(row_idx, "新模型", "", "")

    def _delete_row(self, row_idx):
        if row_idx in self._row_data:
            llm_id = self._row_data[row_idx]["llm_id"]
            if llm_id:
                self.dataloader.delete_llm_config(llm_id)
        self.table.removeRow(row_idx)
        # 重建 row_data 映射
        new_data = {}
        for i in range(self.table.rowCount()):
            if i < row_idx and i in self._row_data:
                new_data[i] = self._row_data[i]
            elif i >= row_idx:
                old_idx = i + 1
                if old_idx in self._row_data:
                    new_data[i] = self._row_data[old_idx]
        self._row_data = new_data

    def _save(self):
        for i in range(self.table.rowCount()):
            name_item = self.table.item(i, 0)
            endpoint_item = self.table.item(i, 1)
            key_item = self.table.item(i, 2)

            if not name_item or not endpoint_item:
                continue

            name = name_item.text().strip()
            endpoint = endpoint_item.text().strip()
            api_key = key_item.text().strip() if key_item else ""

            if not name or not endpoint:
                continue

            # 使用已有的 llm_id，或生成新的
            if i in self._row_data:
                llm_id = self._row_data[i]["llm_id"]
            else:
                llm_id = f"Custom::{uuid.uuid4().hex[:8]}"

            self.dataloader.insert_llm_config(llm_id, name, endpoint, api_key)

        self.dataloader.reload_llm_config()
        self.accept()
