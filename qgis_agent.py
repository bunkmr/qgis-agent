# -*- coding: utf-8 -*-

import os
import sys
import re
import html as html_module

from qgis.PyQt.QtCore import (
    QSettings, QTranslator, QCoreApplication, Qt, QTimer
)
from qgis.PyQt.QtGui import QIcon, QTextCursor, QClipboard, QPalette
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QPushButton, QPlainTextEdit, QLineEdit,
    QDockWidget, QApplication, QMessageBox, QLabel, QVBoxLayout, QHBoxLayout,
    QComboBox, QTableWidgetItem, QFrame, QToolBar
)
from qgis.utils import iface

from .package_manager import PackageManager

required_modules = [
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langchain_deepseek",
    "requests",
]
package_manager = PackageManager(required_modules)


def _soft_import(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


_HAS_LLM_LIBS = all(_soft_import(m) for m in required_modules)


class QGISAgent:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale = QSettings().value("locale/userLocale")[0:2]
        locale_path = os.path.join(self.plugin_dir, "i18n", f"QGISAgent_{locale}.qm")
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr("&QGIS Agent")
        self.toolbar = self.iface.addToolBar("QGIS Agent")
        self.toolbar.setObjectName("QGISAgentToolbar")

        # 将工具栏放到Python控制台后面
        self._position_toolbar_after_console()

        self.plugin_is_active = False
        self.dockwidget = None
        self.edit_dialog = None
        self.live_conversation_id = None
        self.live_conversation = None
        self.dataloader = None
        self.console_text = ""
        self.console_tracker = QTimer()
        self.new_editor = None

    def _position_toolbar_after_console(self):
        """将工具栏放到Python控制台后面"""
        try:
            # 获取主窗口
            main_window = self.iface.mainWindow()

            # 查找Python控制台工具栏
            for toolbar in main_window.findChildren(QToolBar):
                title = toolbar.windowTitle()
                if "Python" in title or "Console" in title or "控制台" in title:
                    # 获取工具栏区域
                    area = main_window.toolBarArea(toolbar)
                    # 将QGIS Agent工具栏添加到同一区域
                    main_window.addToolBar(area, self.toolbar)
                    # 设置为最后一个位置
                    main_window.addToolBarBreak(area)
                    break
        except Exception as e:
            # 如果找不到Python控制台，使用默认位置
            print(f"Warning: Could not find Python console toolbar: {e}")

    def tr(self, message):
        return QCoreApplication.translate("QGISAgent", message)

    def add_action(self, icon_path, text, callback, enabled_flag=True,
                   add_to_menu=True, add_to_toolbar=True, status_tip=None,
                   whats_this=None, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.add_action(
            icon_path,
            text=self.tr("打开 QGIS Agent"),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )

    def onClosePlugin(self):
        self.dockwidget.closingPlugin.disconnect(self.onClosePlugin)
        self.plugin_is_active = False
        if self.dataloader:
            self.dataloader.close()

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&QGIS Agent"), action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def run(self):
        if not _HAS_LLM_LIBS:
            msg = QMessageBox()
            msg.setWindowTitle("缺少依赖")
            msg.setText("QGIS Agent 需要安装以下 Python 库：")
            detail = "\n".join(f"• {m}" for m in required_modules)
            msg.setInformativeText(detail + "\n\n是否尝试自动安装？")
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg.setDefaultButton(QMessageBox.Yes)
            if msg.exec_() == QMessageBox.Yes:
                missing = package_manager.check_dependencies()
                if missing:
                    ok = package_manager.install_missing()
                    if ok:
                        QMessageBox.information(None, "安装成功", "依赖安装完成，请重启 QGIS 后重新启用插件。")
                    else:
                        QMessageBox.warning(None, "安装失败",
                            "自动安装失败，请在 OSGeo4W Shell 中手动运行：\n\n"
                            f"pip install {' '.join(required_modules)}")
                else:
                    QMessageBox.information(None, "已就绪", "依赖已安装，请重启 QGIS。")
            return

        if not self.plugin_is_active:
            self.plugin_is_active = True
            self._init_plugin()

    def _init_plugin(self):
        from .dataloader import DataLoader
        from .conversation import Conversation
        from .dialog_new_conversation import NewConversationDialog
        try:
            from .qgis_agent_dockwidget_v2 import QGISAgentDockWidgetV2 as QGISAgentDockWidget
        except ImportError:
            from .qgis_agent_dockwidget import QGISAgentDockWidget
        from .utils import (
            generate_unique_id, get_current_timestamp, pack, extract_code, set_font_color
        )
        from .config import DB_NAME, PLUGIN_NAME

        # 先创建 dockwidget（后续信号连接依赖它）
        if self.dockwidget is None:
            self.dockwidget = QGISAgentDockWidget()

        # 在主线程中初始化工具调度桥接器（必须在任何工具调用前完成）
        from .qgis_tools import _init_main_thread_bridge, set_code_confirm_callback, set_skip_all_confirms
        _init_main_thread_bridge()
        # 设置全局代码确认回调
        set_code_confirm_callback(self._on_code_confirm_sync)

        # 连接"跳过确认"开关（底部栏和配置页两个 checkbox 保持同步）
        self.dockwidget.cbSkipConfirm.stateChanged.connect(self._on_skip_confirm_changed)
        self.dockwidget.cbSkipConfirmSettings.stateChanged.connect(self._on_skip_confirm_changed)
        # 互相联动
        self.dockwidget.cbSkipConfirm.stateChanged.connect(
            lambda state: self.dockwidget.cbSkipConfirmSettings.setChecked(state == Qt.Checked)
        )
        self.dockwidget.cbSkipConfirmSettings.stateChanged.connect(
            lambda state: self.dockwidget.cbSkipConfirm.setChecked(state == Qt.Checked)
        )

        # ── 恢复保存的设置 ──
        self._load_saved_settings()

        # ── 初始化 RAG 索引（首次自动构建） ──
        self._init_rag_index()

        self.dockwidget.closingPlugin.connect(self.onClosePlugin)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dockwidget)
        self.dockwidget.show()

        self.dataloader = DataLoader(DB_NAME)
        self.dataloader.connect()

        # 加载模型列表到下拉框
        self._load_model_selector()

        self.dockwidget.pbSend.clicked.connect(self._on_new_message_send)
        self.dockwidget.enterPressed.connect(self._on_new_message_send)
        self.dockwidget.stopRequested.connect(self._on_stop_requested)
        self.dockwidget.pbNew.clicked.connect(self._on_new_conversation)
        self.dockwidget.pbSearchConversationCard.clicked.connect(
            self._on_search_conversation
        )
        self.dockwidget.searchPressed.connect(self._on_search_conversation)
        self.dockwidget.switchClearMode.connect(self._switch_clear_mode)

        # 报告页签按钮事件
        self.dockwidget.pbRunCode.clicked.connect(self._on_run_code)
        self.dockwidget.pbLoadCode.clicked.connect(self._on_load_code)
        self.dockwidget.pbCopyCode.clicked.connect(self._on_copy_code)
        self.dockwidget.pbSaveCode.clicked.connect(self._on_save_code)
        self.dockwidget.pbClearCode.clicked.connect(self._on_clear_code)

        # 温度滑块变化时保存
        self.dockwidget.sliderTemperature.valueChanged.connect(self._on_temperature_changed)

        # 标签页切换时刷新模型配置页
        self.dockwidget.twTabs.currentChanged.connect(self._on_tab_changed)

        # 初始化模型配置标签页
        self._init_settings_tab()

        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.displayConversationCard(self.dataloader, slot_funcs)

        from .processor import Processor as _ProcessorClass
        self._Processor = _ProcessorClass
        self._generate_unique_id = generate_unique_id
        self._get_current_timestamp = get_current_timestamp
        self._pack = pack
        self._extract_code = extract_code
        self._set_font_color = set_font_color
        self._Conversation = Conversation
        self._NewEditDialog = NewConversationDialog

    def _load_model_selector(self):
        """加载可用模型到下拉框"""
        self.dockwidget.cbModelSelector.clear()
        self._model_selector_map = {}  # display -> llm_id
        llm_ids = self.dataloader.fetch_llm_list()
        for llm_id in llm_ids:
            name, endpoint, _ = self.dataloader.fetch_llm_info(llm_id)
            display = f"{name}"
            self._model_selector_map[display] = llm_id
            self.dockwidget.cbModelSelector.addItem(display)

        # 如果有当前对话，选中其模型
        if self.live_conversation and self.live_conversation.llmID:
            llm_id = self.live_conversation.llmID
            name, _, _ = self.dataloader.fetch_llm_info(llm_id)
            idx = self.dockwidget.cbModelSelector.findText(name)
            if idx >= 0:
                self.dockwidget.cbModelSelector.setCurrentIndex(idx)

    def _get_selected_llm_id(self):
        """获取当前下拉框选中的 llm_id"""
        display = self.dockwidget.cbModelSelector.currentText()
        return self._model_selector_map.get(display, "")

    def _get_temperature(self):
        """获取当前 temperature 滑块的值"""
        return self.dockwidget.sliderTemperature.value() / 100.0


    def _on_new_message_send(self):
        message = self.dockwidget.ptMessage.toPlainText()
        if not message:
            return

        if self.live_conversation is None:
            self._on_new_conversation()
            if self.live_conversation is None:
                return

        self.dockwidget.ptMessage.clear()

        # 如果用户切换了模型选择器中的模型，更新对话的 llmID
        selected_llm = self._get_selected_llm_id()
        temperature = self._get_temperature()
        need_recreate = (
            (selected_llm and selected_llm != self.live_conversation.llmID)
            or (temperature != getattr(self.live_conversation.processor, 'temperature', 0.0))
        )
        if need_recreate:
            if selected_llm:
                self.live_conversation.meta_info["llmID"] = selected_llm
                self.live_conversation.provider, self.live_conversation.model_name = \
                    self.dataloader.get_llm_info(selected_llm)
            else:
                selected_llm = self.live_conversation.llmID
            # 重新创建 processor 以使用新模型/温度
            self.live_conversation.processor = self._Processor(
                selected_llm, self.live_conversation.ID, self.dataloader, temperature=temperature
            )
            self.live_conversation.processor.thinking.connect(self.live_conversation.llm_thinking.emit)
            self.live_conversation.processor.tool_status.connect(self.live_conversation.llm_tool_status.emit)
            self.live_conversation.processor._code_confirm_callback = self._on_code_confirm
            self.dataloader.update_conversation_info(self.live_conversation.meta_info)

        response_type = "Agent"

        if self.live_conversation is not None:
            # 先显示用户消息
            font_color = self._set_font_color(self.dockwidget.txHistory.palette().color(QPalette.Base))
            safe_message = html_module.escape(message)
            user_html = f"""
                <div style="margin:0;padding:0;line-height:1;text-align:right;color:#6baad1;">
                    用户 {self._get_current_timestamp()}
                </div>
                <div style="margin:0;padding:0;line-height:1;text-align:right;color:{font_color};">
                    {safe_message}
                </div>
            """
            self.dockwidget.txHistory.append(user_html)

            self.live_conversation.llm_response.connect(self._on_response_received)
            self.live_conversation.llm_thinking.connect(self._on_thinking)
            self.live_conversation.llm_tool_status.connect(self._on_tool_status)
            self.live_conversation.llm_workflow_update.connect(self._on_workflow_update)
            self.live_conversation.llm_code_update.connect(self._on_code_update)
            self.live_conversation.llm_execution_log.connect(self._on_execution_log)
            self.live_conversation.llm_interrupted.connect(self._on_response_error)
            self.live_conversation.update_user_prompt(message, response_type)

            # 切换为发送状态：隐藏发送按钮，显示停止按钮
            self.dockwidget.set_sending_state(True)
            self.dockwidget.disableAllButtons()
            self.dockwidget.disableAllTextEdit()

    def _on_response_received(self, response, workflow, model_path):
        if self.live_conversation is not None:
            self.dockwidget.set_sending_state(False)
            self.dockwidget.enableAllButtons()
            self.dockwidget.enableAllTextEdit()
            self.live_conversation.llm_response.disconnect(self._on_response_received)
            self.live_conversation.llm_thinking.disconnect(self._on_thinking)
            try:
                self.live_conversation.llm_tool_status.disconnect(self._on_tool_status)
            except Exception:
                pass
            self.live_conversation.llm_interrupted.disconnect(self._on_response_error)

            # 清除流式标记
            self.dockwidget.finalizeThinking()

            # 确保数据库连接有效（工作线程可能会重置 connection）
            if self.dataloader.connection is None:
                try:
                    self.dataloader.connect()
                except Exception:
                    pass

            self.dockwidget.updateConversation(self.live_conversation)
            self.dockwidget.updateGeneralInfo(self.live_conversation)

            slot_funcs = [
                self._on_conversation_load,
                self._on_conversation_delete,
                self._on_conversation_edit,
            ]
            self.dockwidget.updateConversationCard(
                self.live_conversation.meta_info, slot_funcs
            )
            self.dataloader.update_conversation_info(self.live_conversation.meta_info)

    def _on_thinking(self, partial_text):
        """实时显示模型思考内容"""
        self.dockwidget.showThinking(partial_text)

    def _on_tool_status(self, status_text):
        """显示工具调用状态"""
        self.dockwidget.showToolStatus(status_text)

    def _on_workflow_update(self, workflow_data):
        """更新工作流可视化显示"""
        self.dockwidget.update_workflow_display(workflow_data)

    def _on_code_update(self, code):
        """更新代码编辑器"""
        self.dockwidget.update_code_editor(code)

    def _on_execution_log(self, log_text):
        """追加执行日志"""
        self.dockwidget.append_execution_log(log_text)

    # ── 报告页签按钮事件 ──

    def _on_run_code(self):
        """运行代码编辑器中的代码"""
        code = self.dockwidget.codeEditor.toPlainText()
        if not code:
            self.dockwidget.append_execution_log("⚠️ 没有可执行的代码")
            return

        self.dockwidget.append_execution_log(f"▶ 开始执行代码...")
        self.dockwidget.hide_debug_analysis()

        # 在工作线程中执行代码
        from .qgis_tools import execute_pyqgis
        result = execute_pyqgis(code)

        if result.get("executed"):
            self.dockwidget.append_execution_log(f"✅ 代码执行成功")
            if result.get("stdout"):
                self.dockwidget.append_execution_log(f"输出:\n{result['stdout']}")
            if result.get("stderr"):
                self.dockwidget.append_execution_log(f"警告:\n{result['stderr']}")
        else:
            self.dockwidget.append_execution_log(f"❌ 代码执行失败: {result.get('error', 'unknown')}")

            # 显示错误分析
            if "debug_analysis" in result:
                self.dockwidget.show_debug_analysis(result["debug_analysis"])

    def _on_load_code(self):
        """从文件加载代码"""
        code = self.dockwidget.load_code_from_file()
        if code:
            self.dockwidget.append_execution_log(f"📂 已加载代码文件")

    def _on_copy_code(self):
        """复制代码到剪贴板"""
        if self.dockwidget.copy_code_to_clipboard():
            self.dockwidget.append_execution_log(f"📋 代码已复制到剪贴板")

    def _on_save_code(self):
        """保存代码到文件"""
        file_path = self.dockwidget.save_code_to_file()
        if file_path:
            self.dockwidget.append_execution_log(f"💾 代码已保存到: {file_path}")

    def _on_clear_code(self):
        """清空代码编辑器"""
        self.dockwidget.clear_code_editor()
        self.dockwidget.append_execution_log(f"🗑️ 已清空代码编辑器")

    def _on_response_error(self, error_message):
        self.dockwidget.set_sending_state(False)
        self.dockwidget.enableAllButtons()
        self.dockwidget.enableAllTextEdit()
        self.live_conversation.llm_response.disconnect(self._on_response_received)
        self.live_conversation.llm_thinking.disconnect(self._on_thinking)
        try:
            self.live_conversation.llm_tool_status.disconnect(self._on_tool_status)
        except Exception:
            pass
        self.live_conversation.llm_interrupted.disconnect(self._on_response_error)
        self.dockwidget.finalizeThinking()
        self.dockwidget.txHistory.append(f"<p style='color:red'>错误: {html_module.escape(error_message)}</p>")

    def _on_code_confirm(self, tool_name, code_preview, callback):
        """代码执行确认对话框 — 借鉴 QGPT Agent 的安全确认机制。
        
        在 execute_pyqgis 或 execute_processing 执行前弹窗，
        让用户确认或取消代码执行。
        
        Args:
            tool_name: 工具名称 (execute_pyqgis / execute_processing)
            code_preview: 代码预览文本
            callback: 确认后调用的回调函数，传入 bool (True=确认, False=取消)
        """
        msg = QMessageBox()
        msg.setWindowTitle("代码执行确认")
        msg.setIcon(QMessageBox.Warning)
        msg.setText(f"即将执行 {tool_name}，是否继续？")
        msg.setInformativeText("请检查代码是否正确，确认无误后点击「执行」。")
        msg.setDetailedText(code_preview)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.button(QMessageBox.Yes).setText("执行")
        msg.button(QMessageBox.No).setText("取消")
        
        result = msg.exec_()
        callback(result == QMessageBox.Yes)

    def _on_code_confirm_sync(self, tool_name, code_preview):
        """同步版本的代码确认（用于全局回调，返回 bool）"""
        msg = QMessageBox()
        msg.setWindowTitle("代码执行确认")
        msg.setIcon(QMessageBox.Warning)
        msg.setText(f"即将执行 {tool_name}，是否继续？")
        msg.setInformativeText("请检查代码是否正确，确认无误后点击「执行」。")
        msg.setDetailedText(code_preview)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.button(QMessageBox.Yes).setText("执行")
        msg.button(QMessageBox.No).setText("取消")
        
        result = msg.exec_()
        return result == QMessageBox.Yes

    def _on_skip_confirm_changed(self, state):
        """当"跳过确认"checkbox 状态变化时更新全局开关并保存"""
        from .qgis_tools import set_skip_all_confirms
        set_skip_all_confirms(state == Qt.Checked)
        # 保存设置
        settings = QSettings("QGIS", "QGISAgent")
        settings.setValue("skipConfirm", state == Qt.Checked)

    def _load_saved_settings(self):
        """加载保存的设置"""
        settings = QSettings("QGIS", "QGISAgent")

        # 恢复跳过确认设置
        skip_confirm = settings.value("skipConfirm", False, type=bool)
        self.dockwidget.cbSkipConfirm.setChecked(skip_confirm)
        self.dockwidget.cbSkipConfirmSettings.setChecked(skip_confirm)

        # 恢复温度设置
        temperature = settings.value("temperature", 0, type=int)
        self.dockwidget.sliderTemperature.setValue(temperature)

    def _on_temperature_changed(self, value):
        """温度滑块变化时保存设置"""
        settings = QSettings("QGIS", "QGISAgent")
        settings.setValue("temperature", value)

    def _init_rag_index(self):
        """初始化 RAG API 文档索引（首次使用时自动构建）"""
        try:
            from .rag import DocStore, init_retriever, generate_pyqgis_docs
            store = DocStore()
            init_retriever(store)
            stats = store.get_stats()
            # 如果索引为空，自动构建
            if stats["api_docs"] == 0:
                try:
                    from qgis.PyQt.QtWidgets import QMessageBox
                    reply = QMessageBox.question(
                        None, "QGIS Agent",
                        "首次使用需要构建 PyQGIS API 文档索引（约 10-30 秒），\n"
                        "这将显著提升代码生成的准确性。是否立即构建？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        generate_pyqgis_docs(store)
                        new_stats = store.get_stats()
                        QMessageBox.information(
                            None, "QGIS Agent",
                            f"API 文档索引构建完成！\n"
                            f"共索引 {new_stats['api_docs']} 个 API 条目。"
                        )
                except Exception:
                    pass  # 构建失败静默跳过，不影响插件使用
        except Exception:
            pass  # RAG 初始化失败不阻塞插件

    def _on_stop_requested(self):
        """用户点击停止按钮"""
        if self.live_conversation:
            self.live_conversation.stop()
        self.dockwidget.set_sending_state(False)
        self.dockwidget.enableAllButtons()
        self.dockwidget.enableAllTextEdit()
        self.dockwidget.finalizeThinking()
        self.dockwidget.txHistory.append("<p style='color:#888;'>⏹ 已停止生成</p>")

    def _on_new_conversation(self):
        from .dialog_new_conversation import NewConversationDialog as NewEditDialog

        if self.edit_dialog is None or not self.edit_dialog.isVisible():
            if self.dataloader is None:
                return

            # 新建对话：使用当前选中的模型
            llm_id = self._get_selected_llm_id()
            if not llm_id:
                # 没有模型可用，提示用户先配置
                QMessageBox.warning(None, "无可用模型", "请先在「模型配置」标签页中添加 LLM 模型。")
                self.dockwidget.twTabs.setCurrentWidget(self.dockwidget.tbSettings)
                return

            self.edit_dialog = NewEditDialog(self.dataloader, llm_id=llm_id)
            self.edit_dialog.show()
            if self.edit_dialog.exec_() == QDialog.Accepted:
                title, description, api_key = (
                    self.edit_dialog.get_metadata()
                )
                created = self._get_current_timestamp()
                modified = created
                self.live_conversation_id = self._generate_unique_id()

                meta_info = self._pack(
                    (
                        self.live_conversation_id, llm_id, title, description,
                        created, modified, 0, 0, "local"
                    ),
                    "conversation",
                )
                self.dataloader.create_conversation(meta_info)
                if api_key:
                    self.dataloader.update_api_key(api_key, llm_id)

                self.live_conversation = self._Conversation(
                    self.live_conversation_id, self.dataloader
                )
                # 设置 temperature
                self.live_conversation.processor.temperature = self._get_temperature()
                self.live_conversation.processor._code_confirm_callback = self._on_code_confirm

                self.dockwidget.twTabs.setCurrentWidget(self.dockwidget.tbMessages)
                self.dockwidget.updateConversation(self.live_conversation)
                self.dockwidget.updateGeneralInfo(self.live_conversation)

                slot_funcs = [
                    self._on_conversation_load,
                    self._on_conversation_delete,
                    self._on_conversation_edit,
                ]
                self.dockwidget.addConversationCard(meta_info, slot_funcs)

    def _on_conversation_load(self, conversation_id):
        self.live_conversation_id = conversation_id
        # 确保数据库连接有效（工作线程可能会重置 connection）
        if self.dataloader.connection is None:
            self.dataloader.connect()
        self.live_conversation = self._Conversation(conversation_id, self.dataloader)
        self.live_conversation.lastEdit = self._get_current_timestamp()
        self.live_conversation.processor.temperature = self._get_temperature()
        self.live_conversation.processor._code_confirm_callback = self._on_code_confirm
        self.dataloader.update_conversation_info(self.live_conversation.meta_info)

        # 同步模型选择器
        if self.live_conversation.llmID:
            name, _, _ = self.dataloader.fetch_llm_info(self.live_conversation.llmID)
            idx = self.dockwidget.cbModelSelector.findText(name)
            if idx >= 0:
                self.dockwidget.cbModelSelector.setCurrentIndex(idx)

        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.updateConversationCard(
            self.live_conversation.meta_info, slot_funcs
        )
        self.dockwidget.twTabs.setCurrentWidget(self.dockwidget.tbMessages)
        self.dockwidget.updateConversation(self.live_conversation)
        self.dockwidget.updateGeneralInfo(self.live_conversation)

    def _on_conversation_delete(self, conversation_id: str):
        self.dataloader.delete_conversation(conversation_id)
        if self.live_conversation_id == conversation_id:
            self.live_conversation = None
            self.dockwidget.txHistory.clear()
            self.dockwidget.lbTitle.clear()
            self.dockwidget.lbDescription.clear()
            self.dockwidget.lbMetadata.clear()
            self.dockwidget.removeConversationCard(conversation_id)
        else:
            self.dockwidget.removeConversationCard(conversation_id)

    def _on_conversation_edit(self, conversation_id: str):
        from .dialog_new_conversation import NewConversationDialog as NewEditDialog

        if self.edit_dialog is None or not self.edit_dialog.isVisible():
            meta_info = self.dataloader.select_conversation_info(conversation_id)
            title = meta_info.get("title", "")
            description = meta_info.get("description", "")
            llm_id = meta_info.get("llmID", "")

            self.edit_dialog = NewEditDialog(
                self.dataloader,
                title,
                description,
                llm_id,
            )
            self.edit_dialog.show()

            if self.edit_dialog.exec_() == QDialog.Accepted:
                new_title, new_description, api_key = self.edit_dialog.get_metadata()

                # 更新 meta_info 中的字段
                meta_info["title"] = new_title
                meta_info["description"] = new_description
                meta_info["modified"] = self._get_current_timestamp()

                self.dataloader.update_conversation_info(meta_info)
                if api_key:
                    self.dataloader.update_api_key(api_key, meta_info["llmID"])

                if conversation_id == self.live_conversation_id:
                    self.live_conversation = self._Conversation(conversation_id, self.dataloader)
                    self.dockwidget.updateGeneralInfo(self.live_conversation)

                slot_funcs = [
                    self._on_conversation_load,
                    self._on_conversation_delete,
                    self._on_conversation_edit,
                ]
                self.dockwidget.updateConversationCard(
                    meta_info, slot_funcs
                )

    def _on_search_conversation(self):
        search_text = self.dockwidget.ptSearchConversationCard.toPlainText()
        if not search_text:
            return

        def search_filter(meta_info, keyword=search_text):
            return (keyword.lower() in meta_info["title"].lower() or
                    keyword.lower() in meta_info["description"].lower())

        def highlight(full_text, keyword=search_text):
            pattern = re.compile(f"({re.escape(keyword)})", re.IGNORECASE)
            return pattern.sub(
                r'<span style="background-color: yellow">\1</span>', full_text
            )

        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.displayConversationCard(
            self.dataloader, slot_funcs, search_filter, highlight
        )

        self.dockwidget.pbSearchConversationCard.clicked.disconnect(
            self._on_search_conversation
        )
        self.dockwidget.searchPressed.disconnect(self._on_search_conversation)
        self.dockwidget.pbSearchConversationCard.setText("取消")
        self.dockwidget.pbSearchConversationCard.clicked.connect(
            self._switch_clear_mode
        )

    def _switch_clear_mode(self):
        slot_funcs = [
            self._on_conversation_load,
            self._on_conversation_delete,
            self._on_conversation_edit,
        ]
        self.dockwidget.displayConversationCard(self.dataloader, slot_funcs)
        self.dockwidget.pbSearchConversationCard.clicked.connect(
            self._on_search_conversation
        )
        self.dockwidget.searchPressed.connect(self._on_search_conversation)
        self.dockwidget.pbSearchConversationCard.setText("搜索")

    def _on_tab_changed(self, index):
        """标签页切换时刷新模型配置页"""
        # 模型配置标签页是 index 2
        if index == 2:
            self._refresh_settings_tab()

    def _init_settings_tab(self):
        """初始化模型配置标签页"""
        self._settings_row_data = {}  # row_idx -> {"llm_id": str, "name": str}

        self.dockwidget.btnAddModel.clicked.connect(self._add_model_row)

    def _refresh_settings_tab(self):
        """刷新模型配置表格"""
        table = self.dockwidget.settingsTable
        # 断开之前的按钮信号，避免重复连接
        table.setRowCount(0)
        self._settings_row_data = {}

        rows = self.dataloader.fetch_all_config()
        for i, row in enumerate(rows):
            llm_id, name, endpoint, api_key = row
            self._set_settings_row(i, name, endpoint, api_key, llm_id)

    def _set_settings_row(self, row_idx, name, endpoint, api_key, llm_id=None):
        """设置配置表格的一行数据"""
        import uuid

        table = self.dockwidget.settingsTable
        if row_idx >= table.rowCount():
            table.insertRow(row_idx)

        if llm_id is None:
            llm_id = f"Custom::{uuid.uuid4().hex[:8]}"

        self._settings_row_data[row_idx] = {"llm_id": llm_id, "name": name}

        # 第0列：可编辑的模型名称
        name_item = QTableWidgetItem(name)
        table.setItem(row_idx, 0, name_item)

        # 第1列：API 端点（可编辑）
        endpoint_item = QTableWidgetItem(endpoint)
        table.setItem(row_idx, 1, endpoint_item)

        # 第2列：API Key（密码模式，使用 QLineEdit 设置为密码模式）
        key_widget = QLineEdit()
        key_widget.setEchoMode(QLineEdit.Password)
        key_widget.setText(api_key)
        key_widget.setPlaceholderText("输入 API Key")
        key_widget.setStyleSheet("QLineEdit { border: none; padding: 2px; }")
        # 点击查看/隐藏切换
        key_widget.setClearButtonEnabled(False)
        table.setCellWidget(row_idx, 2, key_widget)

        # 第3列：删除按钮
        del_btn = QPushButton("删除")
        del_btn.setStyleSheet(
            "QPushButton { background-color: #FA7070; color: white; border-radius: 3px; padding: 2px 8px; font-size: 11px; }"
            " QPushButton:hover { background-color: #E05050; }"
        )
        del_btn.clicked.connect(lambda _, r=row_idx: self._delete_model_row(r))
        table.setCellWidget(row_idx, 3, del_btn)

    def _add_model_row(self):
        """添加新模型行 — 弹出参考信息对话框"""
        # 弹出参考信息对话框
        ref_dlg = AddModelReferenceDialog(self.dockwidget)
        if ref_dlg.exec_() == QDialog.Accepted:
            name, endpoint, api_key = ref_dlg.get_values()
        else:
            return  # 用户取消

        table = self.dockwidget.settingsTable
        row_idx = table.rowCount()
        self._set_settings_row(row_idx, name, endpoint, api_key)

        # 自动保存
        self._save_settings_tab()

    def _delete_model_row(self, row_idx):
        """删除模型行并自动保存"""
        if row_idx in self._settings_row_data:
            llm_id = self._settings_row_data[row_idx]["llm_id"]
            if llm_id:
                self.dataloader.delete_llm_config(llm_id)

        table = self.dockwidget.settingsTable
        table.removeRow(row_idx)

        # 重建 row_data 映射
        new_data = {}
        for i in range(table.rowCount()):
            if i < row_idx and i in self._settings_row_data:
                new_data[i] = self._settings_row_data[i]
            elif i >= row_idx:
                old_idx = i + 1
                if old_idx in self._settings_row_data:
                    new_data[i] = self._settings_row_data[old_idx]
        self._settings_row_data = new_data

        # 自动保存
        self._save_settings_tab()

    def _save_settings_tab(self):
        """保存配置页所有模型到数据库"""
        table = self.dockwidget.settingsTable
        for i in range(table.rowCount()):
            name_item = table.item(i, 0)
            endpoint_item = table.item(i, 1)
            key_widget = table.cellWidget(i, 2)

            if not name_item or not endpoint_item:
                continue

            name = name_item.text().strip()
            endpoint = endpoint_item.text().strip()
            api_key = key_widget.text().strip() if key_widget else ""

            if not name or not endpoint:
                continue

            if i in self._settings_row_data:
                llm_id = self._settings_row_data[i]["llm_id"]
            else:
                import uuid
                llm_id = f"Custom::{uuid.uuid4().hex[:8]}"

            self.dataloader.insert_llm_config(llm_id, name, endpoint, api_key)

        self.dataloader.reload_llm_config()
        self._load_model_selector()

    def _run_in_console(self, code: str):
        console_widget = iface.mainWindow().findChild(QDockWidget, "PythonConsole")
        if not console_widget or not console_widget.isVisible():
            iface.actionShowPythonDialog().trigger()
            console_widget = iface.mainWindow().findChild(QDockWidget, "PythonConsole")

        import console

        python_console = console_widget.findChild(
            console.console.PythonConsoleWidget
        )
        QApplication.clipboard().setText(code)
        python_console.pasteEditor()


# ---- 添加模型参考信息对话框 ----

_MODEL_REFERENCE_DATA = [
    {
        "name": "DeepSeek",
        "models": "deepseek-chat, deepseek-reasoner",
        "endpoint": "https://api.deepseek.com/v1",
        "note": "需申请 API Key: platform.deepseek.com",
    },
    {
        "name": "OpenAI",
        "models": "gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo",
        "endpoint": "https://api.openai.com/v1",
        "note": "需申请 API Key: platform.openai.com",
    },
    {
        "name": "智谱 GLM",
        "models": "glm-4, glm-4v, glm-4-plus, glm-4-air, glm-4-flash",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/",
        "note": "需申请 API Key: open.bigmodel.cn",
    },
    {
        "name": "Gemini",
        "models": "gemini-2.0-flash, gemini-2.0-pro, gemini-1.5-pro",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "note": "需申请 API Key: aistudio.google.com",
    },
    {
        "name": "小米 MiMo",
        "models": "mimo-v2.5, mimo-v2.5-pro, mimo-v2-flash",
        "endpoint": "https://api.xiaomimimo.com/v1/chat/completions",
        "note": "小米 AI 开放平台",
    },
    {
        "name": "自定义 (OpenAI 兼容)",
        "models": "任意模型名（如 qwen-plus, claude-3-opus 等）",
        "endpoint": "https://your-api-endpoint.com/v1",
        "note": "任何兼容 OpenAI 接口的服务均可使用",
    },
]


class AddModelReferenceDialog(QDialog):
    """添加模型参考信息对话框 — 参考 WorkBuddy 风格"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加模型 — 参考信息")
        self.setMinimumSize(520, 440)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 标题
        title_lbl = QLabel("选择参考模板（可修改任何字段）")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title_lbl)

        # 参考信息下拉选择
        ref_layout = QHBoxLayout()
        ref_layout.addWidget(QLabel("参考:"))
        self.cbReference = QComboBox()
        ref_names = [r["name"] for r in _MODEL_REFERENCE_DATA]
        self.cbReference.addItems(ref_names)
        self.cbReference.currentIndexChanged.connect(self._on_reference_changed)
        ref_layout.addWidget(self.cbReference, 1)
        layout.addLayout(ref_layout)

        # 参考信息展示
        self.lblRefInfo = QLabel()
        self.lblRefInfo.setWordWrap(True)
        self.lblRefInfo.setStyleSheet(
            "background: #f5f5f5; border-radius: 4px; padding: 8px; font-size: 11px; color: #555;"
        )
        layout.addWidget(self.lblRefInfo)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #ddd;")
        layout.addWidget(line)

        # 模型名称
        layout.addWidget(QLabel("模型名称:"))
        self.ptName = QLineEdit()
        self.ptName.setPlaceholderText("例如: gpt-4o")
        layout.addWidget(self.ptName)

        # API 端点
        layout.addWidget(QLabel("API 端点:"))
        self.ptEndpoint = QLineEdit()
        self.ptEndpoint.setPlaceholderText("例如: https://api.openai.com/v1")
        layout.addWidget(self.ptEndpoint)

        # API Key（密码模式）
        layout.addWidget(QLabel("API Key:"))
        self.ptApiKey = QLineEdit()
        self.ptApiKey.setEchoMode(QLineEdit.Password)
        self.ptApiKey.setPlaceholderText("输入 API Key")
        layout.addWidget(self.ptApiKey)

        layout.addStretch()

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btnOk = QPushButton("添加")
        self.btnOk.setStyleSheet(
            "QPushButton { background-color: #4A90D9; color: white; border-radius: 4px; padding: 6px 24px; }"
            " QPushButton:hover { background-color: #357ABD; }"
        )
        self.btnOk.clicked.connect(self.accept)
        self.btnCancel = QPushButton("取消")
        self.btnCancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btnOk)
        btn_layout.addWidget(self.btnCancel)
        layout.addLayout(btn_layout)

        # 初始化第一个参考信息
        self._on_reference_changed(0)

    def _on_reference_changed(self, index):
        """切换参考模板时更新展示信息和预填字段"""
        if 0 <= index < len(_MODEL_REFERENCE_DATA):
            ref = _MODEL_REFERENCE_DATA[index]
            self.lblRefInfo.setText(
                f"<b>可用模型:</b> {ref['models']}<br>"
                f"<b>API 端点:</b> {ref['endpoint']}<br>"
                f"<b>说明:</b> {ref['note']}"
            )
            # 预填端点（用户可修改）
            self.ptEndpoint.setText(ref["endpoint"])
            # 不清除已输入的名称和 key，但如果是第一个端点模板则填入建议
            if not self.ptName.text():
                # 取第一个模型名作为建议
                first_model = ref["models"].split(",")[0].strip()
                self.ptName.setPlaceholderText(f"例如: {first_model}")

    def get_values(self):
        """返回 (name, endpoint, api_key)"""
        return (
            self.ptName.text().strip(),
            self.ptEndpoint.text().strip(),
            self.ptApiKey.text().strip(),
        )
