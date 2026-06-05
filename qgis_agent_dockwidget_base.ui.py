from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_QGISAgentDockWidget(object):
    def setupUi(self, QGISAgentDockWidget):
        QGISAgentDockWidget.setObjectName("QGISAgentDockWidget")
        QGISAgentDockWidget.setWindowTitle("QGIS Agent")
        QGISAgentDockWidget.setMinimumSize(360, 500)

        self.centralWidget = QtWidgets.QWidget()
        self.centralWidget.setObjectName("centralWidget")

        self.mainLayout = QtWidgets.QVBoxLayout(self.centralWidget)
        self.mainLayout.setContentsMargins(6, 6, 6, 6)
        self.mainLayout.setSpacing(4)

        # 标签页
        self.twTabs = QtWidgets.QTabWidget()
        self.twTabs.setObjectName("twTabs")

        # --- 对话标签页 ---
        self.tbMessages = QtWidgets.QWidget()
        self.tbMessages.setObjectName("tbMessages")
        self.messagesLayout = QtWidgets.QVBoxLayout(self.tbMessages)
        self.messagesLayout.setContentsMargins(0, 0, 0, 0)
        self.messagesLayout.setSpacing(4)

        # 标题和描述
        self.lbTitle = QtWidgets.QLabel("新建对话")
        self.lbTitle.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.lbTitle.setWordWrap(True)

        self.lbDescription = QtWidgets.QLabel("选择或新建对话开始使用QGIS Agent")
        self.lbDescription.setWordWrap(True)
        self.lbDescription.setStyleSheet("color: #666;")

        self.lbMetadata = QtWidgets.QLabel("")
        self.lbMetadata.setStyleSheet("color: #888; font-size: 11px;")
        self.lbMetadata.setWordWrap(True)

        # 对话历史
        self.txHistory = QtWidgets.QTextBrowser()
        self.txHistory.setOpenExternalLinks(True)
        self.txHistory.setReadOnly(True)

        # 消息输入区
        self.messageFrame = QtWidgets.QFrame()
        self.messageFrame.setObjectName("messageFrame")
        self.messageLayout = QtWidgets.QHBoxLayout(self.messageFrame)
        self.messageLayout.setContentsMargins(0, 0, 0, 0)

        self.ptMessage = QtWidgets.QPlainTextEdit()
        self.ptMessage.setPlaceholderText("输入您的问题...")
        self.ptMessage.setFixedHeight(60)
        self.ptMessage.setObjectName("ptMessage")

        self.pbSend = QtWidgets.QPushButton("发送")
        self.pbSend.setFixedSize(60, 60)
        self.pbSend.setStyleSheet("""
            QPushButton { background-color: #4A90D9; color: white; border-radius: 4px; font-size: 14px; }
            QPushButton:hover { background-color: #357ABD; }
            QPushButton:disabled { background-color: #ccc; }
        """)

        self.messageLayout.addWidget(self.ptMessage)
        self.messageLayout.addWidget(self.pbSend)

        # 响应模式
        self.responseModeLayout = QtWidgets.QHBoxLayout()
        self.rbtCode = QtWidgets.QRadioButton("生成代码")
        self.rbtCode.setChecked(True)
        self.rbtVisualModel = QtWidgets.QRadioButton("图形模型")
        self.rbtToolbox = QtWidgets.QRadioButton("工具箱脚本")
        self.responseModeLayout.addWidget(self.rbtCode)
        self.responseModeLayout.addWidget(self.rbtVisualModel)
        self.responseModeLayout.addWidget(self.rbtToolbox)
        self.responseModeLayout.addStretch()

        self.messagesLayout.addWidget(self.lbTitle)
        self.messagesLayout.addWidget(self.lbDescription)
        self.messagesLayout.addWidget(self.lbMetadata)
        self.messagesLayout.addWidget(self.txHistory)
        self.messagesLayout.addLayout(self.responseModeLayout)
        self.messagesLayout.addWidget(self.messageFrame)

        # --- 对话列表标签页 ---
        self.tbConversations = QtWidgets.QWidget()
        self.tbConversations.setObjectName("tbConversations")
        self.conversationsLayout = QtWidgets.QVBoxLayout(self.tbConversations)
        self.conversationsLayout.setContentsMargins(4, 4, 4, 4)
        self.conversationsLayout.setSpacing(4)

        # 搜索区
        self.searchFrame = QtWidgets.QHBoxLayout()
        self.ptSearchConversationCard = QtWidgets.QPlainTextEdit()
        self.ptSearchConversationCard.setPlaceholderText("搜索对话...")
        self.ptSearchConversationCard.setFixedHeight(30)
        self.pbSearchConversationCard = QtWidgets.QPushButton("搜索")
        self.pbSearchConversationCard.setFixedWidth(60)
        self.searchFrame.addWidget(self.ptSearchConversationCard)
        self.searchFrame.addWidget(self.pbSearchConversationCard)

        # 新建按钮
        self.pbNew = QtWidgets.QPushButton("+ 新建对话")
        self.pbNew.setStyleSheet("""
            QPushButton { background-color: #5CB85C; color: white; border-radius: 4px; font-size: 13px; padding: 6px; }
            QPushButton:hover { background-color: #4CAE4C; }
        """)

        # 对话卡片滚动区
        self.saConversationCard = QtWidgets.QScrollArea()
        self.saConversationCard.setWidgetResizable(True)
        self.saConversationCard.setObjectName("saConversationCard")

        self.conversationsLayout.addLayout(self.searchFrame)
        self.conversationsLayout.addWidget(self.pbNew)
        self.conversationsLayout.addWidget(self.saConversationCard)

        self.twTabs.addTab(self.tbMessages, "对话")
        self.twTabs.addTab(self.tbConversations, "对话列表")

        self.mainLayout.addWidget(self.twTabs)

        QGISAgentDockWidget.setWidget(self.centralWidget)

        self.retranslateUi()
        QtCore.QMetaObject.connectSlotsByName(QGISAgentDockWidget)

    def retranslateUi(self):
        pass
