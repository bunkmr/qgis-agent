import logging
import os

from qgis.PyQt import QtCore, QtWidgets

logger = logging.getLogger(__name__)


class _DockTabWidget(QtWidgets.QTabWidget):
    """侧栏用的页签容器：sizeHint / minimumSizeHint 只按**当前页**算。

    默认实现（QTabWidget::sizeHint）取**所有页** sizeHint 的最大值，于是只要
    有一页里存在 sizeHint 虚高的控件，整个 dock 在尺寸自适应时就会被撑到屏幕
    之外 —— 哪怕用户当时根本没停在那页。

    实测现场（QGIS 3.44.14 / Qt5）：「工作流」页里的 QtWebKit ``QWebView``
    没有实现 sizeHint，Qt 返回默认的 800x600，把本控件的 sizeHint 顶到
    **812x805**。表现就是切过一次「工作流」页签之后，回到「对话」页时 dock
    已经把高度撑到屏幕以外 —— 底部输入框（发送/停止按钮）直接看不见。

    改成「只看当前页」，并把结果夹在 [MIN_HINT_H, MAX_HINT_H] 区间：下限保证
    初始高度不至于扁得没法用，上限保证**任何**页面都不可能把 dock 顶出屏幕。
    """

    MIN_HINT_W = 360
    MAX_HINT_W = 560
    MIN_HINT_H = 430
    MAX_HINT_H = 720

    def _clamp(self, size):
        w = max(self.MIN_HINT_W, min(self.MAX_HINT_W, size.width()))
        h = max(self.MIN_HINT_H, min(self.MAX_HINT_H, size.height()))
        return QtCore.QSize(w, h)

    def sizeHint(self):
        try:
            base = super().sizeHint()
            cur = self.currentWidget()
            if cur is None:
                return base
            bar = self.tabBar().sizeHint().height()
            return self._clamp(QtCore.QSize(
                base.width(), cur.sizeHint().height() + bar + 8))
        except Exception:  # noqa: BLE001 —— 尺寸提示无论如何不能抛异常
            return super().sizeHint()

    def minimumSizeHint(self):
        """同理按当前页算，避免「切到矮页签后高度回不来」。

        默认实现取所有页的最大值（报告页约 400），切到矮页签时 dock 能变小，
        切回对话页时主窗口却不把高度还回来 —— 底部同样会被裁掉。

        ⚠️ **宽度必须沿用父类**，不能一起夹：抬高最小宽度会让 dock 再也缩不回
        最窄（实测：宽度夹到 360 再加两侧边距，dock 最小宽度变成 372，在
        360px 的窄面板里就放不下了）。
        """
        try:
            base = super().minimumSizeHint()
            cur = self.currentWidget()
            if cur is None:
                return base
            bar = self.tabBar().sizeHint().height()
            h = max(self.MIN_HINT_H,
                    min(self.MAX_HINT_H, cur.minimumSizeHint().height() + bar + 8))
            return QtCore.QSize(base.width(), h)
        except Exception:  # noqa: BLE001
            return super().minimumSizeHint()


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

        # 标签页（用 _DockTabWidget：sizeHint 只按当前页算，防止某一页的
        # 虚高控件把整个 dock 撑出屏幕 —— 见类文档）
        self.twTabs = _DockTabWidget()
        self.twTabs.setObjectName("twTabs")

        # --- 对话标签页 ---
        self.tbMessages = QtWidgets.QWidget()
        self.tbMessages.setObjectName("tbMessages")
        self.messagesLayout = QtWidgets.QVBoxLayout(self.tbMessages)
        self.messagesLayout.setContentsMargins(0, 0, 0, 0)
        self.messagesLayout.setSpacing(4)

        # 标题行（只有标题，移除了配置按钮）
        self.titleLayout = QtWidgets.QHBoxLayout()
        self.titleLayout.setContentsMargins(0, 0, 0, 0)
        self.titleLayout.setSpacing(6)
        self.lbTitle = QtWidgets.QLabel("新建对话")
        self.lbTitle.setObjectName("lbTitle")
        self.lbTitle.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.lbTitle.setWordWrap(True)
        self.titleLayout.addWidget(self.lbTitle, 1)

        self.lbDescription = QtWidgets.QLabel("选择或新建对话开始使用 QGIS Agent")
        self.lbDescription.setObjectName("lbDescription")
        self.lbDescription.setWordWrap(True)
        self.lbDescription.setStyleSheet("color: #666; font-size: 12px;")

        self.lbMetadata = QtWidgets.QLabel("")
        self.lbMetadata.setObjectName("lbMetadata")
        self.lbMetadata.setStyleSheet("color: #888; font-size: 11px;")
        # 允许折行：元信息是「时间 + 模型 + 计数」的长串，不折行会把 dock
        # 的最小宽度顶到 390px 以上（把其他标签页压缩努力全部抵消）
        self.lbMetadata.setWordWrap(True)

        # 对话历史
        self.txHistory = QtWidgets.QTextBrowser()
        self.txHistory.setObjectName("txHistory")
        self.txHistory.setOpenExternalLinks(True)
        self.txHistory.setReadOnly(True)

        # 消息输入区（容器由 v2 加边框/聚焦态，这里只搭结构）
        self.messageFrame = QtWidgets.QFrame()
        self.messageFrame.setObjectName("messageFrame")
        self.messageLayout = QtWidgets.QHBoxLayout(self.messageFrame)
        self.messageLayout.setContentsMargins(8, 6, 6, 6)
        self.messageLayout.setSpacing(6)

        self.ptMessage = QtWidgets.QPlainTextEdit()
        self.ptMessage.setPlaceholderText("输入指令…  Enter 发送 / Shift+Enter 换行")
        self.ptMessage.setMinimumHeight(44)
        self.ptMessage.setObjectName("ptMessage")

        self.pbSend = QtWidgets.QPushButton("发送")
        self.pbSend.setObjectName("pbSend")
        self.pbSend.setFixedSize(64, 32)
        self.pbSend.setStyleSheet("""
            QPushButton { background-color: #4A90D9; color: white; border: none; border-radius: 5px; font-size: 13px; }
            QPushButton:hover { background-color: #357ABD; }
            QPushButton:disabled { background-color: #c8ccd2; color: #f0f0f0; }
        """)

        self.messageLayout.addWidget(self.ptMessage, 1)
        self.messageLayout.addWidget(self.pbSend, 0, QtCore.Qt.AlignmentFlag.AlignBottom)

        # 底部栏：模型选择 + Temperature（停止按钮与发送按钮同区，见下）
        self.bottomBarLayout = QtWidgets.QHBoxLayout()
        self.bottomBarLayout.setContentsMargins(0, 2, 0, 0)
        self.bottomBarLayout.setSpacing(6)

        self.lblModel = QtWidgets.QLabel("模型")
        self.lblModel.setStyleSheet("font-size: 12px; color: #888;")
        self.cbModelSelector = QtWidgets.QComboBox()
        self.cbModelSelector.setMinimumWidth(90)
        # 不被长模型名撑宽：按「最小内容长度」自适应，超长部分由下拉弹出层展示
        try:
            self.cbModelSelector.setSizeAdjustPolicy(
                QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            self.cbModelSelector.setMinimumContentsLength(10)
        except Exception:
            pass
        self.cbModelSelector.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
        )
        self.cbModelSelector.setStyleSheet("QComboBox { font-size: 12px; padding: 2px 4px; }")

        self.lblTemperature = QtWidgets.QLabel("温度")
        self.lblTemperature.setStyleSheet("font-size: 12px; color: #888;")
        self.sliderTemperature = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sliderTemperature.setRange(0, 100)
        self.sliderTemperature.setValue(0)
        self.sliderTemperature.setFixedWidth(60)
        self.sliderTemperature.setToolTip("LLM 温度 (0=精确, 1=创造)")
        self.lblTempValue = QtWidgets.QLabel("0.0")
        self.lblTempValue.setStyleSheet("font-size: 11px; color: #888; min-width: 22px;")
        self.sliderTemperature.valueChanged.connect(
            lambda v: self.lblTempValue.setText(f"{v / 100:.1f}")
        )

        self.pbStop = QtWidgets.QPushButton("停止")
        self.pbStop.setObjectName("pbStop")
        self.pbStop.setFixedSize(64, 32)
        self.pbStop.setVisible(False)
        self.pbStop.setStyleSheet("""
            QPushButton { background-color: #FA7070; color: white; border: none; border-radius: 5px; font-size: 13px; }
            QPushButton:hover { background-color: #E05050; }
            QPushButton:disabled { background-color: #d9a0a0; }
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

        # >>> 手工调整（非 Designer 生成；若重新生成 .ui 需一并保留）<<<
        # 「停止」与「发送」同占一格、互斥显示：
        #   原先停止按钮放在底部栏，与发送按钮分处两端，窄面板下两边都挤；
        #   移到输入框右侧同一位置后，底部栏只剩「模型 / 温度 / 跳过确认」，
        #   对话页最小宽度随之下降，且停止按钮出现在刚点过发送的地方，符合直觉。
        self.messageLayout.addWidget(self.pbStop, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        # <<< 手工调整结束 <<<

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
        self.settingsTable.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.settingsTable.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.settingsTable.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.settingsTable.setColumnWidth(3, 60)
        self.settingsTable.verticalHeader().setVisible(False)
        self.settingsTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        # 添加模型按钮
        self.btnAddModel = QtWidgets.QPushButton("+ 添加模型")
        self.btnAddModel.setStyleSheet("""
            QPushButton { background-color: #5CB85C; color: white; border-radius: 4px; padding: 6px 16px; }
            QPushButton:hover { background-color: #4CAE4C; }
        """)

        # 模型配置页的"跳过确认"开关（与底部栏的 cbSkipConfirm 保持同步）
        self.cbSkipConfirmSettings = QtWidgets.QCheckBox("跳过代码执行确认")
        self.cbSkipConfirmSettings.setToolTip("勾选后直接执行 PyQGIS/Processing 代码，不再弹窗确认")
        self.cbSkipConfirmSettings.setStyleSheet("QCheckBox { font-size: 12px; color: #888; margin-top: 8px; }")

        self.settingsLayout.addWidget(self.lblSettingsTitle)
        self.settingsLayout.addWidget(self.lblSettingsHint)
        self.settingsLayout.addWidget(self.settingsTable)
        self.settingsLayout.addWidget(self.btnAddModel)
        self.settingsLayout.addWidget(self.cbSkipConfirmSettings)
        self.settingsLayout.addStretch()

        # --- MCP 服务标签页 ---
        # 独立成页（而不是塞在「模型」页底部）：MCP 面向的是「把 QGIS 交给外部
        # Agent 驱动」，与「选哪个模型对话」是两件不相干的事。混在一起时它被压在
        # 模型表格 / 测试连接 / TLS 开关之后，窄面板里要滚很久才看得到，还容易被
        # 当成模型相关设置。控件本体由 qgis_agent._build_mcp_settings_ui 在运行时
        # 填入 mcpLayout（与「模型」页同一套做法）。
        self.tbMcp = QtWidgets.QWidget()
        self.tbMcp.setObjectName("tbMcp")
        self.mcpLayout = QtWidgets.QVBoxLayout(self.tbMcp)
        self.mcpLayout.setContentsMargins(4, 4, 4, 4)
        self.mcpLayout.setSpacing(6)
        # 末尾留一根弹簧，控件按自身高度贴顶，不被拉长填满整页
        self.mcpLayout.addStretch()

        # --- 工作流标签页 ---
        self.tbWorkflow = QtWidgets.QWidget()
        self.tbWorkflow.setObjectName("tbWorkflow")
        self.workflowLayout = QtWidgets.QVBoxLayout(self.tbWorkflow)
        self.workflowLayout.setContentsMargins(4, 4, 4, 4)
        self.workflowLayout.setSpacing(4)

        # 工作流标题
        self.lblWorkflowTitle = QtWidgets.QLabel("地理处理工作流")
        self.lblWorkflowTitle.setStyleSheet("font-size: 14px; font-weight: bold;")

        self.lblWorkflowHint = QtWidgets.QLabel("可视化展示任务执行流程和步骤状态")
        self.lblWorkflowHint.setWordWrap(True)
        self.lblWorkflowHint.setStyleSheet("color: #666; font-size: 11px;")

        # 工作流可视化区域（使用QWebView，参考SpatialAnalysisAgent）
        try:
            from qgis.PyQt.QtWebKitWidgets import QWebView
            self.workflowWebView = QWebView()
            # ⚠️ QWebView 没有实现 sizeHint()，Qt 回落到 QWidget 的默认值
            #    800x600，而 minimumSizeHint() 更是无效的 (-1,-1)。实测它把
            #    「工作流」页的 sizeHint 顶到 773、整个 QTabWidget 顶到
            #    812x805 —— dock 一旦做尺寸自适应就会被撑到屏幕之外，切回
            #    「对话」页时底部输入框正好落在屏幕外。（QGIS4/Qt6 没有
            #    QtWebKit，退化用 QTextBrowser，所以只有 Qt5 侧会中招。）
            #    Ignored = 「忽略 sizeHint，尽量占满可用空间」，正好匹配它
            #    本来就该填满工作流页的用法。
            self.workflowWebView.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Ignored)
            self.workflowWebView.setMinimumSize(1, 160)   # 顺便替掉 (-1,-1)
            self.workflowWebView.setHtml("<html><body><h3>等待任务执行...</h3><p>执行任务后，工作流将在此可视化展示。</p></body></html>")
        except ImportError:
            # 如果QWebView不可用，使用QTextBrowser
            self.workflowWebView = QtWidgets.QTextBrowser()
            self.workflowWebView.setOpenExternalLinks(True)
            self.workflowWebView.setHtml("<html><body><h3>等待任务执行...</h3><p>执行任务后，工作流将在此可视化展示。</p></body></html>")

        # 工作流摘要
        self.lblWorkflowSummary = QtWidgets.QLabel("")
        self.lblWorkflowSummary.setStyleSheet("color: #888; font-size: 11px;")
        self.lblWorkflowSummary.setWordWrap(True)

        self.workflowLayout.addWidget(self.lblWorkflowTitle)
        self.workflowLayout.addWidget(self.lblWorkflowHint)
        self.workflowLayout.addWidget(self.workflowWebView, 1)
        self.workflowLayout.addWidget(self.lblWorkflowSummary)

        # --- 帮助/关于标签页 ---
        self.tbAbout = QtWidgets.QWidget()
        self.tbAbout.setObjectName("tbAbout")
        self.aboutLayout = QtWidgets.QVBoxLayout(self.tbAbout)
        self.aboutLayout.setContentsMargins(4, 4, 4, 4)
        self.aboutLayout.setSpacing(4)

        # 帮助内容显示区域
        self.aboutWebView = QtWidgets.QTextBrowser()
        self.aboutWebView.setOpenExternalLinks(True)
        self.aboutWebView.setHtml(self._get_about_html())

        self.aboutLayout.addWidget(self.aboutWebView, 1)

        # >>> 手工追加（非 Designer 生成；若重新生成 .ui 需一并保留）<<<
        # HELP.html 是仓库内最完整的中文帮助，但历史上没有任何代码打开它（孤儿文件）。
        # 位置放在内容**下方**：这个按钮原来占在顶部，把帮助正文挤下去一行，
        # 而它只是「想要更完整文档时」的补充入口 —— 正文应当第一眼就看到。
        self.pbOpenFullHelp = QtWidgets.QPushButton("📖 打开完整帮助文档 (HELP.html)")
        self.pbOpenFullHelp.setObjectName("pbOpenFullHelp")
        self.pbOpenFullHelp.setToolTip("在默认浏览器中打开插件目录下的 HELP.html")
        self.pbOpenFullHelp.clicked.connect(self._open_full_help)
        self.aboutLayout.addWidget(self.pbOpenFullHelp)
        # <<< 手工追加结束 <<<

        # --- 报告标签页 ---
        self.tbReports = QtWidgets.QWidget()
        self.tbReports.setObjectName("tbReports")
        self.reportsLayout = QtWidgets.QVBoxLayout(self.tbReports)
        self.reportsLayout.setContentsMargins(4, 4, 4, 4)
        self.reportsLayout.setSpacing(4)

        # 报告标题
        self.lblReportsTitle = QtWidgets.QLabel("代码与执行报告")
        self.lblReportsTitle.setStyleSheet("font-size: 14px; font-weight: bold;")

        self.lblReportsHint = QtWidgets.QLabel("查看生成的代码和执行日志")
        self.lblReportsHint.setWordWrap(True)
        self.lblReportsHint.setStyleSheet("color: #666; font-size: 11px;")

        # 代码编辑器
        self.lblCode = QtWidgets.QLabel("生成的代码:")
        self.lblCode.setStyleSheet("font-size: 12px; font-weight: bold; margin-top: 8px;")

        self.codeEditor = QtWidgets.QPlainTextEdit()
        self.codeEditor.setReadOnly(True)
        self.codeEditor.setPlaceholderText("等待代码生成...")
        self.codeEditor.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")

        # 代码操作按钮（两行网格：窄面板下 5 个按钮排一行会把整个 dock 撑到 590px 宽）
        self.codeButtonLayout = QtWidgets.QGridLayout()
        self.codeButtonLayout.setContentsMargins(0, 0, 0, 0)
        self.codeButtonLayout.setSpacing(6)
        self.pbRunCode = QtWidgets.QPushButton("▶ 运行代码")
        self.pbRunCode.setStyleSheet("""
            QPushButton { background-color: #5CB85C; color: white; border-radius: 4px; padding: 6px 10px; font-weight: bold; }
            QPushButton:hover { background-color: #4CAE4C; }
        """)
        self.pbLoadCode = QtWidgets.QPushButton("📂 从文件读取")
        self.pbLoadCode.setStyleSheet("""
            QPushButton { background-color: #5BC0DE; color: white; border-radius: 4px; padding: 6px 10px; }
            QPushButton:hover { background-color: #46B8DA; }
        """)
        self.pbCopyCode = QtWidgets.QPushButton("📋 复制代码")
        self.pbCopyCode.setStyleSheet("""
            QPushButton { background-color: #6c757d; color: white; border-radius: 4px; padding: 6px 10px; }
            QPushButton:hover { background-color: #5a6268; }
        """)
        self.pbSaveCode = QtWidgets.QPushButton("💾 保存代码")
        self.pbSaveCode.setStyleSheet("""
            QPushButton { background-color: #17a2b8; color: white; border-radius: 4px; padding: 6px 10px; }
            QPushButton:hover { background-color: #138496; }
        """)
        self.pbClearCode = QtWidgets.QPushButton("🗑️ 清空")
        self.pbClearCode.setStyleSheet("""
            QPushButton { background-color: #dc3545; color: white; border-radius: 4px; padding: 6px 10px; }
            QPushButton:hover { background-color: #c82333; }
        """)
        self.codeButtonLayout.addWidget(self.pbRunCode, 0, 0)
        self.codeButtonLayout.addWidget(self.pbLoadCode, 0, 1)
        self.codeButtonLayout.addWidget(self.pbCopyCode, 1, 0)
        self.codeButtonLayout.addWidget(self.pbSaveCode, 1, 1)
        self.codeButtonLayout.addWidget(self.pbClearCode, 2, 0)
        self.codeButtonLayout.setColumnStretch(1, 1)

        # 执行日志
        self.lblExecutionLog = QtWidgets.QLabel("执行日志:")
        self.lblExecutionLog.setStyleSheet("font-size: 12px; font-weight: bold; margin-top: 8px;")

        self.executionLog = QtWidgets.QPlainTextEdit()
        self.executionLog.setReadOnly(True)
        self.executionLog.setPlaceholderText("等待执行日志...")
        self.executionLog.setMaximumHeight(150)
        self.executionLog.setStyleSheet("font-family: Consolas, monospace; font-size: 10px; color: #666;")

        # 错误分析（SmartDebugger）
        self.lblDebugAnalysis = QtWidgets.QLabel("错误分析:")
        self.lblDebugAnalysis.setStyleSheet("font-size: 12px; font-weight: bold; margin-top: 8px; color: #D9534F;")
        self.lblDebugAnalysis.setVisible(False)

        self.debugAnalysisText = QtWidgets.QTextBrowser()
        self.debugAnalysisText.setOpenExternalLinks(True)
        self.debugAnalysisText.setMaximumHeight(120)
        self.debugAnalysisText.setVisible(False)

        self.reportsLayout.addWidget(self.lblReportsTitle)
        self.reportsLayout.addWidget(self.lblReportsHint)
        self.reportsLayout.addWidget(self.lblCode)
        self.reportsLayout.addWidget(self.codeEditor, 1)
        self.reportsLayout.addLayout(self.codeButtonLayout)
        self.reportsLayout.addWidget(self.lblExecutionLog)
        self.reportsLayout.addWidget(self.executionLog)
        self.reportsLayout.addWidget(self.lblDebugAnalysis)
        self.reportsLayout.addWidget(self.debugAnalysisText)

        # 页签文案统一压到 2–3 个字：6 个页签在窄 dock 里一字之差就会被挤成半截，
        # 完整含义由 QGISAgentDockWidgetV2._configure_tab_bar 设置的 tooltip 说明。
        self.twTabs.addTab(self.tbMessages, "对话")
        self.twTabs.addTab(self.tbConversations, "历史")
        self.twTabs.addTab(self.tbSettings, "模型")
        self.twTabs.addTab(self.tbMcp, "MCP")
        self.twTabs.addTab(self.tbWorkflow, "工作流")
        self.twTabs.addTab(self.tbReports, "报告")
        self.twTabs.addTab(self.tbAbout, "帮助")

        # 只设结构性属性与最小字号兜底；配色 / 内边距 / 选中态全部由
        # QGISAgentDockWidgetV2._apply_chat_style() 按调色板统一下发
        # （两处都写样式必然漂移，这里刻意不再写第二份）。
        self.twTabs.setDocumentMode(True)

        self.mainLayout.addWidget(self.twTabs)

        QGISAgentDockWidget.setWidget(self.centralWidget)

        self.retranslateUi()
        QtCore.QMetaObject.connectSlotsByName(QGISAgentDockWidget)

    def _plugin_version(self):
        """运行时读取插件版本号（唯一真源：config.PLUGIN_VERSION ← metadata.txt）。

        本文件是 Designer 风格文件，不应硬编码版本号（硬编码必然随发版过期）。
        读取失败时返回占位文案，绝不回退到某个历史版本号。
        """
        for mod in (".config", "config", "qgis_agent.config"):
            try:
                module = __import__(mod, fromlist=["PLUGIN_VERSION"])
                version = getattr(module, "PLUGIN_VERSION", "")
                if version:
                    return version
            except Exception:
                continue
        return "以插件管理器显示为准"

    def _help_file_path(self):
        """返回随包分发的 HELP.html 绝对路径；不存在时返回 None。"""
        try:
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "HELP.html"
            )
            return path if os.path.isfile(path) else None
        except Exception:
            return None

    def _open_full_help(self):
        """用系统默认浏览器打开 HELP.html；文件缺失时优雅降级（不抛异常）。"""
        path = self._help_file_path()
        if not path:
            QtWidgets.QMessageBox.information(
                None,
                "QGIS Agent",
                "未找到完整帮助文档 HELP.html。\n"
                "请重新安装插件，或在线查看：\n"
                "https://github.com/bunkmr/qgis-agent",
            )
            return

        try:
            from qgis.PyQt.QtGui import QDesktopServices  # Qt5 / Qt6 均在 QtGui
        except ImportError:  # pragma: no cover - 兜底，正常路径不会走到
            try:
                from qgis.PyQt.QtCore import QDesktopServices
            except ImportError:
                QDesktopServices = None

        url = QtCore.QUrl.fromLocalFile(path)
        if QDesktopServices is None or not QDesktopServices.openUrl(url):
            QtWidgets.QMessageBox.information(
                None,
                "QGIS Agent",
                "无法自动打开帮助文档，请手动打开：\n%s" % path,
            )

    def _get_about_html(self):
        """帮助页 HTML —— 内容与样式都在 help_content 里（纯函数，可单测）。

        历史坑：本方法原来内联了约 380 行 HTML，且大量使用 Qt 富文本**不支持**的
        属性（display:flex / linear-gradient / border-radius / tr:nth-child），
        再叠加写死的 #333/#888 浅色值 —— 深色主题下又乱又看不清。现在：
          - 结构只用实测可用的那套（表格 + 单元格 inline style + 元素选择器）；
          - 颜色从 utils.chat_colors() 按调色板注入，两个主题都可读。
        """
        try:
            try:
                from .help_content import build_help_html
            except ImportError:
                from help_content import build_help_html
            try:
                from .utils import chat_colors
            except ImportError:
                from utils import chat_colors
        except Exception as _e:
            logger.debug("帮助页模块不可用: %s", _e, exc_info=True)
            return "<html><body><h1>QGIS Agent</h1><p>帮助内容加载失败。</p></body></html>"

        try:
            colors = chat_colors()
        except Exception:
            colors = None

        tools = []
        try:
            try:
                from .qgis_tools import TOOL_DEFINITIONS
            except ImportError:
                from qgis_tools import TOOL_DEFINITIONS
            tools = list(TOOL_DEFINITIONS)
        except Exception:
            logger.debug("帮助页读取工具清单失败", exc_info=True)

        return build_help_html(
            version=self._plugin_version(),
            colors=colors,
            tools=tools,
            tool_count=len(tools),
        )


    def retranslateUi(self):
        pass
