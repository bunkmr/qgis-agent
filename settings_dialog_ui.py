from PyQt5 import QtCore, QtWidgets


class Ui_SettingsDialog(object):
    def setupUi(self, Dialog):
        Dialog.setObjectName("Dialog")
        Dialog.setWindowTitle("大模型配置 - QGIS Agent")
        Dialog.resize(800, 500)

        self.verticalLayout = QtWidgets.QVBoxLayout(Dialog)
        self.verticalLayout.setSpacing(8)

        self.lblHint = QtWidgets.QLabel("在此管理所有大模型配置。可编辑模型名称、API地址和Key，修改后点击保存。")
        self.lblHint.setWordWrap(True)
        self.lblHint.setStyleSheet("color: #666;")

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["模型名称", "API 端点", "API Key", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table.setColumnWidth(3, 60)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        self.btnAdd = QtWidgets.QPushButton("+ 添加模型")
        self.btnAdd.setStyleSheet("QPushButton { background-color: #5CB85C; color: white; border-radius: 4px; padding: 6px 16px; } QPushButton:hover { background-color: #4CAE4C; }")

        self.buttonLayout = QtWidgets.QHBoxLayout()
        self.buttonLayout.addWidget(self.btnAdd)
        self.buttonLayout.addStretch()
        self.pbSave = QtWidgets.QPushButton("保存")
        self.pbSave.setStyleSheet("QPushButton { background-color: #4A90D9; color: white; border-radius: 4px; padding: 6px 24px; } QPushButton:hover { background-color: #357ABD; }")
        self.pbCancel = QtWidgets.QPushButton("取消")
        self.buttonLayout.addWidget(self.pbSave)
        self.buttonLayout.addWidget(self.pbCancel)

        self.verticalLayout.addWidget(self.lblHint)
        self.verticalLayout.addWidget(self.table)
        self.verticalLayout.addLayout(self.buttonLayout)

        QtCore.QMetaObject.connectSlotsByName(Dialog)
