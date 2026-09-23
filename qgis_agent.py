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
    QComboBox, QTableWidgetItem, QFrame, QToolBar, QInputDialog,
    QCheckBox, QGroupBox, QSpinBox
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
    """尝试导入，失败即视为「该依赖不可用」。

    不能只捕获 ImportError：依赖装了一半时抛出的往往不是 ImportError，
    例如 pydantic 与 pydantic-core 版本不匹配会抛 SystemError，
    macOS 上框架/动态库加载失败会抛 OSError。
    这类异常一旦从模块顶层逃逸，整个插件会加载失败且界面没有任何提示，
    因此这里统一兜住，转由 run() 去弹「缺少依赖」对话框。
    """
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
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
    • 「代码审查」区由 code_reviewer 在后台线程产出后异步回填（apply_review），
      仅作展示与提示，不改变上述三档授权逻辑。
    """

    def __init__(self, parent, tool_name, code_preview):
        super().__init__(parent)
        self.decision = None  # "once" | "session" | "always" | None(取消)
        self._review_worker = None  # 由 _start_code_review 挂上，便于收尾时断开
        self.setWindowTitle("代码执行确认")
        self.setMinimumSize(580, 520)
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

        # 代码审查区：先占位，审查线程返回后由 apply_review 回填
        self.reviewStatus = QLabel("🔍 代码审查：正在后台审查…")
        self.reviewStatus.setWordWrap(True)
        self.reviewStatus.setStyleSheet("QLabel { color:#666; }")
        layout.addWidget(self.reviewStatus)

        self.reviewView = QPlainTextEdit()
        self.reviewView.setReadOnly(True)
        self.reviewView.setPlainText("审查进行中，可直接决定是否执行，无需等待。")
        self.reviewView.setMaximumHeight(130)
        layout.addWidget(self.reviewView)

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

    def apply_review(self, review, summary):
        """异步回填代码审查结果（在 UI 线程由信号触发）。

        review: code_reviewer.review_code() 的返回字典；summary: 人类可读摘要。
        任何字段缺失都按「审查不可用」降级展示，绝不影响授权按钮。
        """
        try:
            review = review if isinstance(review, dict) else {}
            issues = [str(i) for i in (review.get("issues") or [])]
            suggestions = [str(s) for s in (review.get("suggestions") or [])]

            if not review:
                self.reviewStatus.setText("🔍 代码审查：不可用（已跳过）")
                self.reviewStatus.setStyleSheet("QLabel { color:#888; }")
            elif issues:
                self.reviewStatus.setText(
                    f"❌ 代码审查：发现 {len(issues)} 个问题、{len(suggestions)} 条建议，请谨慎执行"
                )
                self.reviewStatus.setStyleSheet("QLabel { color:#c0392b; font-weight:bold; }")
            else:
                self.reviewStatus.setText(
                    f"✅ 代码审查：未发现问题（{len(suggestions)} 条建议）"
                )
                self.reviewStatus.setStyleSheet("QLabel { color:#27793f; }")

            lines = []
            if issues:
                lines.append("问题：")
                lines.extend(f"  - {i}" for i in issues)
            if suggestions:
                lines.append("建议：")
                lines.extend(f"  - {s}" for s in suggestions)
            if summary:
                lines.append("")
                lines.append("审查摘要：")
                lines.append(str(summary))
            self.reviewView.setPlainText("\n".join(lines) or "审查未返回内容。")
        except Exception:
            logger.debug("回填代码审查结果失败（不影响确认流程）", exc_info=True)

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
            from .llm_providers import get_llm_instance, resolve_browser_tls
            from langchain_core.messages import HumanMessage
            requested = bool(QSettings("QGIS", "QGISAgent").value("use_browser_tls", False))
            # 依赖缺失时降级为标准 TLS 栈：连接测试要给出「能不能用」的结论，
            # 而不是被一个可选依赖卡死在弹窗上。
            effective, tls_reason = resolve_browser_tls(requested)
            llm = get_llm_instance(
                self.provider, self.model, self.api_key, self.endpoint,
                temperature=0, timeout=self.timeout, browser_tls=effective,
            )
            resp = llm.invoke([HumanMessage(content="请只回复字符 OK")])
            text = getattr(resp, "content", str(resp))
            if isinstance(text, list):
                text = " ".join(str(p.get("text", p)) for p in text)
            note = ""
            if tls_reason:
                note = ("\n\n注：「浏览器兼容 TLS」已勾选但未生效（%s），本次使用标准 TLS 栈。"
                        "仅当接口连接被网关重置时才需要它，可在 QGIS 自带 Python 中执行 "
                        "pip install curl_cffi 后重开本页。" % tls_reason)
            self.finished.emit(True, f"连接成功：{str(text)[:80]}{note}")
        except Exception as _e:
            self.finished.emit(False, str(_e)[:300])


class _CodeReviewWorker(QThread):
    """后台跑 code_reviewer.CodeReviewer，把审查结果异步回填到确认对话框。

    审查会调 LLM.invoke（10-30 秒量级），放在 UI 线程会把确认框卡死，
    因此这里统一走 QThread：结果通过 reviewFinished 发回主线程。
    llm 为 None 或 LLM 审查异常时降级为 CodeReviewer(None) 的规则兜底。

    reviewFinished(review: dict, summary: str)
    """

    reviewFinished = pyqtSignal(dict, str)

    def __init__(self, llm, code, tool_name, tool_id, user_query):
        super().__init__()
        self.llm = llm
        self.code = code
        self.tool_name = tool_name
        self.tool_id = tool_id
        self.user_query = user_query

    def _review_with(self, llm):
        from .code_reviewer import CodeReviewer
        reviewer = CodeReviewer(llm)
        review = reviewer.review_code(
            self.code, self.tool_name, self.tool_id, self.user_query
        )
        return review, reviewer.get_review_summary(review)

    def run(self):
        try:
            review, summary = self._review_with(self.llm)
        except Exception:
            logger.debug("LLM 代码审查失败，回退规则兜底审查", exc_info=True)
            try:
                review, summary = self._review_with(None)
            except Exception:
                logger.debug("规则兜底审查也失败，跳过代码审查展示", exc_info=True)
                review, summary = {}, ""
        self.reviewFinished.emit(review if isinstance(review, dict) else {}, str(summary or ""))


class QGISAgent:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        # locale/userLocale 在极端情况下可能为 None（例如 QGIS 尚未注册 locale、
        # 或插件被非标准方式实例化），此时直接切片会 TypeError 导致整个插件加载失败。
        locale = str(QSettings().value("locale/userLocale") or "en")[0:2]
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
        # 代码审查后台线程句柄（可能同时有多个确认框，逐个保活直到跑完）
        self._code_review_threads = []
        # 最近一次用户提问，供代码审查提供上下文
        self._last_user_query = ""

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
            logger.debug("Could not find Python console toolbar: %s", e)

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
        # 2b) 等在跑的代码审查线程收尾，避免 QThread 被销毁时仍在运行导致退出崩溃
        for _t in list(getattr(self, "_code_review_threads", [])):
            try:
                if _t.isRunning():
                    _t.wait(3000)
            except Exception as _e:
                logger.debug("等待代码审查线程结束失败，忽略: %s", _e, exc_info=True)
        # 2c) 停掉 MCP 桥接服务，释放 127.0.0.1 监听端口并清掉会话文件
        try:
            from .mcp_bridge import MCPBridge
            MCPBridge.get().stop()
        except Exception as _e:
            logger.debug("停止 MCP 服务失败，忽略: %s", _e, exc_info=True)
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
            # 依赖不可用时必须给出**可见且准确**的反馈。历史上此处的异常会
            # 直接逃逸出 run()，表现为「插件启用后毫无反应，只有一条日志
            # Traceback」——用户根本不知道发生了什么。
            try:
                self._handle_missing_dependencies()
            except Exception:
                logger.warning("依赖提示流程异常", exc_info=True)
                QMessageBox.warning(
                    None, "依赖不可用",
                    "QGIS Agent 的 Python 依赖无法正常加载。\n\n"
                    "请打开「QGIS Agent」面板的日志或 QGIS 的 Python 控制台查看详细信息。")
            return

        if not self.plugin_is_active:
            self.plugin_is_active = True
            self._init_plugin()

    def _handle_missing_dependencies(self):
        """依赖不可用时的交互流程：区分「没装」与「装了但坏了」。

        「装了但坏了」（版本错配、动态库加载失败等）不提供自动安装：
        重装上层包通常救不回来，反而会把用户的 Python 环境改得更乱。
        此时只给出诊断信息与排查方向。
        """
        package_manager.check_dependencies()

        # 情况一：模块找得到，但一导入就报错。
        if package_manager.broken:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("依赖已安装但无法加载")
            msg.setText("以下 Python 库可以找到，但导入时报错，插件无法继续启动：")
            msg.setInformativeText(
                package_manager.broken_report() + "\n\n" + package_manager.hint_text())
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
            return

        # 情况二：确实没有 → 询问是否自动安装。
        if not package_manager.missing:
            QMessageBox.information(None, "已就绪", "依赖已安装，请重启 QGIS。")
            return

        msg = QMessageBox()
        msg.setWindowTitle("缺少依赖")
        msg.setText("QGIS Agent 需要安装以下 Python 库：")
        detail = "\n".join(f"• {m}" for m in package_manager.missing)
        msg.setInformativeText(detail + "\n\n是否尝试自动安装？")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            ok = package_manager.install_missing()
            if ok:
                QMessageBox.information(None, "安装成功", "依赖安装完成，请重启 QGIS 后重新启用插件。")
            else:
                QMessageBox.warning(None, "安装失败",
                                    "自动安装失败，请在 OSGeo4W Shell 中手动运行：\n\n"
                                    f"pip install {' '.join(required_modules)}")

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

        # 记录本轮提问，供代码审查作为上下文
        self._last_user_query = message

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
        self._connect_clarification_signal(conv)

    def _connect_clarification_signal(self, conv):
        """连接主动澄清追问信号（每个会话对象只连一次）。

        clarificationRequested 由 conversation 在用户请求模糊时 emit；
        它跨多轮发送持续有效，因此不随 _disconnect_conv_signals 断开，
        用 _clarification_wired 标记避免重复连接导致弹多个追问框。
        """
        if conv is None or getattr(conv, "_clarification_wired", False):
            return
        signal = getattr(conv, "clarificationRequested", None)
        if signal is None:
            return
        try:
            signal.connect(self._on_clarification_requested)
            conv._clarification_wired = True
        except Exception:
            logger.debug("连接 clarificationRequested 失败，澄清追问不可用", exc_info=True)

    def _on_clarification_requested(self, question):
        """用户请求模糊时弹出追问输入框，并把补充信息回传给会话。"""
        try:
            text, ok = QInputDialog.getText(
                self.dockwidget, "需要补充信息", str(question)
            )
        except Exception:
            logger.debug("弹出澄清追问输入框失败，跳过本次追问", exc_info=True)
            return

        if not ok or not text or not text.strip():
            return

        conv = getattr(self, "live_conversation", None)
        provide = getattr(conv, "provide_clarification", None)
        if not callable(provide):
            logger.debug("会话未实现 provide_clarification，丢弃澄清回答")
            return
        try:
            provide(text.strip())
        except Exception:
            logger.debug("回传澄清回答失败", exc_info=True)

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
        # 弹窗前启动代码审查：审查在后台线程跑，结果异步回填，不阻塞对话框显示
        self._start_code_review(dlg, tool_name, code_preview)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        self._detach_code_review(dlg)
        if accepted:
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

    def _start_code_review(self, dlg, tool_name, code_preview):
        """在后台线程对待执行代码做安全审查，结果回填到确认对话框。

        取当前会话的 llm（拿不到就传 None，由 CodeReviewer 走规则兜底）；
        审查全程不阻塞 UI：对话框先显示，审查完成后才更新「代码审查」区。
        """
        llm = None
        try:
            conv = getattr(self, 'live_conversation', None)
            proc = getattr(conv, 'processor', None)
            llm = getattr(proc, 'llm', None)
        except Exception:
            logger.debug("获取会话 llm 失败，代码审查改用规则兜底", exc_info=True)

        try:
            # 清掉已跑完的线程句柄，避免列表无限增长
            self._code_review_threads = [
                t for t in self._code_review_threads if t.isRunning()
            ]
            worker = _CodeReviewWorker(
                llm, code_preview or "", tool_name, tool_name,
                getattr(self, "_last_user_query", "") or "",
            )
            worker.reviewFinished.connect(dlg.apply_review)
            dlg._review_worker = worker
            self._code_review_threads.append(worker)
            worker.start()
        except Exception:
            logger.debug("启动代码审查线程失败，跳过审查展示", exc_info=True)
            try:
                dlg.apply_review({}, "")
            except Exception:
                logger.debug("代码审查占位区更新失败，忽略", exc_info=True)

    def _detach_code_review(self, dlg):
        """对话框关闭后断开审查回填，避免向即将销毁的对话框发信号。"""
        worker = getattr(dlg, "_review_worker", None)
        if worker is None:
            return
        try:
            worker.reviewFinished.disconnect(dlg.apply_review)
        except (RuntimeError, TypeError) as _e:
            logger.debug("断开代码审查信号时已无连接: %s", _e, exc_info=True)
        dlg._review_worker = None

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

        self._build_browser_tls_ui()
        self._build_mcp_settings_ui()

        # 切回模型配置页时刷新一次 MCP 状态，避免显示过期信息
        try:
            self.dockwidget.twTabs.currentChanged.connect(
                lambda _idx: self._refresh_mcp_status()
            )
        except Exception as _e:
            logger.debug("连接标签页切换信号失败，忽略: %s", _e, exc_info=True)

        # 若上次勾选了自动启动，则随插件一起把 MCP 服务拉起来
        self._maybe_autostart_mcp()

    # ──────────────────────────────────────────────
    # 浏览器指纹 TLS 开关（v2.3.2 引入，此前只在无人调用的 SettingsDialog 里）
    # ──────────────────────────────────────────────
    def _build_browser_tls_ui(self):
        """把「浏览器兼容 TLS」开关放进真正可见的模型配置页。

        历史坑：该开关原本只存在于 settings_dialog.py，而那个对话框已无任何调用方，
        用户根本点不到 —— 等于功能没上线。
        """
        try:
            from .llm_providers import browser_tls_available
            self._browser_tls_ready = bool(browser_tls_available())
        except Exception:  # noqa: BLE001
            self._browser_tls_ready = False

        self.cbBrowserTls = QCheckBox(
            "使用浏览器兼容 TLS（仅当接口连接被网关重置时需要，需 pip install curl_cffi）"
        )
        self.cbBrowserTls.setToolTip(
            "部分 API 网关会依据客户端 TLS 指纹判断请求来源，非浏览器客户端可能在握手阶段被中断"
            "（典型表现：Connection reset by peer）。开启后改用 curl_cffi 的浏览器 TLS 栈，"
            "以提升这类接口的连接成功率。\n"
            "curl_cffi 是可选依赖：未安装时本选项会自动关闭，插件改用标准 TLS 栈，其它功能不受影响。"
        )
        requested = bool(QSettings("QGIS", "QGISAgent").value("use_browser_tls", False))
        # 依赖不在位时，把陈旧的「已勾选」纠正掉：设置里写着开、实际永远生效不了，
        # 比直接关掉更容易误导（旧版还会因此让每次 LLM 调用都抛异常）。
        if requested and not self._browser_tls_ready:
            requested = False
            QSettings("QGIS", "QGISAgent").setValue("use_browser_tls", False)
        self.cbBrowserTls.setChecked(requested)
        self.cbBrowserTls.stateChanged.connect(self._on_browser_tls_changed)

        layout = self.dockwidget.settingsLayout
        idx = layout.count() - 1
        layout.insertWidget(idx, self.cbBrowserTls)
        self.lblBrowserTls = QLabel()
        self.lblBrowserTls.setWordWrap(True)
        self.lblBrowserTls.setStyleSheet("color: #666; font-size: 11px;")
        layout.insertWidget(idx + 1, self.lblBrowserTls)
        self._refresh_browser_tls_hint()

    def _refresh_browser_tls_hint(self):
        """就地把 curl_cffi 的可用状态说清楚，避免用户以为勾了就已生效。"""
        label = getattr(self, "lblBrowserTls", None)
        if label is None:
            return
        if not getattr(self, "_browser_tls_ready", False):
            label.setText(
                "⚠ 未安装 curl_cffi（可选依赖），此选项暂不可用。"
                "只要接口没有出现「连接被重置」，就无需安装，不影响其它功能。"
            )
        elif self.cbBrowserTls.isChecked():
            label.setText("✔ 已启用：请求将走 curl_cffi 的浏览器 TLS 栈。")
        else:
            label.setText("curl_cffi 已就绪，需要时可开启。")

    def _on_browser_tls_changed(self, _state=None):
        checked = self.cbBrowserTls.isChecked()
        if checked and not getattr(self, "_browser_tls_ready", False):
            # 勾了却不生效比直接关掉更容易误导；旧实现还会让每次 LLM 调用都抛异常，
            # 表现是「测试连接失败」+ 对话完全不能用。
            self.cbBrowserTls.blockSignals(True)
            self.cbBrowserTls.setChecked(False)
            self.cbBrowserTls.blockSignals(False)
            QSettings("QGIS", "QGISAgent").setValue("use_browser_tls", False)
            self._refresh_browser_tls_hint()
            QMessageBox.information(
                self.dockwidget,
                "「浏览器兼容 TLS」暂不可用",
                "未检测到 curl_cffi，该选项已自动关闭。\n\n"
                "它是可选依赖，只在接口连接被网关重置（Connection reset by peer）时才需要。"
                "如需启用，请在 QGIS 自带的 Python 中执行：\n\n"
                "    pip install curl_cffi\n\n"
                "不安装不影响插件的其它功能 —— 插件会自动使用标准 TLS 栈。",
            )
            return
        QSettings("QGIS", "QGISAgent").setValue("use_browser_tls", checked)
        self._refresh_browser_tls_hint()

    # ──────────────────────────────────────────────
    # MCP 服务设置（外部 Agent 通过 MCP 驱动 QGIS）
    # ──────────────────────────────────────────────
    @staticmethod
    def _mcp_settings():
        return QSettings("QGIS", "QGISAgent")

    def _build_mcp_settings_ui(self):
        """在模型配置页底部插入「MCP 服务」设置区。"""
        settings = self._mcp_settings()
        group = QGroupBox("MCP 服务（供 Claude Desktop / Cursor 等外部 Agent 调用）")
        outer = QVBoxLayout(group)
        outer.setSpacing(6)

        try:
            from .mcp_protocol import DEFAULT_PORT, session_file_path
            default_port = DEFAULT_PORT
            session_hint = session_file_path()
        except Exception:  # noqa: BLE001
            default_port = 9876
            session_hint = "~/.qgis_agent/mcp_session.json"

        hint = QLabel(
            "启动后仅在 127.0.0.1 上监听，且强制校验访问令牌，局域网内其他机器无法连接。"
            "端口与令牌已写入 %s，外部 MCP Server 会自动读取，通常无需手工配置。"
            % session_hint
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(hint)

        self.cbMcpAutostart = QCheckBox("随插件启动时自动运行 MCP 服务")
        self.cbMcpAutostart.setChecked(bool(settings.value("mcp/autostart", False)))
        outer.addWidget(self.cbMcpAutostart)

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("监听端口"))
        self.spMcpPort = QSpinBox()
        self.spMcpPort.setRange(1024, 65535)
        try:
            self.spMcpPort.setValue(int(settings.value("mcp/port", default_port)))
        except (TypeError, ValueError):
            self.spMcpPort.setValue(default_port)
        self.spMcpPort.setToolTip("默认 9876。若被占用可换一个端口，改完需重新启动服务。")
        row_port.addWidget(self.spMcpPort)
        self.btnMcpToggle = QPushButton("启动服务")
        self.btnMcpToggle.clicked.connect(self._on_mcp_toggle)
        row_port.addWidget(self.btnMcpToggle)
        outer.addLayout(row_port)

        row_token = QHBoxLayout()
        row_token.addWidget(QLabel("访问令牌"))
        self.leMcpToken = QLineEdit()
        self.leMcpToken.setToolTip(
            "外部客户端必须携带该令牌才能调用工具。修改后需重新启动服务。\n"
            "令牌较长，输入框内显示不全（会滚动）：请用「复制令牌」按钮取值，不要手抄屏幕上的片段。"
        )
        token = str(settings.value("mcp/token", "") or "")
        if not token:
            token = self._new_mcp_token()
            settings.setValue("mcp/token", token)
        self.leMcpToken.setText(token)
        # setText 会把光标放到末尾，QLineEdit 随之滚动到尾部 —— 屏幕上只剩后半截令牌，
        # 用户手抄极易抄错（实测踩过：显示的是 64 位令牌的最后 34 位）。这里回到头部。
        self.leMcpToken.setCursorPosition(0)
        row_token.addWidget(self.leMcpToken)
        btn_regen = QPushButton("重新生成")
        btn_regen.setToolTip("生成一份新的 32 字节随机令牌（旧令牌立即失效）")
        btn_regen.clicked.connect(self._on_mcp_regenerate_token)
        row_token.addWidget(btn_regen)
        btn_copy_token = QPushButton("复制令牌")
        btn_copy_token.clicked.connect(self._on_mcp_copy_token)
        row_token.addWidget(btn_copy_token)
        outer.addLayout(row_token)

        self.cbMcpDangerous = QCheckBox(
            "允许外部 Agent 调用特权工具（执行 PyQGIS 代码 / 处理算法 / 删图层 / 运行技能）"
        )
        self.cbMcpDangerous.setChecked(bool(settings.value("mcp/allow_dangerous", False)))
        self.cbMcpDangerous.setToolTip(
            "默认关闭：这些工具既不会出现在外部 Agent 的工具清单里，直接调用也会被拒绝。\n"
            "开启后外部 Agent 可以请求它们，但每次执行仍会在 QGIS 界面上弹出确认框，由你本人点击确认。\n"
            "「运行技能」之所以归入此类，是因为技能会执行用户技能目录下的 Python 代码。\n"
            "注意：若你同时打开了插件底部的「跳过代码执行确认」，外部 Agent 的这些操作也将不再弹窗。"
        )
        outer.addWidget(self.cbMcpDangerous)

        self.lblMcpStatus = QLabel("状态：未运行")
        self.lblMcpStatus.setWordWrap(True)
        self.lblMcpStatus.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(self.lblMcpStatus)

        row_actions = QHBoxLayout()
        self.btnMcpCopyConfig = QPushButton("复制客户端配置")
        self.btnMcpCopyConfig.setToolTip(
            "复制一段可直接粘贴进 Claude Desktop / Cursor 配置文件的 mcpServers JSON"
        )
        self.btnMcpCopyConfig.clicked.connect(self._on_mcp_copy_config)
        row_actions.addWidget(self.btnMcpCopyConfig)
        btn_check = QPushButton("测试连通性")
        btn_check.setToolTip("运行 MCP Server 的自检，确认外部客户端能连上插件内的桥接服务")
        btn_check.clicked.connect(self._on_mcp_selfcheck)
        row_actions.addWidget(btn_check)
        outer.addLayout(row_actions)

        self.boxMcp = group
        layout = self.dockwidget.settingsLayout
        layout.insertWidget(layout.count() - 1, group)

        # 桥接服务状态变化时刷新显示
        try:
            from .mcp_bridge import MCPBridge
            MCPBridge.get().statusChanged.connect(lambda _msg: self._refresh_mcp_status())
        except Exception as _e:
            logger.debug("连接 MCP 状态信号失败，忽略: %s", _e, exc_info=True)

        self._refresh_mcp_status()

    @staticmethod
    def _new_mcp_token():
        try:
            from .mcp_bridge import _default_token
            return _default_token()
        except Exception:  # noqa: BLE001
            import os as _os
            return _os.urandom(32).hex()

    def _refresh_mcp_status(self):
        try:
            from .mcp_bridge import MCPBridge
            bridge = MCPBridge.get()
            running = bridge.is_running()
            if running:
                self.lblMcpStatus.setText(
                    "状态：运行中 · 监听 127.0.0.1:%d · 工具 %d 个（含危险工具：%s）"
                    % (bridge.port or 0,
                       len(self._mcp_visible_tool_names()),
                       "是" if self.cbMcpDangerous.isChecked() else "否")
                )
                self.btnMcpToggle.setText("停止服务")
            else:
                self.lblMcpStatus.setText("状态：未运行")
                self.btnMcpToggle.setText("启动服务")
        except Exception as _e:
            logger.debug("刷新 MCP 状态失败: %s", _e, exc_info=True)

    def _mcp_visible_tool_names(self):
        try:
            from .mcp_bridge import MCPBridge
            from .qgis_tools import TOOL_DEFINITIONS
            dangerous = MCPBridge._dangerous_tool_names()
            if self.cbMcpDangerous.isChecked():
                return [t.get("name") for t in TOOL_DEFINITIONS]
            return [t.get("name") for t in TOOL_DEFINITIONS
                    if t.get("name") not in dangerous]
        except Exception:  # noqa: BLE001
            return []

    def _persist_mcp_settings(self):
        settings = self._mcp_settings()
        settings.setValue("mcp/autostart", self.cbMcpAutostart.isChecked())
        settings.setValue("mcp/port", int(self.spMcpPort.value()))
        settings.setValue("mcp/token", self.leMcpToken.text().strip())
        settings.setValue("mcp/allow_dangerous", self.cbMcpDangerous.isChecked())

    def _on_mcp_toggle(self):
        from .mcp_bridge import MCPBridge
        bridge = MCPBridge.get()
        if bridge.is_running():
            _ok, message = bridge.stop()
            self._persist_mcp_settings()
            self._refresh_mcp_status()
            _set_status = getattr(self.dockwidget, "_set_status", None)
            if callable(_set_status):
                _set_status("MCP 服务已停止")
            return

        token = self.leMcpToken.text().strip()
        if not token:
            token = self._new_mcp_token()
            self.leMcpToken.setText(token)
        self._persist_mcp_settings()
        ok, message = bridge.start(
            port=int(self.spMcpPort.value()),
            token=token,
            allow_dangerous=self.cbMcpDangerous.isChecked(),
        )
        self._refresh_mcp_status()
        if ok:
            _set_status = getattr(self.dockwidget, "_set_status", None)
            if callable(_set_status):
                _set_status("MCP 服务已启动 · 127.0.0.1:%d" % (bridge.port or 0))
        else:
            QMessageBox.warning(self.dockwidget, "MCP 服务启动失败", message)

    def _maybe_autostart_mcp(self):
        """按设置决定是否随插件启动 MCP 服务（失败不打扰用户，仅记录）。"""
        try:
            if not self.cbMcpAutostart.isChecked():
                self._refresh_mcp_status()
                return
            from .mcp_bridge import MCPBridge
            bridge = MCPBridge.get()
            if bridge.is_running():
                self._refresh_mcp_status()
                return
            token = self.leMcpToken.text().strip() or self._new_mcp_token()
            self.leMcpToken.setText(token)
            self._persist_mcp_settings()
            ok, message = bridge.start(
                port=int(self.spMcpPort.value()),
                token=token,
                allow_dangerous=self.cbMcpDangerous.isChecked(),
            )
            self._refresh_mcp_status()
            if not ok:
                logger.warning("MCP 服务自动启动失败: %s", message)
        except Exception as _e:
            logger.debug("MCP 自动启动异常，忽略: %s", _e, exc_info=True)

    def _on_mcp_regenerate_token(self):
        token = self._new_mcp_token()
        self.leMcpToken.setText(token)
        settings = self._mcp_settings()
        settings.setValue("mcp/token", token)
        try:
            from .mcp_bridge import MCPBridge
            bridge = MCPBridge.get()
            if bridge.is_running():
                bridge.apply_settings(token=token)
        except Exception as _e:
            logger.debug("热更新 MCP 令牌失败: %s", _e, exc_info=True)
        QMessageBox.information(
            self.dockwidget, "令牌已更新",
            "已生成新的访问令牌，并写回插件设置。\n"
            "请把新令牌同步到 MCP 客户端配置（点「复制客户端配置」即可拿到）。"
        )

    def _on_mcp_copy_token(self):
        token = self.leMcpToken.text().strip()
        if not token:
            return
        QApplication.clipboard().setText(token)
        _set_status = getattr(self.dockwidget, "_set_status", None)
        if callable(_set_status):
            _set_status("访问令牌已复制到剪贴板")

    def _on_mcp_copy_config(self):
        try:
            from .mcp_bridge import MCPBridge
            import json as _json
            bridge = MCPBridge.get()
            config = bridge.client_config()
            text = _json.dumps(config, ensure_ascii=False, indent=2)
            QApplication.clipboard().setText(text)
            QMessageBox.information(
                self.dockwidget, "客户端配置已复制",
                "已复制 Claude Desktop / Cursor 的 mcpServers 配置片段：\n\n"
                + text + "\n\n粘贴到客户端的配置文件后重启客户端即可。"
            )
        except Exception as _e:
            QMessageBox.warning(self.dockwidget, "复制失败", "生成配置片段失败：%s" % _e)

    def _on_mcp_selfcheck(self):
        """在 QGIS 自带 Python 里跑一次 MCP Server 自检，确认整条链路通。"""
        import subprocess
        import sys as _sys
        server_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "mcp_server", "qgis_agent_mcp_server.py",
        )
        if not os.path.exists(server_script):
            QMessageBox.warning(self.dockwidget, "找不到 MCP Server",
                                "未找到 %s" % server_script)
            return
        try:
            proc = subprocess.run(
                [_sys.executable, server_script, "--check"],
                capture_output=True, text=True, timeout=30,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
        except Exception as _e:
            QMessageBox.warning(self.dockwidget, "自检失败", "无法运行自检：%s" % _e)
            return
        box = QMessageBox(self.dockwidget)
        box.setWindowTitle("MCP 连通性自检")
        box.setText("自检%s" % ("通过" if proc.returncode == 0 else "未通过"))
        box.setDetailedText(output.strip())
        box.exec()


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
