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

        # 标题行（只有标题，移除了配置按钮）
        self.titleLayout = QtWidgets.QHBoxLayout()
        self.lbTitle = QtWidgets.QLabel("新建对话")
        self.lbTitle.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.lbTitle.setWordWrap(True)
        self.titleLayout.addWidget(self.lbTitle, 1)

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

        # 底部栏：模型选择 + Temperature + 停止按钮
        self.bottomBarLayout = QtWidgets.QHBoxLayout()
        self.bottomBarLayout.setContentsMargins(0, 2, 0, 0)
        self.bottomBarLayout.setSpacing(6)

        self.lblModel = QtWidgets.QLabel("模型:")
        self.lblModel.setStyleSheet("font-size: 12px; color: #888;")
        self.cbModelSelector = QtWidgets.QComboBox()
        self.cbModelSelector.setMinimumWidth(120)
        self.cbModelSelector.setStyleSheet("QComboBox { font-size: 12px; padding: 2px 4px; }")

        self.lblTemperature = QtWidgets.QLabel("温度:")
        self.lblTemperature.setStyleSheet("font-size: 12px; color: #888;")
        self.sliderTemperature = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sliderTemperature.setRange(0, 100)
        self.sliderTemperature.setValue(0)
        self.sliderTemperature.setFixedWidth(80)
        self.sliderTemperature.setToolTip("LLM 温度 (0=精确, 1=创造)")
        self.lblTempValue = QtWidgets.QLabel("0.0")
        self.lblTempValue.setStyleSheet("font-size: 11px; color: #888; min-width: 24px;")
        self.sliderTemperature.valueChanged.connect(
            lambda v: self.lblTempValue.setText(f"{v/100:.1f}")
        )

        self.pbStop = QtWidgets.QPushButton("⏹ 停止")
        self.pbStop.setFixedSize(70, 26)
        self.pbStop.setVisible(False)
        self.pbStop.setStyleSheet("""
            QPushButton { background-color: #FA7070; color: white; border-radius: 4px; font-size: 12px; }
            QPushButton:hover { background-color: #E05050; }
        """)

        # 跳过代码确认的开关
        self.cbSkipConfirm = QtWidgets.QCheckBox("跳过确认")
        self.cbSkipConfirm.setToolTip("勾选后直接执行所有 PyQGIS/Processing 代码，不再弹窗确认")
        self.cbSkipConfirm.setStyleSheet("QCheckBox { font-size: 11px; color: #888; }")

        self.bottomBarLayout.addWidget(self.lblModel)
        self.bottomBarLayout.addWidget(self.cbModelSelector, 1)
        self.bottomBarLayout.addWidget(self.lblTemperature)
        self.bottomBarLayout.addWidget(self.sliderTemperature)
        self.bottomBarLayout.addWidget(self.lblTempValue)
        self.bottomBarLayout.addWidget(self.cbSkipConfirm)
        self.bottomBarLayout.addWidget(self.pbStop)

        self.messagesLayout.addLayout(self.titleLayout)
        self.messagesLayout.addWidget(self.lbDescription)
        self.messagesLayout.addWidget(self.lbMetadata)
        self.messagesLayout.addWidget(self.txHistory)
        self.messagesLayout.addWidget(self.messageFrame)
        self.messagesLayout.addLayout(self.bottomBarLayout)

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

        # --- 大模型配置标签页 ---
        self.tbSettings = QtWidgets.QWidget()
        self.tbSettings.setObjectName("tbSettings")
        self.settingsLayout = QtWidgets.QVBoxLayout(self.tbSettings)
        self.settingsLayout.setContentsMargins(4, 4, 4, 4)
        self.settingsLayout.setSpacing(6)

        # 配置页标题
        self.lblSettingsTitle = QtWidgets.QLabel("大模型配置")
        self.lblSettingsTitle.setStyleSheet("font-size: 14px; font-weight: bold;")

        self.lblSettingsHint = QtWidgets.QLabel("管理 API 端点及密钥。添加模型时可参考内置信息，支持任意 OpenAI 兼容接口。")
        self.lblSettingsHint.setWordWrap(True)
        self.lblSettingsHint.setStyleSheet("color: #666; font-size: 11px;")

        # 模型配置表格
        self.settingsTable = QtWidgets.QTableWidget()
        self.settingsTable.setColumnCount(4)
        self.settingsTable.setHorizontalHeaderLabels(["模型名称", "API 端点", "API Key", ""])
        self.settingsTable.horizontalHeader().setStretchLastSection(False)
        self.settingsTable.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.settingsTable.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.settingsTable.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.settingsTable.setColumnWidth(3, 60)
        self.settingsTable.verticalHeader().setVisible(False)
        self.settingsTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        # 添加模型按钮
        self.btnAddModel = QtWidgets.QPushButton("+ 添加模型")
        self.btnAddModel.setStyleSheet("""
            QPushButton { background-color: #5CB85C; color: white; border-radius: 4px; padding: 6px 16px; }
            QPushButton:hover { background-color: #4CAE4C; }
        """)

        # 模型配置页的"跳过确认"开关（与底部栏的 cbSkipConfirm 保持同步）
        self.cbSkipConfirmSettings = QtWidgets.QCheckBox("跳过代码执行确认（直接执行 PyQGIS/Processing，不再弹窗）")
        self.cbSkipConfirmSettings.setStyleSheet("QCheckBox { font-size: 12px; color: #888; margin-top: 8px; }")

        self.settingsLayout.addWidget(self.lblSettingsTitle)
        self.settingsLayout.addWidget(self.lblSettingsHint)
        self.settingsLayout.addWidget(self.settingsTable)
        self.settingsLayout.addWidget(self.btnAddModel)
        self.settingsLayout.addWidget(self.cbSkipConfirmSettings)
        self.settingsLayout.addStretch()

        self.twTabs.addTab(self.tbMessages, "对话")
        self.twTabs.addTab(self.tbConversations, "对话列表")
        self.twTabs.addTab(self.tbSettings, "模型配置")

        self.mainLayout.addWidget(self.twTabs)

        QGISAgentDockWidget.setWidget(self.centralWidget)

        self.retranslateUi()
        QtCore.QMetaObject.connectSlotsByName(QGISAgentDockWidget)

    def retranslateUi(self):
        pass
