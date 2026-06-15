import html as html_module
from datetime import datetime

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt
from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QSizePolicy, QSpacerItem, QWidget, QPlainTextEdit
)
from qgis.PyQt.QtGui import QFont, QPalette

from .utils import handle_none_conversation, pack, unpack, format_description, create_markdown, set_font_color
from .qgis_agent_dockwidget_base_ui import Ui_QGISAgentDockWidget


class QGISAgentDockWidget(QtWidgets.QDockWidget, Ui_QGISAgentDockWidget):
    closingPlugin = pyqtSignal()
    enterPressed = pyqtSignal(str)
    searchPressed = pyqtSignal(str)
    switchClearMode = pyqtSignal(str)
    modelClicked = pyqtSignal(int)
    stopRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.conversationCards = {}
        self.scrollAreaWidget = None
        self.scrollAreaLayout = None
        self.setupUi(self)

        self.messagesLayout.setStretch(3, 1)
        self.ptMessage.setFixedHeight(40)

        self.pbStop.clicked.connect(self.stopRequested.emit)

        self.ptMessage.installEventFilter(self)
        self.ptSearchConversationCard.installEventFilter(self)

        self.twTabs.setCurrentWidget(self.tbMessages)

    def set_sending_state(self, is_sending):
        """切换发送/停止状态"""
        self.pbSend.setVisible(not is_sending)
        self.pbStop.setVisible(is_sending)
        self.cbModelSelector.setDisabled(is_sending)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def displayConversationCard(self, dataloader, slots_functions,
                                search_filter=lambda x: True,
                                highlight_rule=lambda x: x):
        self.scrollAreaLayout = QVBoxLayout()
        self.scrollAreaWidget = QWidget()
        self.saConversationCard.setWidget(self.scrollAreaWidget)
        self.scrollAreaWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        meta_table = dataloader.select_conversation_info()
        meta_table.sort(key=lambda info: datetime.strptime(info['modified'], "%m %d %Y %H:%M:%S"))

        for meta_info in meta_table:
            if search_filter(meta_info):
                self.addConversationCard(meta_info, slots_functions, highlight=highlight_rule)

        self.scrollAreaWidget.setLayout(self.scrollAreaLayout)

    def updateConversationCard(self, conversation_meta_info, slots_functions):
        conversation_id = conversation_meta_info['ID']
        self.removeConversationCard(conversation_id)
        self.addConversationCard(conversation_meta_info, slots_functions)

    def addConversationCard(self, meta_info, slots_functions, order=0, highlight=lambda x: x):
        on_load, on_delete, on_edit = slots_functions
        card = QGroupBox()
        layout = QVBoxLayout()

        conv_id, llm_id, title, desc, created, modified, msg_count, wf_count, user_id = unpack(meta_info, "conversation")

        title_label = QLabel(highlight(title))
        font = QFont()
        font.setBold(True)
        title_label.setFont(font)

        desc_label = QLabel(highlight(desc))
        desc_label.setWordWrap(True)

        metadata = f"创建: {created} | 模型: {llm_id} | 消息: {msg_count}"
        meta_label = QLabel(metadata)
        meta_label.setAlignment(Qt.AlignRight)

        btn_layout = QHBoxLayout()
        spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        btn_edit = QPushButton("编辑")
        btn_edit.setStyleSheet("QPushButton { background-color: #9DDE8B; }")
        btn_edit.clicked.connect(lambda: on_edit(conv_id))

        btn_delete = QPushButton("删除")
        btn_delete.setStyleSheet("QPushButton { background-color: #FA7070; }")
        btn_delete.clicked.connect(lambda: on_delete(conv_id))

        btn_open = QPushButton("打开")
        btn_open.clicked.connect(lambda: on_load(conv_id))

        btn_layout.addSpacerItem(spacer)
        btn_layout.addWidget(btn_edit)
        btn_layout.addWidget(btn_delete)
        btn_layout.addWidget(btn_open)

        layout.addWidget(title_label)
        layout.addWidget(desc_label)
        layout.addWidget(meta_label)
        layout.addLayout(btn_layout)
        card.setLayout(layout)

        self.scrollAreaLayout.insertWidget(order, card)
        self.conversationCards[conv_id] = card

    def removeConversationCard(self, conversation_id):
        if conversation_id in self.conversationCards:
            card = self.conversationCards[conversation_id]
            self.scrollAreaLayout.removeWidget(card)
            card.deleteLater()
            del self.conversationCards[conversation_id]

    @handle_none_conversation
    def updateGeneralInfo(self, conversation):
        self.lbTitle.setText(conversation.title)
        self.lbDescription.setText(format_description(conversation.description))
        self.lbMetadata.setText(conversation.get_metadata())

    @handle_none_conversation
    def updateConversation(self, conversation):
        current_html = ""
        interaction_history = conversation.fetch()
        font_color = set_font_color(self.txHistory.palette().color(QPalette.Base))

        for interaction in interaction_history:
            msg_dict = pack(interaction, "interaction")
            if msg_dict["typeMessage"] == "input":
                safe_text = html_module.escape(msg_dict["requestText"])
                new_msg = f"""
                    <div style="margin:0;padding:0;line-height:1;text-align:right;color:#6baad1;">
                        用户 {msg_dict["requestTime"]}
                    </div>
                    <div style="margin:0;padding:0;line-height:1;text-align:right;color:{font_color};">
                        {safe_text}
                    </div>
                """
                current_html += new_msg

            if msg_dict["typeMessage"] == "return":
                new_msg = f"""
                    <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;text-align:left;">
                        QGIS Agent {msg_dict["responseTime"]}
                    </div>
                    <div style="margin:0;padding:0;line-height:1;color:{font_color};text-align:left;">
                        {create_markdown(msg_dict["responseText"])}
                    </div>
                    <div><br></div>
                """
                current_html += new_msg

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def showThinking(self, partial_text, response_time=""):
        """实时显示模型思考/生成中的内容"""
        font_color = set_font_color(self.txHistory.palette().color(QPalette.Base))
        # 构建当前完整 HTML（保留历史 + 追加流式内容）
        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, 'toHtml') else ""

        # 用标记来定位流式内容块
        marker = '<!-- STREAMING_MARKER -->'
        thinking_html = f"""
            <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;text-align:left;">
                QGIS Agent 思考中... {response_time}
            </div>
            <div style="margin:0;padding:0;line-height:1;color:{font_color};text-align:left;">
                {create_markdown(partial_text)}
            </div>
            <span style="color:#FD8A8A;">▌</span>
            <div><br></div>
            {marker}
        """

        # 如果已有流式标记，替换掉旧内容；否则追加
        if marker in current_html:
            # 找到标记位置，替换标记之前到上一个消息之后的所有流式内容
            marker_pos = current_html.find(marker)
            # 找到流式块开始位置（倒数第二个 <div style="margin:0;padding:0;line-height:1;color:#FD8A8A;">）
            search_start = current_html.rfind(
                '<div style="margin:0;padding:0;line-height:1;color:#FD8A8A;">',
                0, marker_pos
            )
            if search_start >= 0:
                current_html = current_html[:search_start] + thinking_html
            else:
                current_html = current_html[:marker_pos] + thinking_html
        else:
            # 先移除可能残留的旧标记
            if marker in current_html:
                current_html = current_html.replace(marker, "")
            current_html += thinking_html

        self.txHistory.setHtml(current_html)
        self.txHistory.setReadOnly(True)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def finalizeThinking(self):
        """清除流式标记，准备显示最终结果"""
        current_html = self.txHistory.toHtml() if hasattr(self.txHistory, 'toHtml') else ""
        marker = '<!-- STREAMING_MARKER -->'
        current_html = current_html.replace(marker, "")
        self.txHistory.setHtml(current_html)

    def showToolStatus(self, status_text):
        """在聊天框中显示工具调用状态"""
        status_html = f"""
            <div style="margin:4px 0;padding:4px 8px;background-color:#2d2d2d;border-left:3px solid #4A90D9;border-radius:2px;color:#b0b0b0;font-size:12px;font-family:Consolas,monospace;">
                {html_module.escape(status_text)}
            </div>
        """
        self.txHistory.append(status_html)
        self.txHistory.verticalScrollBar().setValue(self.txHistory.verticalScrollBar().maximum())

    def disableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            if btn is self.pbStop:
                continue  # 停止按钮始终可用
            btn.setDisabled(True)

    def enableAllButtons(self):
        for btn in self.findChildren(QPushButton):
            if btn is self.pbStop:
                btn.setVisible(False)  # 隐藏停止按钮
                continue
            btn.setDisabled(False)

    def disableAllTextEdit(self):
        for te in self.findChildren(QPlainTextEdit):
            te.setDisabled(True)

    def enableAllTextEdit(self):
        for te in self.findChildren(QPlainTextEdit):
            te.setDisabled(False)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if obj is self.ptMessage and event.key() == Qt.Key_Return:
                self.enterPressed.emit(self.ptMessage.toPlainText())
                return True
            if obj is self.ptSearchConversationCard and event.key() == Qt.Key_Return:
                self.searchPressed.emit(self.ptSearchConversationCard.toPlainText())
                return True
        return super().eventFilter(obj, event)

    # ── 工作流可视化方法 ──

    def update_workflow_display(self, workflow_data):
        """
        更新工作流标签页的显示内容

        Args:
            workflow_data: 工作流数据字典
                {
                    "name": "工作流名称",
                    "status": "completed/running/failed",
                    "steps": [
                        {"id": "step1", "name": "步骤1", "status": "completed", "tool": "native:buffer"},
                        {"id": "step2", "name": "步骤2", "status": "running", "tool": "native:savefeatures"},
                    ],
                    "summary": "执行摘要"
                }
        """
        html_content = self._generate_workflow_html(workflow_data)
        self.workflowWebView.setHtml(html_content)

        # 更新摘要
        if "summary" in workflow_data:
            self.lblWorkflowSummary.setText(workflow_data["summary"])

    def _generate_workflow_html(self, workflow_data):
        """生成工作流HTML内容"""
        name = workflow_data.get("name", "未命名工作流")
        status = workflow_data.get("status", "pending")
        steps = workflow_data.get("steps", [])

        # 状态颜色映射
        status_colors = {
            "pending": "#cccccc",
            "running": "#ffcc00",
            "completed": "#66cc66",
            "failed": "#cc6666"
        }

        status_icons = {
            "pending": "⏳",
            "running": "⚙️",
            "completed": "✅",
            "failed": "❌"
        }

        html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{
    font-family: "Microsoft YaHei", Arial, sans-serif;
    padding: 10px;
    font-size: 12px;
    margin: 0;
}}
.workflow-container {{
    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
    padding: 15px;
    border-radius: 10px;
    margin: 10px 0;
}}
.workflow-title {{
    font-size: 16px;
    font-weight: bold;
    color: #2c3e50;
    margin-bottom: 10px;
}}
.workflow-status {{
    display: inline-block;
    padding: 5px 15px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: bold;
    color: white;
    margin-bottom: 15px;
}}
.step-container {{
    display: flex;
    align-items: center;
    margin: 10px 0;
    padding: 10px;
    background: white;
    border-radius: 8px;
    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
}}
.step-icon {{
    font-size: 24px;
    margin-right: 15px;
    min-width: 40px;
    text-align: center;
}}
.step-info {{
    flex: 1;
}}
.step-name {{
    font-weight: bold;
    color: #333;
    margin-bottom: 3px;
}}
.step-tool {{
    font-size: 11px;
    color: #666;
}}
.step-status {{
    padding: 3px 10px;
    border-radius: 15px;
    font-size: 11px;
    font-weight: bold;
}}
.arrow {{
    text-align: center;
    font-size: 20px;
    color: #3498db;
    margin: 5px 0;
}}
</style>
</head>
<body>
<div class="workflow-container">
    <div class="workflow-title">🔄 {name}</div>
    <div class="workflow-status" style="background-color: {status_colors.get(status, '#cccccc')}">
        {status_icons.get(status, '❓')} {status.upper()}
    </div>
"""

        for i, step in enumerate(steps):
            step_status = step.get("status", "pending")
            step_icon = status_icons.get(step_status, "❓")
            step_color = status_colors.get(step_status, "#cccccc")

            html += f"""
    <div class="step-container">
        <div class="step-icon">{step_icon}</div>
        <div class="step-info">
            <div class="step-name">{step.get('name', f'步骤 {i+1}')}</div>
            <div class="step-tool">🔧 {step.get('tool', 'unknown')}</div>
        </div>
        <div class="step-status" style="background-color: {step_color}; color: white;">
            {step_status.upper()}
        </div>
    </div>
"""
            if i < len(steps) - 1:
                html += '    <div class="arrow">↓</div>\n'

        html += """
</div>
</body>
</html>
"""
        return html

    def clear_workflow_display(self):
        """清空工作流显示"""
        self.workflowWebView.setHtml("""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {
    font-family: "Microsoft YaHei", Arial, sans-serif;
    padding: 20px;
    text-align: center;
    color: #666;
}
.icon { font-size: 48px; margin-bottom: 15px; }
.title { font-size: 16px; font-weight: bold; color: #333; margin-bottom: 10px; }
.desc { font-size: 12px; }
</style>
</head>
<body>
<div class="icon">🔄</div>
<div class="title">等待任务执行...</div>
<div class="desc">执行任务后，工作流将在此可视化展示。</div>
</body>
</html>
""")
        self.lblWorkflowSummary.setText("")

    # ── 报告页签方法 ──

    def update_code_editor(self, code):
        """更新代码编辑器"""
        self.codeEditor.setPlainText(code)

    def update_execution_log(self, log_text):
        """更新执行日志"""
        self.executionLog.setPlainText(log_text)

    def append_execution_log(self, log_text):
        """追加执行日志"""
        self.executionLog.appendPlainText(log_text)

    def show_debug_analysis(self, analysis):
        """显示错误分析"""
        self.lblDebugAnalysis.setVisible(True)
        self.debugAnalysisText.setVisible(True)

        html = f"""
<div style="font-family: Arial; font-size: 12px;">
    <p><strong>错误类型:</strong> {analysis.get('error_category', 'unknown')}</p>
    <p><strong>置信度:</strong> {analysis.get('confidence', 0)*100:.1f}%</p>
    <p><strong>建议:</strong></p>
    <ul>
"""
        for suggestion in analysis.get('suggestions', []):
            html += f"        <li>{suggestion}</li>\n"

        html += """    </ul>
</div>
"""
        self.debugAnalysisText.setHtml(html)

    def hide_debug_analysis(self):
        """隐藏错误分析"""
        self.lblDebugAnalysis.setVisible(False)
        self.debugAnalysisText.setVisible(False)

    def copy_code_to_clipboard(self):
        """复制代码到剪贴板"""
        from qgis.PyQt.QtWidgets import QApplication
        code = self.codeEditor.toPlainText()
        if code:
            QApplication.clipboard().setText(code)
            return True
        return False

    def save_code_to_file(self):
        """保存代码到文件"""
        from qgis.PyQt.QtWidgets import QFileDialog
        code = self.codeEditor.toPlainText()
        if not code:
            return None

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存代码", "", "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)
            return file_path
        return None

    def load_code_from_file(self):
        """从文件加载代码"""
        from qgis.PyQt.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开代码文件", "", "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            with open(file_path, 'r', encoding='utf-8') as f:
                code = f.read()
            self.codeEditor.setPlainText(code)
            return code
        return None

    def clear_code_editor(self):
        """清空代码编辑器"""
        self.codeEditor.clear()
        self.executionLog.clear()
        self.hide_debug_analysis()
