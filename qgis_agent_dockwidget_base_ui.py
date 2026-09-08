import os

from qgis.PyQt import QtCore, QtWidgets


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
        self.sliderTemperature = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sliderTemperature.setRange(0, 100)
        self.sliderTemperature.setValue(0)
        self.sliderTemperature.setFixedWidth(80)
        self.sliderTemperature.setToolTip("LLM 温度 (0=精确, 1=创造)")
        self.lblTempValue = QtWidgets.QLabel("0.0")
        self.lblTempValue.setStyleSheet("font-size: 11px; color: #888; min-width: 24px;")
        self.sliderTemperature.valueChanged.connect(
            lambda v: self.lblTempValue.setText(f"{v / 100:.1f}")
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
        self.cbSkipConfirmSettings = QtWidgets.QCheckBox("跳过代码执行确认（直接执行 PyQGIS/Processing，不再弹窗）")
        self.cbSkipConfirmSettings.setStyleSheet("QCheckBox { font-size: 12px; color: #888; margin-top: 8px; }")

        self.settingsLayout.addWidget(self.lblSettingsTitle)
        self.settingsLayout.addWidget(self.lblSettingsHint)
        self.settingsLayout.addWidget(self.settingsTable)
        self.settingsLayout.addWidget(self.btnAddModel)
        self.settingsLayout.addWidget(self.cbSkipConfirmSettings)
        self.settingsLayout.addStretch()

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

        # >>> 手工追加（非 Designer 生成；若重新生成 .ui 需一并保留）<<<
        # HELP.html 是仓库内最完整的中文帮助，但历史上没有任何代码打开它（孤儿文件）。
        # 这里只往既有 aboutLayout 追加一个按钮，不改动其它布局结构；
        # 点击后用系统默认浏览器打开随包分发的 HELP.html。
        self.pbOpenFullHelp = QtWidgets.QPushButton("📖 打开完整帮助文档 (HELP.html)")
        self.pbOpenFullHelp.setObjectName("pbOpenFullHelp")
        self.pbOpenFullHelp.setToolTip("在默认浏览器中打开插件目录下的 HELP.html")
        self.pbOpenFullHelp.clicked.connect(self._open_full_help)
        self.aboutLayout.addWidget(self.pbOpenFullHelp)
        # <<< 手工追加结束 <<<

        # 帮助内容显示区域
        self.aboutWebView = QtWidgets.QTextBrowser()
        self.aboutWebView.setOpenExternalLinks(True)
        self.aboutWebView.setHtml(self._get_about_html())

        self.aboutLayout.addWidget(self.aboutWebView)

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

        # 代码操作按钮
        self.codeButtonLayout = QtWidgets.QHBoxLayout()
        self.pbRunCode = QtWidgets.QPushButton("▶ 运行代码")
        self.pbRunCode.setStyleSheet("""
            QPushButton { background-color: #5CB85C; color: white; border-radius: 4px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background-color: #4CAE4C; }
        """)
        self.pbLoadCode = QtWidgets.QPushButton("📂 从文件读取")
        self.pbLoadCode.setStyleSheet("""
            QPushButton { background-color: #5BC0DE; color: white; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #46B8DA; }
        """)
        self.pbCopyCode = QtWidgets.QPushButton("📋 复制代码")
        self.pbCopyCode.setStyleSheet("""
            QPushButton { background-color: #6c757d; color: white; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #5a6268; }
        """)
        self.pbSaveCode = QtWidgets.QPushButton("💾 保存代码")
        self.pbSaveCode.setStyleSheet("""
            QPushButton { background-color: #17a2b8; color: white; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #138496; }
        """)
        self.pbClearCode = QtWidgets.QPushButton("🗑️ 清空")
        self.pbClearCode.setStyleSheet("""
            QPushButton { background-color: #dc3545; color: white; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #c82333; }
        """)
        self.codeButtonLayout.addWidget(self.pbRunCode)
        self.codeButtonLayout.addWidget(self.pbLoadCode)
        self.codeButtonLayout.addWidget(self.pbCopyCode)
        self.codeButtonLayout.addWidget(self.pbSaveCode)
        self.codeButtonLayout.addWidget(self.pbClearCode)

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

        self.twTabs.addTab(self.tbMessages, "对话")
        self.twTabs.addTab(self.tbConversations, "对话列表")
        self.twTabs.addTab(self.tbSettings, "模型配置")
        self.twTabs.addTab(self.tbWorkflow, "工作流")
        self.twTabs.addTab(self.tbReports, "报告")
        self.twTabs.addTab(self.tbAbout, "帮助")

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

    def _build_tools_rows(self):
        """从 qgis_tools.TOOL_DEFINITIONS 动态生成内置工具表格行。

        工具数量会随版本增长，硬编码清单必然过期（旧版此处只写了 6 个）。
        读取失败时降级为文字提示，不让帮助页崩溃。
        """
        try:
            try:
                from .qgis_tools import TOOL_DEFINITIONS
            except ImportError:
                from qgis_tools import TOOL_DEFINITIONS
        except Exception:
            # TODO: 工具清单读取失败时的降级分支；定位并修复导入问题后可保留作保险
            return ('<tr><td colspan="3">工具清单暂时无法显示，请点击上方'
                    '「打开完整帮助文档」查看完整列表。</td></tr>')

        rows = []
        for tool in TOOL_DEFINITIONS:
            name = tool.get("name", "")
            if not name:
                continue
            desc = tool.get("description", "") or ""
            desc = desc.split("。")[0].split("；")[0]
            if len(desc) > 40:
                desc = desc[:40] + "…"
            rows.append(
                "<tr><td><code>%s</code></td><td>%s</td><td>🔧 工具</td></tr>"
                % (name, desc)
            )
        if not rows:
            return '<tr><td colspan="3">未加载到工具定义。</td></tr>'
        return "\n".join(rows)

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
        """生成帮助/关于页面的HTML内容（纯HTML/CSS，不依赖JavaScript）"""
        html = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {
    font-family: "Microsoft YaHei", Arial, sans-serif;
    line-height: 1.6;
    color: #333;
    padding: 10px;
    font-size: 12px;
}
h1 {
    color: #2c3e50;
    border-bottom: 2px solid #3498db;
    padding-bottom: 10px;
    font-size: 18px;
}
h2 {
    color: #2980b9;
    margin-top: 20px;
    font-size: 14px;
}
h3 {
    color: #27ae60;
    font-size: 13px;
}
code {
    background-color: #f4f4f4;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: Consolas, monospace;
    font-size: 11px;
}
pre {
    background-color: #f8f8f8;
    padding: 10px;
    border-radius: 5px;
    border-left: 4px solid #3498db;
    overflow-x: auto;
    font-size: 11px;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0;
    font-size: 11px;
}
th, td {
    border: 1px solid #ddd;
    padding: 8px;
    text-align: left;
}
th {
    background-color: #3498db;
    color: white;
}
tr:nth-child(even) {
    background-color: #f2f2f2;
}
.tip {
    background-color: #e7f3fe;
    border-left: 4px solid #2196F3;
    padding: 10px;
    margin: 10px 0;
    border-radius: 4px;
}
.warning {
    background-color: #fff3cd;
    border-left: 4px solid #ffc107;
    padding: 10px;
    margin: 10px 0;
    border-radius: 4px;
}
ul, ol {
    margin: 10px 0;
    padding-left: 20px;
}
li {
    margin: 5px 0;
}
a {
    color: #3498db;
    text-decoration: none;
}
a:hover {
    text-decoration: underline;
}
.feature-box {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 15px;
    border-radius: 10px;
    margin: 10px 0;
}
.feature-box h3 {
    color: white;
    margin-top: 0;
}
.version {
    color: #888;
    font-size: 11px;
}
/* 架构图样式 */
.arch-box {
    background: #f9f9f9;
    border: 2px solid #3498db;
    border-radius: 10px;
    padding: 15px;
    margin: 15px 0;
}
.arch-title {
    background: #3498db;
    color: white;
    padding: 8px 15px;
    border-radius: 5px;
    font-weight: bold;
    display: inline-block;
    margin-bottom: 10px;
}
.arch-item {
    background: white;
    border: 1px solid #ddd;
    border-radius: 5px;
    padding: 10px;
    margin: 5px 0;
    display: flex;
    align-items: center;
}
.arch-icon {
    font-size: 20px;
    margin-right: 10px;
}
.arch-arrow {
    text-align: center;
    font-size: 20px;
    color: #3498db;
    margin: 5px 0;
}
/* 流程图样式 */
.flow-container {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    margin: 15px 0;
}
.flow-step {
    background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
    color: white;
    padding: 12px 20px;
    border-radius: 25px;
    font-size: 12px;
    text-align: center;
    min-width: 120px;
}
.flow-arrow {
    font-size: 24px;
    color: #3498db;
}
</style>
</head>
<body>

<h1>🗺️ QGIS Agent</h1>
<p class="version">版本 __PLUGIN_VERSION__ | 将大语言模型嵌入QGIS的智能助手</p>

<div class="feature-box">
<h3>✨ 核心亮点</h3>
<ul>
<li>📚 <strong>RAG API 文档检索</strong> - 检索本地 PyQGIS API 文档</li>
<li>🧰 <strong>679 个 Processing 工具文档</strong> - tool_docs/ 下每个算法一份 TOML 参考</li>
<li>🐛 <strong>SmartDebugger</strong> - 工具失败自动诊断并改写重试（v2.2.0 起已接入）</li>
<li>🛡️ <strong>安全护栏</strong> - PyQGIS 代码 AST 扫描、危险操作确认、不可信数据净化</li>
<li>🔄 <strong>工作流固化</strong> - 可复用的任务流程 <span style="color:#ffd966;">【规划中，当前版本不可用】</span></li>
<li>❓ <strong>主动提问</strong> - 识别模糊请求 <span style="color:#ffd966;">【规划中，当前版本不可用】</span></li>
</ul>
</div>

<h2>🏗️ 系统架构</h2>

<div class="arch-box">
<div class="arch-title">🖥️ QGIS 主线程</div>
<div class="arch-item"><span class="arch-icon">🧩</span> <strong>QGISAgent</strong> - 主控制器</div>
<div class="arch-item"><span class="arch-icon">🪟</span> <strong>DockWidget</strong> - UI 面板</div>
<div class="arch-item"><span class="arch-icon">💬</span> <strong>Conversation</strong> - 会话管理</div>

<div class="arch-arrow">↓</div>

<div class="arch-title">⚙️ 工作线程</div>
<div class="arch-item"><span class="arch-icon">🔧</span> <strong>ToolAgentWorker</strong> - 异步执行</div>
<div class="arch-item"><span class="arch-icon">🧠</span> <strong>Processor</strong> - Agent 循环</div>

<div class="arch-arrow">↓</div>

<div class="arch-title">📚 RAG 引擎</div>
<div class="arch-item"><span class="arch-icon">📖</span> <strong>DocStore</strong> - SQLite FTS5（API 文档运行时构建，数量随 QGIS 版本变化）</div>
<div class="arch-item"><span class="arch-icon">🧰</span> <strong>ToolDocs</strong> - 679 个 Processing 算法/工具参考</div>

<div class="arch-arrow">↓</div>

<div class="arch-title">🔩 QGIS 工具层</div>
<div class="arch-item"><span class="arch-icon">📞</span> <strong>call_tool()</strong> - 线程桥</div>
<div class="arch-item"><span class="arch-icon">🗺️</span> <strong>QGIS API</strong> - QgsProject / iface / Processing</div>
</div>

<h2>🚀 快速开始</h2>

<h3>1. 配置 LLM API</h3>
<ol>
<li>打开 QGIS Agent 面板</li>
<li>切换到 <strong>模型配置</strong> 标签页</li>
<li>点击 <strong>+ 添加模型</strong></li>
<li>填写 API 端点和密钥</li>
</ol>

<div class="tip">
💡 <strong>支持的 LLM 提供商</strong>: DeepSeek, OpenAI, GLM, Gemini, MiMo, 以及任何 OpenAI 兼容 API
</div>

<h3>2. 基本使用</h3>
<table>
<tr><th>操作</th><th>示例指令</th></tr>
<tr><td>查看图层</td><td><code>查看当前项目有哪些图层</code></td></tr>
<tr><td>添加图层</td><td><code>添加图层 D:/data/roads.shp</code></td></tr>
<tr><td>缓冲区分析</td><td><code>对道路图层做100米缓冲区分析</code></td></tr>
<tr><td>裁剪数据</td><td><code>用AOI图层裁剪道路图层</code></td></tr>
<tr><td>属性查询</td><td><code>筛选面积大于100的建筑</code></td></tr>
</table>

<h2>🔧 核心功能</h2>

<h3>📚 RAG API 文档检索</h3>
<p>执行代码前自动查询 PyQGIS API 签名和参数：</p>

<div class="flow-container">
<div class="flow-step">👤 用户请求</div>
<div class="flow-arrow">→</div>
<div class="flow-step">🔍 RAG 检索</div>
<div class="flow-arrow">→</div>
<div class="flow-step">🧠 LLM 生成</div>
<div class="flow-arrow">→</div>
<div class="flow-step">⚙️ 执行分析</div>
</div>

<h3>🐛 SmartDebugger 智能调试</h3>
<p>代码执行失败时，自动分析错误并提供修复建议：</p>
<table>
<tr><th>错误类型</th><th>识别</th><th>建议</th></tr>
<tr><td>ImportError</td><td>✅</td><td>检查库安装</td></tr>
<tr><td>QgsVectorLayer 错误</td><td>✅</td><td>检查路径</td></tr>
<tr><td>Processing 算法错误</td><td>✅</td><td>检查算法ID</td></tr>
<tr><td>几何错误</td><td>✅</td><td>修复几何</td></tr>
</table>

<h3>🔄 工作流固化 <span style="color:#c0392b;">【规划中，当前版本不可用】</span></h3>
<p>将对话中的工具调用序列保存为可重用工作流（<code>workflow_recorder.py</code> /
<code>workflow_executor.py</code> 代码已存在，但尚未接入对话链路）：</p>

<div class="flow-container">
<div class="flow-step">📝 第一次对话</div>
<div class="flow-arrow">→</div>
<div class="flow-step">💾 录制工作流</div>
<div class="flow-arrow">→</div>
<div class="flow-step">🔄 第二次对话</div>
<div class="flow-arrow">→</div>
<div class="flow-step">⚡ 直接执行</div>
</div>

<h3>❓ 主动提问 <span style="color:#c0392b;">【规划中，当前版本不可用】</span></h3>
<p>识别模糊请求，主动向用户澄清（代码已存在但尚未接入对话链路，当前不会触发）：</p>
<pre>
用户: 分析一下
Agent: 请具体说明要分析什么：
1. 统计面积、长度、数量
2. 分析空间分布特征
3. 检查数据质量
</pre>

<h2>📋 内置工具</h2>

<p>下表由 <code>qgis_tools.TOOL_DEFINITIONS</code> 动态生成，数量随版本变化：</p>

<table>
<tr><th>工具</th><th>功能</th><th>分类</th></tr>
__TOOLS_TABLE_ROWS__
</table>

<h2>🔌 工具文档系统</h2>

<p>内置 <strong>679 个 QGIS Processing 算法/工具参考文档</strong>（tool_docs/ 下每个算法一份 TOML）：</p>

<div class="flow-container">
<div class="flow-step">👤 用户请求</div>
<div class="flow-arrow">→</div>
<div class="flow-step">🔍 工具检索</div>
<div class="flow-arrow">→</div>
<div class="flow-step">📋 参数说明</div>
<div class="flow-arrow">→</div>
<div class="flow-step">💻 生成代码</div>
<div class="flow-arrow">→</div>
<div class="flow-step">⚙️ 执行分析</div>
</div>

<h3>支持的工具类型</h3>
<ul>
<li><strong>native:</strong> - QGIS 原生算法</li>
<li><strong>gdal:</strong> - GDAL/OGR 工具</li>
<li><strong>qgis:</strong> - QGIS 扩展工具</li>
<li><strong>3d:</strong> - 3D 分析工具</li>
</ul>

<h2>💡 最佳实践</h2>

<div class="tip">
<strong>提示 1:</strong> 使用绝对路径<br>
<code>添加图层 D:/data/roads.shp</code> ✓<br>
<code>添加图层 roads.shp</code> ✗
</div>

<div class="tip">
<strong>提示 2:</strong> 明确指定参数<br>
<code>对道路图层做100米缓冲区分析</code> ✓<br>
<code>分析一下</code> ✗
</div>

<div class="tip">
<strong>提示 3:</strong> 分步骤执行复杂任务<br>
1. 先加载数据<br>
2. 再执行分析<br>
3. 最后保存结果
</div>

<h2>🐛 故障排除</h2>

<table>
<tr><th>问题</th><th>解决方案</th></tr>
<tr><td>插件无法加载</td><td>检查 QGIS 版本 ≥ 3.22（插件元数据要求的最低版本）</td></tr>
<tr><td>API 调用失败</td><td>检查网络连接和 API 密钥</td></tr>
<tr><td>代码执行错误</td><td>查看 SmartDebugger 的修复建议</td></tr>
<tr><td>图层加载失败</td><td>检查文件路径是否正确</td></tr>
</table>

<h2>📄 更新日志</h2>

<h3>v2.2.0（开发中）</h3>
<ul>
<li>🐛 SmartDebugger 接线：工具失败自动诊断并回灌 LLM 改写重试</li>
<li>🛡️ 安全：PyQGIS 代码 AST 静态扫描、危险操作确认、不可信数据净化</li>
<li>💬 体验：错误信息分级为中文可操作提示；Enter 发送 / Shift+Enter 换行</li>
<li>🐛 修复「点停止后该对话永久报废」</li>
</ul>

<h3>v2.1.3 (2026-09-02)</h3>
<ul>
<li>✅ QGIS 4（PyQt6）兼容；本地 / 自托管模型 API Key 可留空</li>
<li>✅ Cloudflare 403 自动规避（附加浏览器 User-Agent）</li>
<li>🐛 修复「点击发送无反应」：无活动对话自动创建，异常改为可见红字提示</li>
</ul>

<h2>🔗 相关链接</h2>

<h2>🔗 相关链接</h2>
<ul>
<li><a href="https://github.com/bunkmr/qgis_agent">GitHub 仓库</a></li>
<li><a href="https://github.com/bunkmr/qgis_agent/issues">问题反馈</a></li>
<li><a href="https://qgis.org">QGIS 官网</a></li>
</ul>

<h2>📝 许可证</h2>
<p>MIT License</p>

<hr>
<p style="text-align: center; color: #888;">
Made with ❤️ by bunkmr<br>
Inspired by SpatialAnalysisAgent (GIBD, Penn State University)
</p>

</body>
</html>
"""
        # 版本号 / 工具清单不在此处硬编码（本文件为 Designer 风格文件，硬编码会随发版过期），
        # 统一在运行时注入：版本号取 config.PLUGIN_VERSION，工具行取 qgis_tools.TOOL_DEFINITIONS。
        return (html
                .replace("__PLUGIN_VERSION__", self._plugin_version())
                .replace("__TOOLS_TABLE_ROWS__", self._build_tools_rows()))

    def retranslateUi(self):
        pass
