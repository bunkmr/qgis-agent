from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_NewConversationDialog(object):
    def setupUi(self, Dialog):
        Dialog.setObjectName("Dialog")
        Dialog.setWindowTitle("新建对话 - QGIS Agent")
        Dialog.setFixedSize(400, 420)

        self.verticalLayout = QtWidgets.QVBoxLayout(Dialog)
        self.verticalLayout.setSpacing(8)

        # 名称
        self.lblName = QtWidgets.QLabel("对话名称")
        self.ptName = QtWidgets.QLineEdit()
        self.ptName.setPlaceholderText("输入对话名称")

        # 描述
        self.lblDescription = QtWidgets.QLabel("对话描述")
        self.ptDescription = QtWidgets.QPlainTextEdit()
        self.ptDescription.setPlaceholderText("输入对话描述（可选）")
        self.ptDescription.setFixedHeight(60)

        # LLM选择
        self.lblLLM = QtWidgets.QLabel("选择模型")
        self.cbLLM = QtWidgets.QComboBox()

        # API端点
        self.lblEndpoint = QtWidgets.QLabel("API 端点")
        self.leAPIEndpoint = QtWidgets.QLineEdit()
        self.leAPIEndpoint.setReadOnly(True)

        # API Key
        self.lblAPIKey = QtWidgets.QLabel("API Key")
        self.leAPIKey = QtWidgets.QLineEdit()
        self.leAPIKey.setEchoMode(QtWidgets.QLineEdit.Password)
        self.leAPIKey.setPlaceholderText("输入API Key")

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

        self.verticalLayout.addWidget(self.lblName)
        self.verticalLayout.addWidget(self.ptName)
        self.verticalLayout.addWidget(self.lblDescription)
        self.verticalLayout.addWidget(self.ptDescription)
        self.verticalLayout.addWidget(self.lblLLM)
        self.verticalLayout.addWidget(self.cbLLM)
        self.verticalLayout.addWidget(self.lblEndpoint)
        self.verticalLayout.addWidget(self.leAPIEndpoint)
        self.verticalLayout.addWidget(self.lblAPIKey)
        self.verticalLayout.addWidget(self.leAPIKey)
        self.verticalLayout.addLayout(self.buttonLayout)

        QtCore.QMetaObject.connectSlotsByName(Dialog)
