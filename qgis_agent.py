# -*- coding: utf-8 -*-

import os
import re
import html as html_module

from qgis.PyQt.QtCore import (
    QSettings, QTranslator, QCoreApplication, Qt, QTimer, pyqtSignal, QThread
)
from qgis.PyQt.QtGui import QIcon, QPalette, QFont
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QPushButton, QLineEdit, QPlainTextEdit,
    QDockWidget, QApplication, QMessageBox, QLabel, QVBoxLayout, QHBoxLayout,
    QComboBox, QTableWidgetItem, QFrame, QToolBar
)
from qgis.utils import iface

from .package_manager import PackageManager
import logging
logger = logging.getLogger(__name__)

required_modules = [
    "langchain_core",
    "langchain_openai",
    "langchain_deepseek",
]
package_manager = PackageManager(required_modules)


def _soft_import(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


_HAS_LLM_LIBS = all(_soft_import(m) for m in required_modules)


class CodeConfirmDialog(QDialog):
    """P1-4 自定义代码执行确认对话框。

    与旧实现（代码藏在 QMessageBox 的「详细」里，等于没确认）不同：
    • 代码预览**默认展开**且只读，用户一眼可见将执行的全部内容；
    • 提供三档授权，避免每次都手动点「执行」：
        - 仅此一次：本次执行，下次仍确认；
        - 本次会话允许该工具：本会话内同名工具自动放行（重启复位）；
        - 总是允许：写入 QSettings，跨重启长期免确认。
    """

    def __init__(self, parent, tool_name, code_preview):
        super().__init__(parent)
        self.decision = None  # "once" | "session" | "always" | None(取消)
        self.setWindowTitle("代码执行确认")
        self.setMinimumSize(580, 440)
        self.setModal(True)

        layout = QVBoxLayout(self)

        warn = QLabel(
            f"即将执行 <b>{html_module.escape(tool_name)}</b>，是否继续？\n"
            "请检查下方代码是否正确，确认无误后再点「执行」。"
        )
        warn.setWordWrap(True)
        layout.addWidget(warn)

        # 代码预览：默认展开、只读、等宽字体
        code_view = QPlainTextEdit()
        code_view.setPlainText(code_preview or "")
        code_view.setReadOnly(True)
        code_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Consolas, Monaco, monospace")
        mono.setPointSize(11)
        code_view.setFont(mono)
        layout.addWidget(code_view, 1)

        # 按钮行
        btn_once = QPushButton("仅此一次")
        btn_session = QPushButton("本次会话允许该工具")
        btn_always = QPushButton("总是允许")
        btn_cancel = QPushButton("取消")
        for _b in (btn_once, btn_session, btn_always, btn_cancel):
            _b.setStyleSheet("QPushButton { padding: 6px 10px; }")
        btn_cancel.setStyleSheet(
            "QPushButton { padding: 6px 10px; background:#eeeeee; }"
        )

        row = QHBoxLayout()
        row.addWidget(btn_once)
        row.addWidget(btn_session)
        row.addWidget(btn_always)
        row.addStretch(1)
        row.addWidget(btn_cancel)
        layout.addLayout(row)

        btn_once.clicked.connect(lambda _checked=False: self._choose("once"))
        btn_session.clicked.connect(lambda _checked=False: self._choose("session"))
        btn_always.clicked.connect(lambda _checked=False: self._choose("always"))
        btn_cancel.clicked.connect(self.reject)

    def _choose(self, decision):
        self.decision = decision
        self.accept()


class _RagBuildWorker(QThread):
    """P1-7 后台构建 PyQGIS API 文档索引，避免首启界面假死 10-30 秒。"""

    def run(self):
        try:
            from .rag import DocStore, init_retriever, generate_pyqgis_docs
            store = DocStore()
            init_retriever(store)
            stats = store.get_stats()
            if stats.get("api_docs", 0) == 0:
                generate_pyqgis_docs(store)
        except Exception:
            logger.debug("RAG 后台建索引异常（已忽略，不影响使用）", exc_info=True)


class _TestConnectionWorker(QThread):
    """D14 后台测试模型 API 连通性，避免界面假死。

    finished(success: bool, message: str)
    """

    finished = pyqtSignal(bool, str)

    def __init__(self, provider, model, api_key, endpoint, timeout=20):
        super().__init__()
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout

    def run(self):
        try:
            from .llm_providers import get_llm_instance
            from langchain_core.messages import HumanMessage
            llm = get_llm_instance(
                self.provider, self.model, self.api_key, self.endpoint,
                temperature=0, timeout=self.timeout,
            )
            resp = llm.invoke([HumanMessage(content="请只回复字符 OK")])
            text = getattr(resp, "content", str(resp))
            if isinstance(text, list):
                text = " ".join(str(p.get("text", p)) for p in text)
            self.finished.emit(True, f"连接成功：{str(text)[:80]}")
        except Exception as _e:
            self.finished.emit(False, str(_e)[:300])


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

        # 「跳过确认」开关：仅本次会话（进程内）有效，不做持久化，
        # 重启 QGIS 后自动复位为 False，避免一次性勾选变成永久无确认的代码执行
        self._skip_confirm = False

        # ── 前台优化 S3 运行时状态 ──
        # P1-4 代码确认三档授权：持久化「总是允许」字典（启动时从 QSettings 载入）
        self._code_confirm_always = {}
        # P1-4 本次会话允许的工具集合（进程内有效，重启复位）
        self._session_allowed_tools = set()
        # P1-7 后台 RAG 建索引线程句柄
        self._rag_build_thread = None

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
        # 关闭 dock 时中断可能在跑的后台对话线程，避免线程继续占用资源
        try:
            if self.live_conversation is not None and self.live_conversation.processor is not None:
                self.live_conversation.processor.shutdown()
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        try:
            self.dockwidget.closingPlugin.disconnect(self.onClosePlugin)
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        self.plugin_is_active = False
        if self.dataloader:
            try:
                self.dataloader.close()
            except Exception as _e:
                logger.debug("ignored exception", exc_info=True)

    def unload(self):
        # 1) 先中断所有后台 LLM 请求 / 工作线程，避免 QGIS 关闭界面一直转圈卡死
        try:
            from .processor import shutdown_all_processors
            shutdown_all_processors()
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        try:
            if self.live_conversation is not None and self.live_conversation.processor is not None:
                self.live_conversation.processor.shutdown()
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        # 2) 停掉任何可能存活的定时器
        try:
            if self.console_tracker is not None:
                self.console_tracker.stop()
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        # 3) 关闭 dock 并断开信号（不 delete，交给 QGIS 自行回收，避免向已销毁对象发信号崩溃）
        try:
            if self.dockwidget is not None:
                self.iface.removeDockWidget(self.dockwidget)
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        # 4) 关闭数据库连接
        try:
            if self.dataloader is not None:
                self.dataloader.close()
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)
        # 5) 清理菜单与工具栏（必须放在最后，且全程 try，unload 抛异常会让 QGIS 关闭卡死）
        for action in list(self.actions):
            try:
                self.iface.removePluginMenu(self.tr("&QGIS Agent"), action)
                self.iface.removeToolBarIcon(action)
            except Exception as _e:
                logger.debug("ignored exception", exc_info=True)
        try:
            del self.toolbar
        except Exception as _e:
            logger.debug("ignored exception", exc_info=True)

    def run(self):
        if not _HAS_LLM_LIBS:
            msg = QMessageBox()
            msg.setWindowTitle("缺少依赖")
            msg.setText("QGIS Agent 需要安装以下 Python 库：")
            detail = "\n".join(f"• {m}" for m in required_modules)
            msg.setInformativeText(detail + "\n\n是否尝试自动安装？")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            if msg.exec() == QMessageBox.StandardButton.Yes:
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
        from .qgis_agent_dockwidget_v2 import QGISAgentDockWidgetV2 as QGISAgentDockWidget
        from .utils import (
            generate_unique_id, get_current_timestamp, pack, extract_code, set_font_color
        )
        from .config import DB_NAME

        # 先创建 dockwidget（后续信号连接依赖它）
        if self.dockwidget is None:
            self.dockwidget = QGISAgentDockWidget()

        # 在主线程中初始化工具调度桥接器（必须在任何工具调用前完成）
        from .qgis_tools import _init_main_thread_bridge, set_code_confirm_callback
        _init_main_thread_bridge()
        # 设置全局代码确认回调
        set_code_confirm_callback(self._on_code_confirm_sync)

        # 连接"跳过确认"开关（底部栏和配置页两个 checkbox 保持同步）
        # 该开关仅本次会话有效，不做持久化，在 tooltip 中向用户说明
        _skip_confirm_tip = (
            "跳过代码执行前的确认弹窗。\n"
            "仅本次会话有效，重启 QGIS 后自动恢复为需要确认。"
        )
        self.dockwidget.cbSkipConfirm.setToolTip(_skip_confirm_tip)
        self.dockwidget.cbSkipConfirmSettings.setToolTip(_skip_confirm_tip)
        self.dockwidget.cbSkipConfirm.stateChanged.connect(self._on_skip_confirm_changed)
        self.dockwidget.cbSkipConfirmSettings.stateChanged.connect(self._on_skip_confirm_changed)
        # 互相联动
        self.dockwidget.cbSkipConfirm.stateChanged.connect(
            lambda state: self.dockwidget.cbSkipConfirmSettings.setChecked(state == Qt.CheckState.Checked)
        )
        self.dockwidget.cbSkipConfirmSettings.stateChanged.connect(
            lambda state: self.dockwidget.cbSkipConfirm.setChecked(state == Qt.CheckState.Checked)
        )

        # ── 恢复保存的设置 ──
        self._load_saved_settings()

        self.dockwidget.closingPlugin.connect(self.onClosePlugin)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dockwidget)
        self.dockwidget.show()

        # ── D4 首次启动引导（仅首次弹出，之后持久化 firstRunDone） ──
        self._maybe_show_first_run_guide()

        # ── P1-7 RAG 建索引：移到后台线程，首启不再阻塞界面（约 10-30s） ──
        self._init_rag_index_async()

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
            # 没有活动对话时，自动用当前选中模型创建对话，避免发送时反复弹出"新建对话"对话框
            try:
                llm_id = self._get_selected_llm_id()
                if not llm_id:
                    QMessageBox.warning(
                        None, "无可用模型",
                        "请先在「模型配置」标签页中添加 LLM 模型，或使用「+ 新建对话」指定模型。",
                    )
                    self.dockwidget.twTabs.setCurrentWidget(self.dockwidget.tbSettings)
                    return
                self.live_conversation_id = self._generate_unique_id()
                created = self._get_current_timestamp()
                title = message.strip().replace("\n", " ")[:20] or "新对话"
                meta_info = self._pack(
                    (self.live_conversation_id, llm_id, title, "", created, created, 0, 0, "local"),
                    "conversation",
                )
                self.dataloader.create_conversation(meta_info)
                self.live_conversation = self._Conversation(self.live_conversation_id, self.dataloader)
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
            except Exception as e:
                # 建对话失败（如 RAG/模型组件构造异常）不要再静默吞掉，直接在聊天框红字提示
                self.dockwidget.txHistory.append(
                    f"<p style='color:red'>错误: 创建对话失败: {html_module.escape(str(e))}</p>"
                )
                return

        self.dockwidget.ptMessage.clear()

        # 如果用户切换了模型选择器中的模型，更新对话的 llmID
        selected_llm = self._get_selected_llm_id()
        temperature = self._get_temperature()
        llm_changed = selected_llm and selected_llm != self.live_conversation.llmID
        temp_changed = temperature != getattr(self.live_conversation.processor, 'temperature', 0.0)
        need_recreate = llm_changed or temp_changed
        if need_recreate:
            if selected_llm:
                self.live_conversation.meta_info["llmID"] = selected_llm
                self.live_conversation.provider, self.live_conversation.model_name = \
                    self.dataloader.get_llm_info(selected_llm)
            else:
                selected_llm = self.live_conversation.llmID
            # 重新创建 processor 以使用新模型/温度
            # 先关闭旧 processor：它的 QThreadPool 与在跑的 worker 无人接管，
            # 不 shutdown 会泄漏线程池，且上一轮未结束时可能拖垮 QGIS
            old_processor = getattr(self.live_conversation, "processor", None)
            if old_processor is not None:
                try:
                    old_processor.shutdown()
                except Exception as _e:
                    logger.debug("关闭旧 Processor 失败，继续重建: %s", _e, exc_info=True)
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
            font_color = self._set_font_color(self.dockwidget.txHistory.palette().color(QPalette.ColorRole.Base))
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

            # 统一成对连接/断开，避免多次发送后信号重复触发与旧对象泄漏
            self._connect_conv_signals(self.live_conversation)
            self.live_conversation.update_user_prompt(message, response_type)

            # 切换为发送状态：隐藏发送按钮，显示停止按钮
            self.dockwidget.set_sending_state(True)
            self.dockwidget.disableAllButtons()
            self.dockwidget.disableAllTextEdit()

    def _connect_conv_signals(self, conv):
        """连接一个会话的全部信号（与 _disconnect_conv_signals 成对调用）。

        每次发送时都会连接，因此必须在完成/出错/中断时逐个断开，
        否则第 N 次发送会让槽函数被触发 N 次，并且旧会话对象会被信号持有而无法回收。
        """
        if conv is None:
            return
        conv.llm_response.connect(self._on_response_received)
        conv.llm_thinking.connect(self._on_thinking)
        conv.llm_tool_status.connect(self._on_tool_status)
        conv.llm_workflow_update.connect(self._on_workflow_update)
        conv.llm_code_update.connect(self._on_code_update)
        conv.llm_execution_log.connect(self._on_execution_log)
        conv.llm_interrupted.connect(self._on_response_error)

    def _disconnect_conv_signals(self, conv):
        """断开 _connect_conv_signals 连接的全部信号（已断开时安全跳过）。"""
        if conv is None:
            return
        pairs = (
            ("llm_response", self._on_response_received),
            ("llm_thinking", self._on_thinking),
            ("llm_tool_status", self._on_tool_status),
            ("llm_workflow_update", self._on_workflow_update),
            ("llm_code_update", self._on_code_update),
            ("llm_execution_log", self._on_execution_log),
            ("llm_interrupted", self._on_response_error),
        )
        for signal_name, slot in pairs:
            signal = getattr(conv, signal_name, None)
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError) as _e:
                # 已断开或槽未连接时 Qt 会抛 RuntimeError/TypeError，忽略即可
                logger.debug("断开会话信号 %s 时已无连接: %s", signal_name, _e, exc_info=True)

    def _on_response_received(self, response, workflow, model_path):
        if self.live_conversation is not None:
            self.dockwidget.set_sending_state(False)
            self._reset_send_controls()
            self.dockwidget.enableAllButtons()
            self.dockwidget.enableAllTextEdit()
            self._disconnect_conv_signals(self.live_conversation)

            # 清除流式标记
            self.dockwidget.finalizeThinking()

            # 确保数据库连接有效（工作线程可能会重置 connection）
            if self.dataloader.connection is None:
                try:
                    self.dataloader.connect()
                except Exception as _e:
                    logger.debug("ignored exception", exc_info=True)

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

        self.dockwidget.append_execution_log("▶ 开始执行代码...")
        self.dockwidget.hide_debug_analysis()

        # 在工作线程中执行代码
        from .qgis_tools import execute_pyqgis
        result = execute_pyqgis(code)

        if result.get("executed"):
            self.dockwidget.append_execution_log("✅ 代码执行成功")
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
            self.dockwidget.append_execution_log("📂 已加载代码文件")

    def _on_copy_code(self):
        """复制代码到剪贴板"""
        if self.dockwidget.copy_code_to_clipboard():
            self.dockwidget.append_execution_log("📋 代码已复制到剪贴板")

    def _on_save_code(self):
        """保存代码到文件"""
        file_path = self.dockwidget.save_code_to_file()
        if file_path:
            self.dockwidget.append_execution_log(f"💾 代码已保存到: {file_path}")

    def _on_clear_code(self):
        """清空代码编辑器"""
        self.dockwidget.clear_code_editor()
        self.dockwidget.append_execution_log("🗑️ 已清空代码编辑器")

    def _on_response_error(self, error_message):
        self.dockwidget.set_sending_state(False)
        self._reset_send_controls()
        self.dockwidget.enableAllButtons()
        self.dockwidget.enableAllTextEdit()
        # 与 _connect_conv_signals 成对断开，补齐 workflow/code/log 三个信号
        self._disconnect_conv_signals(self.live_conversation)
        self.dockwidget.finalizeThinking()

        err_text = str(error_message)

        # 尝试用 error_classifier 做错误分级；模块缺失或异常时降级为原始展示
        info = None
        try:
            from .error_classifier import classify_error
            info = classify_error(err_text)
        except Exception as _e:
            logger.debug("错误分级不可用，回退为原始错误信息展示: %s", _e, exc_info=True)

        if isinstance(info, dict) and info.get("title"):
            title = str(info.get("title"))
            message = str(info.get("message") or err_text)
            hint = str(info.get("hint") or "")
            category = str(info.get("category") or "unknown")
            retryable = bool(info.get("retryable"))
            action = info.get("action")
            if action == "open_settings":
                extra = "请到「模型配置」标签页检查模型、API 端点与 API Key 是否正确。"
                hint = f"{hint} {extra}".strip() if hint else extra
            if retryable:
                hint = f"{hint}（可直接重试）".strip() if hint else "可直接重试"
        else:
            # 降级：保持原有的原始错误展示
            category = "unknown"
            title = "出错了"
            message = err_text
            hint = ""

        html_parts = [
            f"<p style='color:red;'><b>{html_module.escape(title)}</b></p>",
            f"<p style='margin:0;'>{html_module.escape(message)}</p>",
        ]
        if hint:
            html_parts.append(
                f"<p style='margin:0;color:#666;'>建议：{html_module.escape(hint)}</p>"
            )
        # 技术细节（原始堆栈）不直接铺在对话里，写入执行日志面板供排查
        html_parts.append(
            "<p style='margin:0;color:#888;font-size:11px;'>"
            f"类型：{html_module.escape(category)}，技术细节已写入「报告」页签的执行日志。</p>"
        )
        self.dockwidget.txHistory.append("".join(html_parts))

        try:
            self.dockwidget.append_execution_log(f"❌ {title}\n{err_text}")
        except Exception as _e:
            logger.debug("写入执行日志失败，忽略: %s", _e, exc_info=True)

    def _ask_code_confirm(self, tool_name, code_preview):
        """统一的代码执行确认入口（P1-4 三档授权）。

        返回 True=允许执行，False=取消。

        优先级：持久化「总是允许」> 本次会话允许 > 弹窗询问。
        """
        # 总是允许（持久化到 QSettings，跨重启有效）
        if self._code_confirm_always.get(tool_name):
            return True
        # 本次会话允许该工具（进程内有效，重启复位）
        if tool_name in self._session_allowed_tools:
            return True

        dlg = CodeConfirmDialog(self.dockwidget, tool_name, code_preview)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            decision = dlg.decision
            if decision == "always":
                self._code_confirm_always[tool_name] = True
                try:
                    settings = QSettings("QGIS", "QGISAgent")
                    allowed = settings.value("codeConfirmAlways", [])
                    if not isinstance(allowed, list):
                        allowed = []
                    if tool_name not in allowed:
                        allowed.append(tool_name)
                        settings.setValue("codeConfirmAlways", allowed)
                except Exception as _e:
                    logger.debug("持久化「总是允许」失败，本会话仍生效: %s", _e, exc_info=True)
            elif decision == "session":
                self._session_allowed_tools.add(tool_name)
            return True
        return False

    def _on_code_confirm(self, tool_name, code_preview, callback):
        """异步代码执行确认（processor 调用，带回调）。

        在 execute_pyqgis 或 execute_processing 执行前弹出
        CodeConfirmDialog（代码默认展开 + 三档授权），让用户确认或取消。
        """
        try:
            result = self._ask_code_confirm(tool_name, code_preview)
        except Exception as _e:
            logger.debug("代码确认异常，默认拒绝执行: %s", _e, exc_info=True)
            result = False
        try:
            callback(result)
        except Exception:
            pass

    def _on_code_confirm_sync(self, tool_name, code_preview):
        """同步版本的代码确认（用于全局回调，返回 bool）。"""
        try:
            return self._ask_code_confirm(tool_name, code_preview)
        except Exception as _e:
            logger.debug("代码确认异常，默认拒绝执行: %s", _e, exc_info=True)
            return False

    def _on_skip_confirm_changed(self, state):
        """当"跳过确认"checkbox 状态变化时更新全局开关。

        注意：该开关只保存在进程内的实例属性 self._skip_confirm 中，
        不写入 QSettings，重启 QGIS 后自动恢复为"需要确认"，
        避免一次误勾选导致长期静默执行任意代码。
        """
        from .qgis_tools import set_skip_all_confirms
        self._skip_confirm = (state == Qt.CheckState.Checked)
        set_skip_all_confirms(self._skip_confirm)

    def _load_saved_settings(self):
        """加载保存的设置（不含"跳过确认"，该项故意不持久化）"""
        settings = QSettings("QGIS", "QGISAgent")

        # 恢复温度设置
        temperature = settings.value("temperature", 0, type=int)
        self.dockwidget.sliderTemperature.setValue(temperature)

        # P1-4：恢复「总是允许」持久化设置（跨重启长期免确认）
        allowed = settings.value("codeConfirmAlways", [])
        if isinstance(allowed, list):
            self._code_confirm_always = {t: True for t in allowed if isinstance(t, str)}

    def _on_temperature_changed(self, value):
        """温度滑块变化时保存设置"""
        settings = QSettings("QGIS", "QGISAgent")
        settings.setValue("temperature", value)

    def _init_rag_index_async(self):
        """P1-7：首启 RAG 建索引挪到后台线程，避免界面假死 10-30 秒。

        旧实现在 UI 线程同步构建，且会先弹「是否构建」对话框，
        导致首启时 dock 要在建完索引后才显示（体验＝卡死）。
        现在：dock 先显示，索引在后台线程静默构建，状态条给出进度。
        """
        try:
            # 快速探测索引是否为空（只读，主线程，毫秒级）
            from .rag import DocStore, init_retriever
            store = DocStore()
            init_retriever(store)
            empty = store.get_stats().get("api_docs", 0) == 0
        except Exception:
            empty = False
            logger.debug("RAG 索引探测失败，跳过后台构建", exc_info=True)

        if not empty:
            return

        _set_status = getattr(self.dockwidget, "_set_status", None)
        if callable(_set_status):
            _set_status("🔧 正在后台构建 API 索引…")

        if getattr(self, "_rag_build_thread", None) and self._rag_build_thread.isRunning():
            return
        self._rag_build_thread = _RagBuildWorker()
        self._rag_build_thread.finished.connect(self._on_rag_build_done)
        self._rag_build_thread.start()

    def _on_rag_build_done(self):
        """RAG 后台构建完成后的轻量反馈（不弹窗，避免打扰）。"""
        try:
            _set_status = getattr(self.dockwidget, "_set_status", None)
            if callable(_set_status):
                _set_status("✅ API 索引就绪")
        except Exception:
            pass

    def _maybe_show_first_run_guide(self):
        """D4：首次启动引导。仅在首次弹出一次，之后持久化 firstRunDone。"""
        try:
            settings = QSettings("QGIS", "QGISAgent")
            if settings.value("firstRunDone", False, type=bool):
                return
            QMessageBox.information(
                self.dockwidget,
                "欢迎使用 QGIS Agent",
                "这是一款在 QGIS 内运行的 AI 助手插件。\n\n"
                "• 在底部输入框直接描述 GIS 任务（如「把图层重投影到 WGS84」）；\n"
                "• 执行 PyQGIS 代码前会弹出确认框，可勾选「总是允许」免重复确认；\n"
                "• 首次会自动在后台构建 PyQGIS API 索引（约 10-30 秒，不卡界面）；\n"
                "• 发送中可随时点「停止」，停止后该对话仍可继续。\n\n"
                "更多用法见帮助页（聊天框右上角「?」）。",
            )
            settings.setValue("firstRunDone", True)
        except Exception as _e:
            logger.debug("首启引导失败，已忽略: %s", _e, exc_info=True)

    def _on_stop_requested(self):
        """用户点击停止按钮 — U10 停止中间态。

        点击停止后先进入「停止中…」中间态（禁用停止按钮、给出反馈），
        直至 worker 真正结束（_on_response_received / _on_response_error）
        才恢复界面，避免旧实现「点停止后该对话永久报废」的误导。

        若当前并无在途调用，则直接恢复界面，不进入中间态。
        """
        if self.live_conversation:
            self.live_conversation.stop()

        if getattr(self.live_conversation, "llm_finished", True):
            # 没有在途调用：直接恢复界面
            self.dockwidget.set_sending_state(False)
            self.dockwidget.enableAllButtons()
            self.dockwidget.enableAllTextEdit()
            self._reset_send_controls()
            self.dockwidget.txHistory.append(
                "<p style='color:#888;'>⏹ 没有正在进行的生成</p>"
            )
            return

        # 进入「停止中」中间态
        try:
            self.dockwidget.pbStop.setText("停止中…")
            self.dockwidget.pbStop.setEnabled(False)
            _set_status = getattr(self.dockwidget, "_set_status", None)
            if callable(_set_status):
                _set_status("⏹ 正在停止…")
        except Exception:
            pass
        self.dockwidget.txHistory.append(
            "<p style='color:#888;'>⏹ 已发送停止请求</p>"
        )

    def _reset_send_controls(self):
        """U10：恢复发送/停止按钮到初始可用状态（停止按钮文案复位为「停止」）。"""
        try:
            self.dockwidget.pbStop.setText("停止")
            self.dockwidget.pbStop.setEnabled(True)
        except Exception:
            pass

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
            if self.edit_dialog.exec() == QDialog.DialogCode.Accepted:
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
        # 删除不可恢复，先做二次确认，避免误点永久丢失整个会话
        reply = QMessageBox.question(
            self.dockwidget,
            "删除会话",
            "确定要删除这个会话吗？\n\n"
            "会话中的全部消息与代码记录将被永久移除，删除后不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.dataloader.delete_conversation(conversation_id)
        if self.live_conversation_id == conversation_id:
            # 丢弃当前会话前先断开信号，避免旧对象被信号持有导致内存泄漏
            self._disconnect_conv_signals(self.live_conversation)
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

            if self.edit_dialog.exec() == QDialog.DialogCode.Accepted:
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
            kw = keyword.lower()
            return kw in meta_info["title"].lower() or kw in meta_info["description"].lower()

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

        # D14：测试连接按钮（点击后后台线程执行，不阻塞界面）
        self.btnTestConnection = QPushButton("🔌 测试连接")
        self.btnTestConnection.setToolTip("用当前选中的模型测试 API 连通性（后台线程执行，不阻塞界面）")
        self.btnTestConnection.clicked.connect(self._on_test_connection)
        self.dockwidget.settingsLayout.addWidget(self.btnTestConnection)

    def _on_test_connection(self):
        """D14：后台测试当前选中模型的 API 连通性，避免界面假死。"""
        llm_id = self._get_selected_llm_id()
        if not llm_id:
            QMessageBox.warning(
                None, "无可用模型", "请先在「模型配置」标签页添加并选中一个模型。"
            )
            return
        try:
            provider, model_name = self.dataloader.get_llm_info(llm_id)
            name, endpoint, api_key = self.dataloader.fetch_llm_info(llm_id)
        except Exception as _e:
            QMessageBox.warning(None, "读取模型失败", f"无法读取模型配置：{_e}")
            return

        btn = getattr(self, "btnTestConnection", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("测试中…")
        _set_status = getattr(self.dockwidget, "_set_status", None)
        if callable(_set_status):
            _set_status("🔌 正在测试连接…")

        worker = _TestConnectionWorker(provider, model_name, api_key, endpoint, timeout=20)
        self._test_conn_worker = worker  # 保持引用，避免被 GC 回收

        def _on_done(success, message):
            try:
                if btn is not None:
                    btn.setEnabled(True)
                    btn.setText("🔌 测试连接")
                if callable(_set_status):
                    _set_status("✅ 连接成功" if success else "⚠ 连接失败")
                if success:
                    QMessageBox.information(self.dockwidget, "连接测试", message)
                else:
                    QMessageBox.warning(self.dockwidget, "连接测试失败", message)
            except Exception as _e:
                logger.debug("测试连接回调异常: %s", _e, exc_info=True)

        worker.finished.connect(_on_done)
        worker.start()

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
        key_widget.setEchoMode(QLineEdit.EchoMode.Password)
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
        if ref_dlg.exec() == QDialog.DialogCode.Accepted:
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
        line.setFrameShape(QFrame.Shape.HLine)
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
        self.ptApiKey.setEchoMode(QLineEdit.EchoMode.Password)
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
