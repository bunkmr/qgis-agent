from PyQt5 import QtCore, QtWidgets


class Ui_NewConversationDialog(object):
    def setupUi(self, Dialog):
        Dialog.setObjectName("Dialog")
        Dialog.setWindowTitle("新建对话 - QGIS Agent")
        Dialog.setFixedSize(400, 260)

        self.verticalLayout = QtWidgets.QVBoxLayout(Dialog)
        self.verticalLayout.setSpacing(8)

        # 模型提示（只读，从底部选择器继承）
        self.lblModelInfo = QtWidgets.QLabel("当前模型: (从底部选择器选择)")
        self.lblModelInfo.setStyleSheet("color: #888; font-size: 12px;")

        # 名称
        self.lblName = QtWidgets.QLabel("对话名称")
        self.ptName = QtWidgets.QLineEdit()
        self.ptName.setPlaceholderText("输入对话名称")

        # 描述
        self.lblDescription = QtWidgets.QLabel("对话描述")
        self.ptDescription = QtWidgets.QPlainTextEdit()
        self.ptDescription.setPlaceholderText("输入对话描述（可选）")
        self.ptDescription.setFixedHeight(60)

        # 按钮
        self.buttonLayout = QtWidgets.QHBoxLayout()
        self.buttonLayout.addStretch()
        self.pbOkay = QtWidgets.QPushButton("确定")
        self.pbOkay.setStyleSheet("""
            QPushButton { background-color: #4A90D9; color: white; border-radius: 4px; padding: 6px 16px; }
            QPushButton:hover { background-color: #357ABD; }
        """)
        self.pbCancel = QtWidgets.QPushButton("取消")
        self.buttonLayout.addWidget(self.pbOkay)
        self.buttonLayout.addWidget(self.pbCancel)

        self.verticalLayout.addWidget(self.lblModelInfo)
        self.verticalLayout.addWidget(self.lblName)
        self.verticalLayout.addWidget(self.ptName)
        self.verticalLayout.addWidget(self.lblDescription)
        self.verticalLayout.addWidget(self.ptDescription)
        self.verticalLayout.addStretch()
        self.verticalLayout.addLayout(self.buttonLayout)

        QtCore.QMetaObject.connectSlotsByName(Dialog)
