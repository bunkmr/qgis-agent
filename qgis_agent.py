# -*- coding: utf-8 -*-

import os
import re
import sys
import html as html_module

from qgis.PyQt.QtCore import (
    QSettings, Qt, QTimer, QPoint, QCoreApplication, QT_TRANSLATE_NOOP,
    pyqtSignal, QThread
)
from qgis.PyQt.QtGui import QIcon, QPalette, QFont
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QPushButton, QLineEdit, QPlainTextEdit,
    QDockWidget, QApplication, QMessageBox, QLabel, QVBoxLayout, QHBoxLayout,
    QComboBox, QTableWidgetItem, QFrame, QToolBar, QInputDialog,
    QGroupBox, QSpinBox
)
from qgis.utils import iface

from . import i18n
from .package_manager import PackageManager
from .qt_widgets import WrappingCheckBox
import logging
import contextlib
logger = logging.getLogger(__name__)

#: 界面文案翻译入口。写法**刻意与 pyuic 生成的代码保持一致**（字面量 context +
#: 模块级别名），因为 pylupdate 只认这种字面量形式 —— 把 context 写成变量
#: 它就抓不到这些串（提取结果会静默变少，翻译"看起来没生效"）。
#: 三处 context 必须一致：这里、base_ui、``build_translations.CONTEXT``。
_translate = QCoreApplication.translate

#: 插件菜单名。用 ``QT_TRANSLATE_NOOP`` 声明 —— 它是 Qt 官方的"**只标记**、不
#: 翻译"写法：pylupdate 照常把它抓进 .ts，而运行时拿到的仍是原文，由我们自己
#: 在合适的时机（切换语言时）现翻。这样菜单名不必为了可提取而写两遍。
#: （已实测 pylupdate 确实认得这个宏。）
_MENU_TEXT = QT_TRANSLATE_NOOP("QGISAgent", "QGIS 智能助手(&Q)")

#: 工具栏标题，同上。
_TOOLBAR_TEXT = QT_TRANSLATE_NOOP("QGISAgent", "QGIS Agent")

#: 工具栏/菜单里那一个动作的文案，同上（同样用 NOOP 标记，运行时现翻）。
_ACTION_OPEN_TEXT = QT_TRANSLATE_NOOP("QGISAgent", "打开 QGIS Agent")

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
        self.setWindowTitle(_translate("QGISAgent", "代码执行确认"))
        self.setMinimumSize(580, 520)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # ⚠️ f-string 里的内容 pylupdate 抓不到，必须改成 _translate(...) % 值 的形式
        warn = QLabel(
            _translate(
                "QGISAgent",
                "即将执行 <b>%s</b>，是否继续？\n"
                "请检查下方代码是否正确，确认无误后再点「执行」。")
            % html_module.escape(tool_name)
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
        self.reviewStatus = QLabel(_translate("QGISAgent", "🔍 代码审查：正在后台审查…"))
        self.reviewStatus.setWordWrap(True)
        self.reviewStatus.setStyleSheet("QLabel { color:#666; }")
        layout.addWidget(self.reviewStatus)

        self.reviewView = QPlainTextEdit()
        self.reviewView.setReadOnly(True)
        self.reviewView.setPlainText("审查进行中，可直接决定是否执行，无需等待。")
        self.reviewView.setMaximumHeight(130)
        layout.addWidget(self.reviewView)

        # 按钮行
        btn_once = QPushButton(_translate("QGISAgent", "仅此一次"))
        btn_session = QPushButton(_translate("QGISAgent", "本次会话允许该工具"))
        btn_always = QPushButton(_translate("QGISAgent", "总是允许"))
        btn_cancel = QPushButton(_translate("QGISAgent", "取消"))
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
                self.reviewStatus.setText(_translate("QGISAgent", "🔍 代码审查：不可用（已跳过）"))
                self.reviewStatus.setStyleSheet("QLabel { color:#888; }")
            elif issues:
                self.reviewStatus.setText(_translate(
                    "QGISAgent",
                    "❌ 代码审查：发现 %d 个问题、%d 条建议，请谨慎执行")
                    % (len(issues), len(suggestions)))
                self.reviewStatus.setStyleSheet("QLabel { color:#c0392b; font-weight:bold; }")
            else:
                self.reviewStatus.setText(_translate(
                    "QGISAgent", "✅ 代码审查：未发现问题（%d 条建议）")
                    % len(suggestions))
                self.reviewStatus.setStyleSheet("QLabel { color:#27793f; }")

            lines = []
            if issues:
                lines.append(_translate("QGISAgent", "问题："))
                lines.extend("  - %s" % i for i in issues)
            if suggestions:
                lines.append(_translate("QGISAgent", "建议："))
                lines.extend("  - %s" % s for s in suggestions)
            if summary:
                lines.append("")
                lines.append(_translate("QGISAgent", "审查摘要："))
                lines.append(str(summary))
            self.reviewView.setPlainText(
                "\n".join(lines) or _translate("QGISAgent", "审查未返回内容。"))
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


class DiagnosisDialog(QDialog):
    """连接诊断结果：一句结论 + 可滚动的明细报告 + 复制。

    为什么不用 QMessageBox：诊断报告有十几行（含服务端原文与建议），塞进
    QMessageBox 的正文会被撑得很丑，塞进「详细」又要用户再点一次展开；
    而且那里没有「复制」——用户想把这个报告发给别人看时只能手抄。
    """

    def __init__(self, headline, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_translate("QGISAgent", "连接诊断"))
        self.resize(600, 440)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self.lblHeadline = QLabel(str(headline or _translate("QGISAgent", "诊断完成")))
        self.lblHeadline.setWordWrap(True)
        font = self.lblHeadline.font()
        font.setBold(True)
        self.lblHeadline.setFont(font)
        layout.addWidget(self.lblHeadline)

        self.txtReport = QPlainTextEdit(str(report or ""))
        self.txtReport.setReadOnly(True)
        self.txtReport.setStyleSheet(
            "font-family: Consolas, Menlo, Monaco, monospace; font-size: 11px;"
        )
        layout.addWidget(self.txtReport, 1)

        buttons = QHBoxLayout()
        self.pbCopy = QPushButton(_translate("QGISAgent", "复制报告"))
        self.pbCopy.clicked.connect(self._copy_report)
        pbClose = QPushButton(_translate("QGISAgent", "关闭"))
        pbClose.clicked.connect(self.accept)
        buttons.addWidget(self.pbCopy)
        buttons.addStretch(1)
        buttons.addWidget(pbClose)
        layout.addLayout(buttons)

    def _copy_report(self):
        try:
            QApplication.clipboard().setText(self.txtReport.toPlainText())
            self.pbCopy.setText(_translate("QGISAgent", "已复制 ✓"))
        except Exception as _e:
            logger.debug("复制诊断报告失败: %s", _e, exc_info=True)


class _EndpointDiagnoseWorker(QThread):
    """后台跑端点诊断（地址 / 模型名 / 上下文 / 工具支持四项检查）。

    为什么必须后台：诊断会依次发起最多 6 个 HTTP 请求，每项各自带 timeout，
    最坏情况要等十几秒；放主线程会把 QGIS 界面卡住。

    finished(ok: bool, headline: str, report: str)
    """

    finished = pyqtSignal(bool, str, str)

    def __init__(self, provider, model, api_key, endpoint, timeout=8, browser_tls=False):
        super().__init__()
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.browser_tls = browser_tls

    def run(self):
        try:
            try:
                from .endpoint_diagnostics import diagnose, format_report
            except ImportError:
                from endpoint_diagnostics import diagnose, format_report
            result = diagnose(
                self.provider, self.model, self.api_key, self.endpoint,
                timeout=self.timeout, browser_tls=self.browser_tls,
            )
            self.finished.emit(
                bool(result.get("ok")),
                str(result.get("headline") or "诊断完成"),
                format_report(result),
            )
        except Exception as _e:  # noqa: BLE001 - 诊断失败也要给用户一个明确交代
            logger.debug("端点诊断异常: %s", _e, exc_info=True)
            self.finished.emit(False, "诊断未能完成", "诊断过程中出现异常：%s" % _e)


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

        # ── 界面语言必须在**造出任何界面文案之前**装好 ──
        # QAction 的 text 是在构造那一刻取的值，之后再 retranslate 是补不上的
        # （菜单名、工具栏提示都在这里定下来）。失败一律吞掉：语言只是显示层，
        # 不该成为插件起不来的原因。
        try:
            i18n.apply_language()
        except Exception as _e:  # noqa: BLE001
            logger.debug("装载界面语言失败，回退源码原文: %s", _e, exc_info=True)

        self.actions = []
        #: [(QAction, 源码原文), ...] —— 切换语言时按原文重算文案。
        self._action_sources = []
        #: 当前显示用的插件菜单名（翻译结果）。
        self.menu = _translate("QGISAgent", _MENU_TEXT)
        #: 已经注册进 QGIS 的那个名字 —— 换语言时靠它"摘旧的、挂新的"。
        self._registered_menu = self.menu
        self.toolbar = self.iface.addToolBar(_translate("QGISAgent", _TOOLBAR_TEXT))
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

    def add_action(self, icon_path, text, callback, enabled_flag=True,
                   add_to_menu=True, add_to_toolbar=True, status_tip=None,
                   whats_this=None, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, _translate("QGISAgent", text), parent)
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
        # 留下源码原文，切换语言时才能重算一次文案（QAction 的 text 只在构造时
        # 取一次，不记原文就再也翻不回去）。
        self._action_sources.append((action, text))
        self.actions.append(action)
        return action

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.add_action(
            icon_path,
            text=_ACTION_OPEN_TEXT,
            callback=self.run,
            parent=self.iface.mainWindow(),
        )

    def _clamp_dock_to_screen(self):
        """把底边落到屏幕可用区之外的 dock 收回来（一次性的历史尺寸纠正）。

        背景：v2.4.2 及以前，Qt5 侧「工作流」页使用的 QtWebKit ``QWebView``
        没有实现 sizeHint()，Qt 回落到默认的 800x600，把 QTabWidget 的
        sizeHint 顶到 812x805。dock 一旦做尺寸自适应就被撑到屏幕之外，而
        QGIS 会把这个尺寸记进 profile —— 因此**升级后底座尺寸依然是坏的**，
        表现就是底部输入框（发送 / 停止）永远在屏幕外面。

        修 sizeHint 只保证「以后不再被撑大」，已经存下来的坏尺寸必须就地
        收回一次。判定是客观的：**dock 底边坐标超出屏幕可用区** → 底部控件
        必然看不见。屏幕内的布局（哪怕是用户故意拉满高度）一律不动。
        """
        try:
            dw = self.dockwidget
            if dw is None or not dw.isVisible():
                return
            top = dw.mapToGlobal(QPoint(0, 0))
            screen = dw.screen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            bottom = top.y() + dw.height()
            if bottom <= avail.bottom():
                return
            new_h = max(dw.minimumHeight(), avail.bottom() - top.y())
            new_h = min(new_h, dw.height())
            if new_h >= dw.height():
                return
            logger.info("dock 底边超出屏幕可用区 %dpx，高度 %d → %d",
                        bottom - avail.bottom(), dw.height(), new_h)
            dw.resize(dw.width(), new_h)
        except Exception as _e:  # noqa: BLE001 - 尺寸纠正失败绝不能影响启动
            logger.debug("dock 尺寸纠正跳过: %s", _e)

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
                # ⚠️ 必须用**已注册**的那个名字：菜单名会随界面语言变，写死字面量
                # 时英文界面下会摘不掉（QGIS 找不到那个菜单），留下一个空壳子菜单。
                self.iface.removePluginMenu(self._registered_menu, action)
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
                    None, _translate("QGISAgent", "依赖不可用"),
                    _translate("QGISAgent", "QGIS Agent 的 Python 依赖无法正常加载。\n\n"
                    "请打开「QGIS Agent」面板的日志或 QGIS 的 Python 控制台查看详细信息。"))
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
            msg.setWindowTitle(_translate("QGISAgent", "依赖已安装但无法加载"))
            msg.setText(_translate("QGISAgent", "以下 Python 库可以找到，但导入时报错，插件无法继续启动："))
            msg.setInformativeText(
                package_manager.broken_report() + "\n\n" + package_manager.hint_text())
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
            return

        # 情况二：确实没有 → 询问是否自动安装。
        if not package_manager.missing:
            QMessageBox.information(None, _translate("QGISAgent", "已就绪"), _translate("QGISAgent", "依赖已安装，请重启 QGIS。"))
            return

        msg = QMessageBox()
        msg.setWindowTitle(_translate("QGISAgent", "缺少依赖"))
        msg.setText(_translate("QGISAgent", "QGIS Agent 需要安装以下 Python 库："))
        detail = "\n".join("• %s" % m for m in package_manager.missing)
        msg.setInformativeText(
            detail + _translate("QGISAgent", "\n\n是否尝试自动安装？"))
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            ok = package_manager.install_missing()
            if ok:
                QMessageBox.information(None, _translate("QGISAgent", "安装成功"), _translate("QGISAgent", "依赖安装完成，请重启 QGIS 后重新启用插件。"))
            else:
                QMessageBox.warning(
                    None, _translate("QGISAgent", "安装失败"),
                    _translate(
                        "QGISAgent",
                        "自动安装失败，请在 OSGeo4W Shell 中手动运行：\n\n"
                        "pip install %s") % " ".join(required_modules))

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

        # ⚠️ 控件构建与信号连接**整个进程只做一次**，见 _setup_dock_ui 的说明。
        if not getattr(self, "_dock_ui_ready", False):
            self._setup_dock_ui()
            self._dock_ui_ready = True

        # ── 以下每次「打开面板」都要做（点 × 关闭后再点图标重开会重走一遍）──

        # ── 恢复保存的设置 ──
        self._load_saved_settings()

        # onClosePlugin 里会断开这个连接，重开必须接回来
        with contextlib.suppress(Exception):
            self.dockwidget.closingPlugin.connect(self.onClosePlugin)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dockwidget)
        self.dockwidget.show()

        # 一次性纠正历史遗留的 dock 尺寸（详见 _clamp_dock_to_screen 说明）。
        # 延后到事件循环里跑：dock 的实际尺寸要等布局跑完才定下来。
        QTimer.singleShot(600, self._clamp_dock_to_screen)

        self.dataloader = DataLoader(DB_NAME)
        self.dataloader.connect()

        # 加载模型列表到下拉框
        self._load_model_selector()

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

    def _setup_dock_ui(self):
        """构建面板控件并连接信号 —— **整个进程只做一次**。

        ⚠️ 为什么必须单独拆出来并加闸门：

        用户点面板右上角的 × 只是把 dock **从停靠区摘下来**
        （``onClosePlugin`` 里的 ``removeDockWidget``），dockwidget 对象本身
        还活着、也没被销毁。再点工具栏图标时 ``run()`` 看到
        ``plugin_is_active == False``，于是把 ``_init_plugin()`` 整个再跑一遍
        —— 而它当时是「复用已有 dockwidget」的，于是这里每跑一次就：

        * 往设置页 ``settingsLayout`` **再插一个**「🌐 语言 / Language」分组、
          再插一个「🔌 测试连接与诊断」按钮、再插一套 TLS 复选框与说明；
        * 往 MCP 页 ``mcpLayout`` **再插一个**「MCP 服务」分组；
        * 把所有 ``connect(...)`` 再来一遍（同一个槽被连两次）。

        实测（QGIS 3.44.14 / Qt5，模拟 3 次「关闭 → 重开」）：

        ================  ========  ========  ========
        项目                第1次     第2次     第3次
        ================  ========  ========  ========
        语言分组              1         2         3
        测试连接按钮           1         2         3
        TLS 复选框           1         2         3
        MCP 分组             1         2         3
        ================  ========  ========  ========

        这正是「栏目会随启动次数增加」的原因。

        闸门放在 ``_dock_ui_ready`` 上，而**不是**改 ``onClosePlugin`` 去
        销毁 dockwidget —— 后者会让重开后聊天记录凭空清空，而且异步回调
        （MCP 状态、RAG 进度、定时器）可能拿到已销毁的对象。
        """
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

        self.dockwidget.pbSend.clicked.connect(self._on_new_message_send)
        self.dockwidget.enterPressed.connect(self._on_new_message_send)
        self.dockwidget.stopRequested.connect(self._on_stop_requested)
        self.dockwidget.pbNew.clicked.connect(self._on_new_conversation)
        self.dockwidget.pbSearchConversationCard.clicked.connect(
            self._on_search_conversation
        )
        self.dockwidget.searchPressed.connect(self._on_search_conversation)
        self.dockwidget.switchClearMode.connect(self._switch_clear_mode)
        # 对话里错误卡片的「诊断连接」链接 —— dock 只发信号，网络请求在这里跑
        self.dockwidget.endpointDiagnosisRequested.connect(self._on_test_connection)

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

        # 初始化模型配置标签页（语言分组、测试连接、TLS、MCP 分组都在这里建）
        self._init_settings_tab()

        # ── 下面两件是「整个进程一次」的启动动作，不属于「打开面板」 ──
        # D4 首次启动引导（仅首次弹出，之后持久化 firstRunDone）
        self._maybe_show_first_run_guide()
        # P1-7 RAG 建索引：移到后台线程，首启不再阻塞界面（约 10-30s）
        self._init_rag_index_async()

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
                        None, _translate("QGISAgent", "无可用模型"),
                        _translate("QGISAgent", "请先在「模型配置」标签页中添加 LLM 模型，或使用「+ 新建对话」指定模型。"),
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
                # ⚠️ 必须先让会话把挂在旧 processor 上的信号连接清掉，再 shutdown。
                # 否则旧 worker 收尾时仍会触发会话的槽，而那时 self.processor 已是
                # 新对象 —— 拿新对象去 disconnect 就是用户现场看到的
                # `TypeError: 'method' object is not connected`（并连带把新对象的
                # 连接误断开，导致新一轮的回复永远渲染不出来）。
                try:
                    self.live_conversation.release_processor()
                except Exception as _e:
                    logger.debug("清理旧 Processor 的连接失败，继续重建: %s", _e, exc_info=True)
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
            # 先显示用户消息。
            # ⚠️ 必须与「重建历史」（dockwidget.updateConversation）共用同一个气泡
            # 函数：两处各写一套 HTML 时，回答一到就会因为样式不同而肉眼可见地
            # 跳变（纯文本 → 气泡卡片）。
            user_html = self.dockwidget.user_bubble_html(
                message, self._get_current_timestamp()
            )
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
                self.dockwidget, _translate("QGISAgent", "需要补充信息"), str(question)
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
            from .error_classifier import classify_error, summarize_error
            info = classify_error(err_text)
            detail = summarize_error(err_text)
        except Exception as _e:
            logger.debug("错误分级不可用，回退为原始错误信息展示: %s", _e, exc_info=True)
            detail = err_text[:240]

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

        # 关键改动：无论分级成功与否都带上服务端原文。
        # 此前只把原始报错写进「报告」页签的日志，用户看到的永远是
        # 「错误原因无法自动识别」，必须自己去翻日志才能拿到真正的信息。
        rendered = self.dockwidget.append_error_notice(
            title=title, message=message, hint=hint, detail=detail,
            category=category, raw_text=err_text,
        )
        if not rendered:
            # 极端兜底：连卡片都渲染不出来时，至少往聊天区写一行红字
            try:
                self.dockwidget.txHistory.append(
                    "<p style='color:red;'><b>%s</b> %s</p>"
                    % (html_module.escape(title), html_module.escape(message))
                )
            except Exception as _e:
                logger.debug("兜底错误展示失败: %s", _e, exc_info=True)

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
        with contextlib.suppress(Exception):
            callback(result)

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
        with contextlib.suppress(Exception):
            _set_status = getattr(self.dockwidget, "_set_status", None)
            if callable(_set_status):
                _set_status("✅ API 索引就绪")

    def _maybe_show_first_run_guide(self):
        """D4：首次启动引导。仅在首次弹出一次，之后持久化 firstRunDone。"""
        try:
            settings = QSettings("QGIS", "QGISAgent")
            if settings.value("firstRunDone", False, type=bool):
                return
            QMessageBox.information(
                self.dockwidget,
                _translate("QGISAgent", "欢迎使用 QGIS Agent"),
                _translate("QGISAgent", "这是一款在 QGIS 内运行的 AI 助手插件。\n\n"
                "• 在底部输入框直接描述 GIS 任务（如「把图层重投影到 WGS84」）；\n"
                "• 执行 PyQGIS 代码前会弹出确认框，可勾选「总是允许」免重复确认；\n"
                "• 首次会自动在后台构建 PyQGIS API 索引（约 10-30 秒，不卡界面）；\n"
                "• 发送中可随时点「停止」，停止后该对话仍可继续。\n\n"
                "更多用法见帮助页（聊天框右上角「?」）。"),
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
        conv = self.live_conversation
        # ⚠️ 必须在 stop() **之前**读「是否有在途调用」：stop() 只负责发出中断请求，
        # 但旧实现顺带把 llm_finished 置了 True，于是这里永远判成「没有正在进行的
        # 生成」，「停止中…」中间态形同虚设。Conversation.stop 已不再置位，
        # 这里仍保持「先读状态、再发中断」的写法，不依赖对方实现细节。
        busy = conv is not None and not getattr(conv, "llm_finished", True)
        if conv is not None:
            conv.stop()

        if not busy:
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
        with contextlib.suppress(Exception):
            self.dockwidget.pbStop.setText(_translate("QGISAgent", "停止中…"))
            self.dockwidget.pbStop.setEnabled(False)
            _set_status = getattr(self.dockwidget, "_set_status", None)
            if callable(_set_status):
                _set_status("⏹ 正在停止…")
        self.dockwidget.txHistory.append(
            "<p style='color:#888;'>⏹ 已发送停止请求</p>"
        )

    def _reset_send_controls(self):
        """U10：恢复发送/停止按钮到初始可用状态（停止按钮文案复位为「停止」）。"""
        with contextlib.suppress(Exception):
            self.dockwidget.pbStop.setText(_translate("QGISAgent", "停止"))
            self.dockwidget.pbStop.setEnabled(True)

    def _on_new_conversation(self):
        from .dialog_new_conversation import NewConversationDialog as NewEditDialog

        if self.edit_dialog is None or not self.edit_dialog.isVisible():
            if self.dataloader is None:
                return

            # 新建对话：使用当前选中的模型
            llm_id = self._get_selected_llm_id()
            if not llm_id:
                # 没有模型可用，提示用户先配置
                QMessageBox.warning(None, _translate("QGISAgent", "无可用模型"), _translate("QGISAgent", "请先在「模型配置」标签页中添加 LLM 模型。"))
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
            _translate("QGISAgent", "删除会话"),
            _translate("QGISAgent", "确定要删除这个会话吗？\n\n"
            "会话中的全部消息与代码记录将被永久移除，删除后不可恢复。"),
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
        self.dockwidget.pbSearchConversationCard.setText(_translate("QGISAgent", "取消"))
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
        self.dockwidget.pbSearchConversationCard.setText(_translate("QGISAgent", "搜索"))

    def _on_tab_changed(self, index):
        """标签页切换时刷新模型配置页"""
        # 按控件定位而不是写死下标：页签增删（如 v2.4.5 插入「MCP」页）时
        # 硬编码的 index 会静默指向别的页 —— 表现是「切到某个页签时表格莫名刷新」。
        tabs = getattr(self.dockwidget, "twTabs", None)
        target = getattr(self.dockwidget, "tbSettings", None)
        if tabs is None or target is None:
            return
        try:
            if index == tabs.indexOf(target):
                self._refresh_settings_tab()
        except Exception as _e:  # noqa: BLE001
            logger.debug("切换页签刷新模型表失败，忽略: %s", _e, exc_info=True)

    # ──────────────────────────────────────────────
    # 界面语言 / Interface language（v2.4.14 引入）
    # ──────────────────────────────────────────────
    def _build_language_ui(self):
        """在「模型」设置页顶部放一个「语言 / Language」分组。

        ⚠️ 分组标题与下拉项**刻意不做翻译**（永远两种写法并排）：
        界面本身可能是中文或英文，双语标注才能保证用户在任一边都认得出这个
        入口 —— 尤其是"界面已经被切成英文、想切回中文"的那一刻。若把标题也
        翻译掉，英文界面里它就只剩一个 "Language"，中文用户反而找不到它。
        这与用户明确要求的「语言设置的菜单名称要双语标注」一致。
        """
        # ⚠️ 这一串**不能**包 _translate：它必须两种语言并排同时出现。
        group = QGroupBox("🌐 语言 / Language")
        outer = QVBoxLayout(group)
        outer.setSpacing(4)

        self.cbLanguage = QComboBox()
        # 同理，工具提示也保持双语原文，不做翻译。
        self.cbLanguage.setToolTip(
            "界面语言 / Interface language\n"
            "默认跟随 QGIS 的语言设置；也可以在这里单独指定。\n"
            "Defaults to the QGIS language setting; you can also pick one here.\n"
            "改动立即生效，无需重启 QGIS（插件菜单名会在下次启动时更新）。\n"
            "Takes effect immediately, no restart needed "
            "(the plugin menu name updates on the next start)."
        )
        # 第一项固定是"跟随系统"，其余按 i18n 支持的清单来 —— 都用 itemData 存语言码，
        # 显示文本换成双语也不会影响取值。
        self.cbLanguage.addItem(i18n.AUTO_LABEL, i18n.AUTO)
        for code, label in i18n.available_languages():
            self.cbLanguage.addItem(label, code)
        self._select_language(i18n.stored_choice())
        self.cbLanguage.currentIndexChanged.connect(self._on_language_changed)
        outer.addWidget(self.cbLanguage)

        self.lblLanguageHint = QLabel()
        self.lblLanguageHint.setWordWrap(True)
        self.lblLanguageHint.setStyleSheet("color: #666; font-size: 11px;")
        self._refresh_language_hint()
        outer.addWidget(self.lblLanguageHint)

        self.boxLanguage = group
        # 插在页面标题/说明之后、模型表格之前：语言是全局设置，放最上面一眼可见 ——
        # 窄面板里不必滚过模型表格才找得到它。
        self.dockwidget.settingsLayout.insertWidget(2, group)

    def _select_language(self, value):
        """按设置值选中下拉项；认不出来则落到「跟随系统」。"""
        label = i18n.choice_label(value)
        idx = self.cbLanguage.findText(label)
        self.cbLanguage.blockSignals(True)      # 程序化选中不该触发"用户改了语言"
        self.cbLanguage.setCurrentIndex(idx if idx >= 0 else 0)
        self.cbLanguage.blockSignals(False)

    def _current_language_choice(self):
        """读下拉框当前选中的语言码（拿不到就回 auto）。"""
        data = self.cbLanguage.currentData()
        return str(data).strip() if data else i18n.AUTO

    def _on_language_changed(self, _index=None):
        """用户切换语言：落盘 → 当场装载 → 就地刷新界面文案。"""
        choice = self._current_language_choice()
        try:
            QSettings("QGIS", "QGISAgent").setValue(i18n.SETTINGS_KEY, choice)
        except Exception as _e:  # noqa: BLE001
            logger.debug("保存语言设置失败: %s", _e, exc_info=True)
        applied = i18n.apply_language(choice)
        self._refresh_after_language_change(applied)

    def _refresh_language_hint(self):
        """说清楚"现在实际用的是哪个语言"，尤其是跟随系统时跟随到了什么。"""
        label = getattr(self, "lblLanguageHint", None)
        if label is None:
            return
        if i18n.stored_choice() == i18n.AUTO:
            label.setText(_translate(
                "QGISAgent",
                "当前跟随 QGIS 的语言（%s）。如需固定为某种语言，请在上方选择。")
                % i18n.language_label(i18n.preferred_language()))
        else:
            label.setText(_translate(
                "QGISAgent", "当前界面语言：%s。")
                % i18n.language_label(i18n.preferred_language()))

    def _refresh_after_language_change(self, _applied_code=None):
        """语言切换后把界面文案刷新一遍。

        ⚠️ 这里**只改字符串，不重建任何控件**：下拉框本身、对话列表、当前会话
        都还在用着，重建会把用户状态抹掉。静态文案由 base_ui 的 retranslateUi
        统一覆盖（那是 setupUi 的镜像），本文件自己造的控件在这里另刷。
        """
        try:
            self.dockwidget.retranslateUi()
        except Exception as _e:  # noqa: BLE001
            logger.debug("刷新 dock 文案失败: %s", _e, exc_info=True)
        # base_ui 管不到运行时动态创建的控件的文案，由 dockwidget 自己刷
        try:
            self.dockwidget.retranslate_dynamic_ui()
        except Exception as _e:  # noqa: BLE001
            logger.debug("刷新 dock 动态文案失败: %s", _e, exc_info=True)
        self._retranslate_own_widgets()
        self._retranslate_actions()
        self._refresh_plugin_menu()

    def _retranslate_own_widgets(self):
        """刷新 qgis_agent.py 自己造的那些常驻控件（不含 base_ui 负责的部分）。

        与 base_ui 的 ``retranslateUi()`` 是同一件事的两个半边。

        ⚠️ 新增界面文案时，**构造处与这里必须成对写**，否则切换语言后该控件会
        一直停在旧语言（静默、不报错）。``tests/test_i18n_coverage.py`` 会比对
        两处的字符串集合。

        ⚠️ 刻意不动的地方：
          - 语言分组标题与下拉项：它们**永远双语并排**，不参与翻译（见
            ``_build_language_ui``）；
          - 临时对话框（代码确认 / 连接诊断 / 添加模型）：都是按需新建的，
            新建时自然取当前语言，不需要在这里刷；
          - 状态类文案（MCP 服务状态、令牌显示/隐藏、录制状态）：一律**按当前
            状态重算**，不能写死成某个固定词 —— 否则会把界面说反。
        """
        # ── 语言分组自身 ──
        self._refresh_language_hint()

        # ── 模型配置页（mcpLayout 之外的常驻控件）──
        btn = getattr(self, "btnTestConnection", None)
        if btn is not None:
            btn.setText(_translate("QGISAgent", "🔌 测试连接与诊断"))
            btn.setToolTip(_translate(
                "QGISAgent",
                "逐项检查：服务地址是否可达、模型名是否与服务端一致、"
                "上下文长度是否够用、是否支持工具调用（后台线程执行，不阻塞界面）"))
        cb_tls = getattr(self, "cbBrowserTls", None)
        if cb_tls is not None:
            cb_tls.setText(_translate(
                "QGISAgent",
                "使用浏览器兼容 TLS（仅当接口连接被网关重置时需要，"
                "需 pip install curl_cffi）"))
            cb_tls.setToolTip(_translate(
                "QGISAgent",
                "部分 API 网关会依据客户端 TLS 指纹判断请求来源，"
                "非浏览器客户端可能在握手阶段被中断（典型表现：Connection reset by peer）。"
                "开启后改用 curl_cffi 的浏览器 TLS 栈，以提升这类接口的连接成功率。\n"
                "curl_cffi 是可选依赖：未安装时本选项会置灰不可选，"
                "插件改用标准 TLS 栈，其它功能不受影响。"))
            # 提示语有"未安装 / 已启用 / 就绪"三态，必须按状态重算
            self._refresh_browser_tls_hint()

        self._retranslate_mcp_widgets()

        # 最后刷依赖状态的文案：上面若写死过，这里会被状态值纠正
        self._refresh_mcp_status()

    def _retranslate_mcp_widgets(self):
        """刷新 MCP 页的静态文案（状态相关的那几条交给 ``_refresh_mcp_status``）。"""
        group = getattr(self, "boxMcp", None)
        if group is None:
            return                      # MCP 区没建起来（例如早期异常），跳过
        group.setTitle(_translate("QGISAgent", "MCP 服务"))

        hint = getattr(self, "lblMcpHint", None)
        if hint is not None:
            try:
                from .mcp_protocol import session_file_path
                session_hint = session_file_path()
            except Exception:       # noqa: BLE001
                session_hint = "~/.qgis_agent/mcp_session.json"
            hint.setText(
                _translate("QGISAgent",
                           "把本插件的 GIS 工具暴露给 Claude Desktop / Cursor 等外部 Agent 调用。"
                           "启动后仅在 127.0.0.1 上监听，且强制校验访问令牌，"
                           "局域网内其他机器无法连接。"
                           "端口与令牌已写入 %s，外部 MCP Server 会自动读取，"
                           "通常无需手工配置。")
                % session_hint)

        for attr, text in (
            ("cbMcpAutostart", "随插件启动时自动运行 MCP 服务"),
            ("lblMcpPortLabel", "监听端口"),
            ("lblMcpTokenLabel", "访问令牌"),
            ("btnMcpRegen", "重新生成"),
            ("btnMcpCopyToken", "复制令牌"),
            ("btnMcpCopyConfig", "复制客户端配置"),
            ("btnMcpCheck", "测试连通性"),
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setText(_translate("QGISAgent", text))

        tips = (
            ("cbMcpAutostart",
             "只影响「下次 QGIS 启动插件时要不要自动拉起服务」，改动立即写入设置，"
             "不会当场启动或停止服务。"),
            ("spMcpPort",
             "默认 9876。若被占用可换一个端口。\n"
             "⚠️ 监听端口无法热切换：改动会被保存，"
             "但要在「停止服务 → 启动服务」之后才生效，当前连接不受影响。"),
            ("btnMcpRegen", "生成一份新的 32 字节随机令牌（旧令牌立即失效）"),
            ("btnMcpCopyConfig",
             "复制一段可直接粘贴进 Claude Desktop / Cursor 配置文件的 mcpServers JSON。\n"
             "其中的 command 会自动换成「本机确实能跑起 MCP Server」的 Python 解释器 —— "
             "不能直接用 QGIS 主程序，它不会讲 MCP 协议。"),
            ("btnMcpCheck", "运行 MCP Server 的自检，确认外部客户端能连上插件内的桥接服务"),
        )
        for attr, text in tips:
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setToolTip(_translate("QGISAgent", text))

        checkbox = getattr(self, "cbMcpDangerous", None)
        if checkbox is not None:
            checkbox.setText(_translate(
                "QGISAgent",
                "允许外部 Agent 调用特权工具（执行 PyQGIS 代码 / 处理算法 / 删图层 / 运行技能）"))
            checkbox.setToolTip(_translate(
                "QGISAgent",
                "默认关闭：这些工具既不会出现在外部 Agent 的工具清单里，"
                "直接调用也会被拒绝。\n"
                "开启后外部 Agent 可以请求它们，但每次执行仍会在 QGIS 界面上"
                "弹出确认框，由你本人点击确认。\n"
                "「运行技能」之所以归入此类，是因为技能会执行用户技能目录下的 Python 代码。\n"
                "注意：若你同时打开了插件底部的「跳过代码执行确认」，"
                "外部 Agent 的这些操作也将不再弹窗。\n"
                "改动立即生效（服务运行中也会当场刷新工具清单与权限），无需重启服务。"))

        field = getattr(self, "leMcpToken", None)
        if field is not None:
            field.setToolTip(_translate(
                "QGISAgent",
                "外部客户端必须携带该令牌才能调用工具。\n"
                "编辑完（焦点离开输入框）会立即生效并写回设置，服务无需重启。\n"
                "默认以星号隐藏，避免截图/录屏时泄露；要核对时点右侧「显示」展开。\n"
                "取完整令牌请用「复制令牌」按钮，不要手抄屏幕上的片段。"))

        reveal = getattr(self, "btnMcpTokenReveal", None)
        if reveal is not None:
            # 显示 / 隐藏是状态相关：按当前是否勾选重算
            reveal.setText(_translate("QGISAgent", "隐藏") if reveal.isChecked()
                           else _translate("QGISAgent", "显示"))
            reveal.setToolTip(_translate(
                "QGISAgent",
                "在明文与星号之间切换。只改变本机屏幕上的呈现，"
                "不会修改或复制令牌本身。"))

    def _refresh_plugin_menu(self):
        """把插件菜单名换成当前语言的写法。

        ⚠️ QGIS 没有"重命名插件菜单"的接口 —— 只能先 removePluginMenu 摘掉、
        再 addPluginToMenu 挂回去。摘/挂之间菜单是空的，万一 add 失败会留下
        一个彻底消失的菜单，所以异常时必须**用旧名字补挂一次**。
        """
        new_name = _translate("QGISAgent", _MENU_TEXT)
        if new_name == self._registered_menu:
            return
        old_name = self._registered_menu
        try:
            for action in list(self.actions):
                self.iface.removePluginMenu(old_name, action)
            for action in self.actions:
                self.iface.addPluginToMenu(new_name, action)
            self._registered_menu = new_name
            self.menu = new_name
        except Exception as _e:  # noqa: BLE001
            logger.debug("重挂插件菜单失败，回退旧名字: %s", _e, exc_info=True)
            try:
                for action in list(self.actions):
                    self.iface.addPluginToMenu(old_name, action)
            except Exception:  # noqa: BLE001
                logger.debug("回退插件菜单也失败，忽略", exc_info=True)

    def _retranslate_actions(self):
        """按源码原文重算工具栏/菜单里 QAction 的文案。

        ⚠️ QAction 的 text 只在构造时取一次；不主动改，切语言后菜单与工具提示
        会永远停在装载时那一版。
        """
        for action, source in list(getattr(self, "_action_sources", [])):
            try:
                action.setText(_translate("QGISAgent", source))
            except Exception as _e:  # noqa: BLE001
                logger.debug("刷新动作文案失败: %s", _e, exc_info=True)

    def _init_settings_tab(self):
        """初始化模型配置标签页"""
        self._settings_row_data = {}  # row_idx -> {"llm_id": str, "name": str}

        self.dockwidget.btnAddModel.clicked.connect(self._add_model_row)

        # 语言分组最先建：它要插在页面顶部，晚建的话下标会被别的控件挤走
        self._build_language_ui()

        # D14：测试连接按钮（点击后后台线程执行，不阻塞界面）
        # v2.4.2 起升级为「测试连接与诊断」：一次跑完地址 / 模型名 / 上下文 / 工具支持
        # 四项检查 —— 只测「连不连得上」会漏掉最坑的一类故障（模型不支持工具调用时
        # 连接测试照样通过，但对话每次都失败）。
        self.btnTestConnection = QPushButton(_translate("QGISAgent", "🔌 测试连接与诊断"))
        self.btnTestConnection.setToolTip(
            _translate("QGISAgent", "逐项检查：服务地址是否可达、模型名是否与服务端一致、"
            "上下文长度是否够用、是否支持工具调用（后台线程执行，不阻塞界面）")
        )
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

        # ⚠️ 这句说明文案很长，必须用会折行的复选框：QCheckBox 不折行，它的
        #    最小宽度 = 整行文字宽度（实测英文下 888px），会顺着布局一路把
        #    dock 的最小宽度从 690px 顶到 931px —— 英文界面下再也拉不小。
        self.cbBrowserTls = WrappingCheckBox(
            _translate("QGISAgent", "使用浏览器兼容 TLS（仅当接口连接被网关重置时需要，需 pip install curl_cffi）")
        )
        self.cbBrowserTls.setToolTip(
            _translate("QGISAgent", "部分 API 网关会依据客户端 TLS 指纹判断请求来源，非浏览器客户端可能在握手阶段被中断"
            "（典型表现：Connection reset by peer）。开启后改用 curl_cffi 的浏览器 TLS 栈，"
            "以提升这类接口的连接成功率。\n"
            "curl_cffi 是可选依赖：未安装时本选项会置灰不可选，插件改用标准 TLS 栈，其它功能不受影响。")
        )
        requested = bool(QSettings("QGIS", "QGISAgent").value("use_browser_tls", False))
        # 依赖不在位时，把陈旧的「已勾选」纠正掉：设置里写着开、实际永远生效不了，
        # 比直接关掉更容易误导（旧版还会因此让每次 LLM 调用都抛异常）。
        if requested and not self._browser_tls_ready:
            requested = False
            QSettings("QGIS", "QGISAgent").setValue("use_browser_tls", False)
        self.cbBrowserTls.setChecked(requested)
        # 依赖不在位时直接置灰：能勾却永远不生效的开关，只会换来一个
        # 「装了吗？装了也不生效」的弹窗。置灰 + 下方灰字说明，用户一眼知道该做什么。
        # 副作用：_on_browser_tls_changed 里的兜底弹窗在 UI 上变得不可达（那正是目的），
        # 保留它只是为了防住 setChecked(True) 之类的程序化调用。
        if not self._browser_tls_ready:
            self.cbBrowserTls.setEnabled(False)
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
                _translate("QGISAgent", "⚠ 未安装 curl_cffi（可选依赖），此选项已置灰。"
                "只有在接口出现「连接被重置」时才需要它；如需启用，请在 QGIS 自带的 Python 中执行："
                " pip install curl_cffi （装好后重启 QGIS）。不影响其它功能。")
            )
        elif self.cbBrowserTls.isChecked():
            label.setText(_translate("QGISAgent", "✔ 已启用：请求将走 curl_cffi 的浏览器 TLS 栈。"))
        else:
            label.setText(_translate("QGISAgent", "curl_cffi 已就绪，需要时可开启。"))

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
                _translate("QGISAgent", "「浏览器兼容 TLS」暂不可用"),
                _translate("QGISAgent", "未检测到 curl_cffi，该选项已自动关闭。\n\n"
                "它是可选依赖，只在接口连接被网关重置（Connection reset by peer）时才需要。"
                "如需启用，请在 QGIS 自带的 Python 中执行：\n\n"
                "    pip install curl_cffi\n\n"
                "不安装不影响插件的其它功能 —— 插件会自动使用标准 TLS 栈。"),
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
        """把「MCP 服务」设置区填进独立的「MCP」页签（见 base_ui 的 tbMcp）。"""
        settings = self._mcp_settings()
        # 分组标题保持短（只写「MCP 服务」）：窄 dock 下 QGroupBox 的标题会被裁掉，
        # 「供 Claude Desktop / Cursor …」这类说明放到下边的 hint 里更稳妥。
        group = QGroupBox(_translate("QGISAgent", "MCP 服务"))
        outer = QVBoxLayout(group)
        outer.setSpacing(6)

        try:
            from .mcp_protocol import DEFAULT_PORT, session_file_path
            default_port = DEFAULT_PORT
            session_hint = session_file_path()
        except Exception:  # noqa: BLE001
            default_port = 9876
            session_hint = "~/.qgis_agent/mcp_session.json"

        # 存成属性（而不是局部变量）：切换界面语言时要能重新设一遍文案。
        self.lblMcpHint = QLabel(
            _translate("QGISAgent",
                       "把本插件的 GIS 工具暴露给 Claude Desktop / Cursor 等外部 Agent 调用。"
                       "启动后仅在 127.0.0.1 上监听，且强制校验访问令牌，局域网内其他机器无法连接。"
                       "端口与令牌已写入 %s，外部 MCP Server 会自动读取，通常无需手工配置。")
            % session_hint
        )
        self.lblMcpHint.setWordWrap(True)
        self.lblMcpHint.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(self.lblMcpHint)

        # 同 cbBrowserTls：长说明文字必须折行，否则最小宽度会被顶高
        self.cbMcpAutostart = WrappingCheckBox(
            _translate("QGISAgent", "随插件启动时自动运行 MCP 服务"))
        self.cbMcpAutostart.setChecked(bool(settings.value("mcp/autostart", False)))
        self.cbMcpAutostart.setToolTip(
            _translate("QGISAgent",
                       "只影响「下次 QGIS 启动插件时要不要自动拉起服务」，改动立即写入设置，"
                       "不会当场启动或停止服务。")
        )
        # 勾/取消都要落盘 —— 之前只有点「启动/停止服务」时才顺手持久化，
        # 单独勾一下再关设置页，设置就丢了。
        self.cbMcpAutostart.toggled.connect(self._on_mcp_autostart_toggled)
        outer.addWidget(self.cbMcpAutostart)

        row_port = QHBoxLayout()
        self.lblMcpPortLabel = QLabel(_translate("QGISAgent", "监听端口"))
        row_port.addWidget(self.lblMcpPortLabel)
        self.spMcpPort = QSpinBox()
        self.spMcpPort.setRange(1024, 65535)
        try:
            self.spMcpPort.setValue(int(settings.value("mcp/port", default_port)))
        except (TypeError, ValueError):
            self.spMcpPort.setValue(default_port)
        self.spMcpPort.setToolTip(
            _translate("QGISAgent",
                       "默认 9876。若被占用可换一个端口。\n"
                       "⚠️ 监听端口无法热切换：改动会被保存，"
                       "但要在「停止服务 → 启动服务」之后才生效，当前连接不受影响。")
        )
        self.spMcpPort.valueChanged.connect(self._on_mcp_port_changed)
        row_port.addWidget(self.spMcpPort)
        self.btnMcpToggle = QPushButton(_translate("QGISAgent", "启动服务"))
        self.btnMcpToggle.clicked.connect(self._on_mcp_toggle)
        row_port.addWidget(self.btnMcpToggle)
        outer.addLayout(row_port)

        row_token = QHBoxLayout()
        self.lblMcpTokenLabel = QLabel(_translate("QGISAgent", "访问令牌"))
        row_token.addWidget(self.lblMcpTokenLabel)
        self.leMcpToken = QLineEdit()
        # 默认掩码：令牌是长期凭证，明文长期摊在屏幕上，截图/录屏/远程协助时等于直接泄露。
        # 需要核对时点右侧「显示」临时展开。
        self.leMcpToken.setEchoMode(QLineEdit.EchoMode.Password)
        self.leMcpToken.setToolTip(
            _translate("QGISAgent",
                       "外部客户端必须携带该令牌才能调用工具。\n"
                       "编辑完（焦点离开输入框）会立即生效并写回设置，服务无需重启。\n"
                       "默认以星号隐藏，避免截图/录屏时泄露；要核对时点右侧「显示」展开。\n"
                       "取完整令牌请用「复制令牌」按钮，不要手抄屏幕上的片段。")
        )
        token = str(settings.value("mcp/token", "") or "")
        if not token:
            token = self._new_mcp_token()
            settings.setValue("mcp/token", token)
        self.leMcpToken.setText(token)
        self._mcp_token_last_applied = token
        # 光标回到开头（setText 会把视口滚到尾部，屏幕上只剩后半截令牌）。
        self.leMcpToken.setCursorPosition(0)
        # editingFinished：只在"编辑完并离开焦点/回车"时触发，避免每敲一个字符就改令牌。
        self.leMcpToken.editingFinished.connect(self._on_mcp_token_edited)
        row_token.addWidget(self.leMcpToken, 1)
        outer.addLayout(row_token)

        # ⚠️ 三个操作按钮**另起一行**：原先「标签 + 输入框 + 显示 + 重新生成 + 复制令牌」
        #    五件套挤一行，英文按钮文字更长（Show / Regenerate / Copy token），
        #    实测该行最小宽度 **447px**（中文约 330px），把整页顶到 455px —— 又一次
        #    「切到英文后面板变宽」。拆开后两行分别约 140 / 290px。
        row_token_btns = QHBoxLayout()
        self.btnMcpTokenReveal = QPushButton(_translate("QGISAgent", "显示"))
        self.btnMcpTokenReveal.setCheckable(True)
        self.btnMcpTokenReveal.setToolTip(
            _translate("QGISAgent",
                       "在明文与星号之间切换。只改变本机屏幕上的呈现，"
                       "不会修改或复制令牌本身。")
        )
        self.btnMcpTokenReveal.toggled.connect(self._on_mcp_token_reveal_toggled)
        row_token_btns.addWidget(self.btnMcpTokenReveal)
        self.btnMcpRegen = QPushButton(_translate("QGISAgent", "重新生成"))
        self.btnMcpRegen.setToolTip(
            _translate("QGISAgent", "生成一份新的 32 字节随机令牌（旧令牌立即失效）"))
        self.btnMcpRegen.clicked.connect(self._on_mcp_regenerate_token)
        row_token_btns.addWidget(self.btnMcpRegen)
        self.btnMcpCopyToken = QPushButton(_translate("QGISAgent", "复制令牌"))
        self.btnMcpCopyToken.clicked.connect(self._on_mcp_copy_token)
        row_token_btns.addWidget(self.btnMcpCopyToken)
        row_token_btns.addStretch(1)
        outer.addLayout(row_token_btns)

        # 同上：这是全页最长的一句（英文实测 883px），不折行 dock 就拉不小
        self.cbMcpDangerous = WrappingCheckBox(
            _translate("QGISAgent", "允许外部 Agent 调用特权工具（执行 PyQGIS 代码 / 处理算法 / 删图层 / 运行技能）")
        )
        self.cbMcpDangerous.setChecked(bool(settings.value("mcp/allow_dangerous", False)))
        self.cbMcpDangerous.setToolTip(
            _translate("QGISAgent",
                       "默认关闭：这些工具既不会出现在外部 Agent 的工具清单里，"
                       "直接调用也会被拒绝。\n"
                       "开启后外部 Agent 可以请求它们，但每次执行仍会在 QGIS 界面上"
                       "弹出确认框，由你本人点击确认。\n"
                       "「运行技能」之所以归入此类，是因为技能会执行用户技能目录下的 "
                       "Python 代码。\n"
                       "注意：若你同时打开了插件底部的「跳过代码执行确认」，"
                       "外部 Agent 的这些操作也将不再弹窗。\n"
                       "改动立即生效（服务运行中也会当场刷新工具清单与权限），"
                       "无需重启服务。")
        )
        # ⚠️ 这里必须有 toggled 连接：此前 allow_dangerous 只在 bridge.start() 那一刻
        # 读一次，勾上开关却什么都不发生 —— 表现为「测试连接里暴露危险工具仍是 false」，
        # 用户唯一的出路是手动「停止服务 → 启动服务」。apply_settings 早就写好支持
        # 热更新，却只在「重新生成令牌」那一处被调用过。
        self.cbMcpDangerous.toggled.connect(self._on_mcp_dangerous_toggled)
        outer.addWidget(self.cbMcpDangerous)

        self.lblMcpStatus = QLabel(_translate("QGISAgent", "状态：未运行"))
        self.lblMcpStatus.setWordWrap(True)
        self.lblMcpStatus.setStyleSheet("color: #666; font-size: 11px;")
        outer.addWidget(self.lblMcpStatus)

        row_actions = QHBoxLayout()
        self.btnMcpCopyConfig = QPushButton(_translate("QGISAgent", "复制客户端配置"))
        self.btnMcpCopyConfig.setToolTip(
            _translate("QGISAgent",
                       "复制一段可直接粘贴进 Claude Desktop / Cursor 配置文件的 "
                       "mcpServers JSON。\n"
                       "其中的 command 会自动换成「本机确实能跑起 MCP Server」的 "
                       "Python 解释器 —— 不能直接用 QGIS 主程序，它不会讲 MCP 协议。")
        )
        self.btnMcpCopyConfig.clicked.connect(self._on_mcp_copy_config)
        row_actions.addWidget(self.btnMcpCopyConfig)
        self.btnMcpCheck = QPushButton(_translate("QGISAgent", "测试连通性"))
        self.btnMcpCheck.setToolTip(
            _translate("QGISAgent",
                       "运行 MCP Server 的自检，确认外部客户端能连上插件内的桥接服务"))
        self.btnMcpCheck.clicked.connect(self._on_mcp_selfcheck)
        row_actions.addWidget(self.btnMcpCheck)
        outer.addLayout(row_actions)

        self.boxMcp = group
        layout = self.dockwidget.mcpLayout
        # 插到末尾的空档（各页布局末尾都有一个 addStretch，占位用的弹簧必须留在最后）
        layout.insertWidget(max(0, layout.count() - 1), group)

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
                # 显示服务**实际生效**的值，而不是复选框的样子：
                # 两者在热更新尚未落地时会不一致，用复选框的值会掩盖问题。
                dangerous_now = bridge.allow_dangerous
                self.lblMcpStatus.setText(
                    _translate("QGISAgent", "状态：运行中 · 监听 127.0.0.1:%d · 工具 %d 个（含危险工具：%s）")
                    % (bridge.port or 0,
                       len(self._mcp_visible_tool_names()),
                       _translate("QGISAgent", "是") if dangerous_now
                       else _translate("QGISAgent", "否"))
                )
                if dangerous_now != self.cbMcpDangerous.isChecked():
                    self.lblMcpStatus.setText(
                        self.lblMcpStatus.text()
                        + _translate("QGISAgent", "（与服务当前设置不一致）"))
                self.btnMcpToggle.setText(_translate("QGISAgent", "停止服务"))
            else:
                self.lblMcpStatus.setText(_translate("QGISAgent", "状态：未运行"))
                self.btnMcpToggle.setText(_translate("QGISAgent", "启动服务"))
        except Exception as _e:
            logger.debug("刷新 MCP 状态失败: %s", _e, exc_info=True)

    def _mcp_visible_tool_names(self):
        try:
            from .mcp_bridge import MCPBridge
            from .qgis_tools import TOOL_DEFINITIONS
            bridge = MCPBridge.get()
            dangerous = MCPBridge._dangerous_tool_names()
            # 运行中取服务实际生效的开关，未运行取复选框的值 ——
            # 这个方法的语义是"外部 Agent 现在能看到几个工具"，必须与实际一致。
            allowed = (bridge.allow_dangerous if bridge.is_running()
                       else self.cbMcpDangerous.isChecked())
            if allowed:
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

    # ── 设置热更新：改完立即生效，不必"停止服务 → 启动服务" ──
    def _mcp_status_note(self, text):
        """把一行提示写到 dock 底部状态栏（拿不到就静默忽略）。"""
        _set_status = getattr(self.dockwidget, "_set_status", None)
        if callable(_set_status):
            _set_status(text)

    def _apply_mcp_live(self, token=None, allow_dangerous=None):
        """把设置页的改动推给正在运行的桥接服务。

        服务未运行时 `apply_settings` 只更新内存属性、不报错，所以这里
        无需判断运行状态；异常一律吞掉：热更新失败绝不能影响设置页可用性
        （下次启动服务时仍会从设置重新读入，只是"当次不生效"）。
        """
        try:
            from .mcp_bridge import MCPBridge
            MCPBridge.get().apply_settings(token=token, allow_dangerous=allow_dangerous)
        except Exception as _e:  # noqa: BLE001
            logger.debug("热更新 MCP 设置失败: %s", _e, exc_info=True)

    def _on_mcp_dangerous_toggled(self, checked):
        """危险工具开关：勾上/取消立刻生效（服务运行中当场刷新工具清单与权限）。"""
        self._persist_mcp_settings()
        self._apply_mcp_live(allow_dangerous=bool(checked))
        self._refresh_mcp_status()
        from .mcp_bridge import MCPBridge
        if MCPBridge.get().is_running():
            self._mcp_status_note(
                "MCP 危险工具已%s（立即生效）：外部 Agent %s"
                % ("开启" if checked else "关闭",
                   "现在能看到并调用 execute_pyqgis 等特权工具" if checked
                   else "的工具清单已收回特权工具")
            )
        else:
            self._mcp_status_note(
                "MCP 危险工具设置已保存（服务未运行，下次启动时生效）")

    def _on_mcp_token_edited(self):
        """令牌编辑完成：写回设置并热更新服务。

        空令牌一律不落地 —— 服务端把空令牌当作"拒绝一切请求"，
        真写下去会让运行中的服务当场瘫痪（所有调用返回未授权）。
        此时恢复上一次生效值，并明确告知用户。
        """
        token = self.leMcpToken.text().strip()
        last = getattr(self, "_mcp_token_last_applied", "")
        if not token:
            if last:
                QMessageBox.warning(
                    self.dockwidget, _translate("QGISAgent", "令牌不能为空"),
                    _translate("QGISAgent", "访问令牌留空会让所有外部请求被拒绝（服务强制要求令牌）。\n"
                    "已恢复为上一个有效令牌。")
                )
            self.leMcpToken.setText(last)
            self.leMcpToken.setCursorPosition(0)
            return
        if token == last:
            return  # 没变，别白白刷一遍会话文件
        self.leMcpToken.setText(token)
        self.leMcpToken.setCursorPosition(0)
        self._mcp_token_last_applied = token
        settings = self._mcp_settings()
        settings.setValue("mcp/token", token)
        self._apply_mcp_live(token=token)
        from .mcp_bridge import MCPBridge
        if MCPBridge.get().is_running():
            self._mcp_status_note("MCP 访问令牌已更新（立即生效，请同步到客户端配置）")
        else:
            self._mcp_status_note("MCP 访问令牌已保存（服务未运行）")

    def _on_mcp_autostart_toggled(self, checked):
        """自动启动开关：只落盘，不碰运行中的服务（语义就是"下次启动时才用"）。"""
        settings = self._mcp_settings()
        settings.setValue("mcp/autostart", bool(checked))
        self._mcp_status_note(
            "已设置：QGIS 启动时%s自动运行 MCP 服务"
            % ("会" if checked else "不会"))

    def _on_mcp_port_changed(self, value):
        """端口改动：能落盘，但监听端口无法热切换 —— 必须停启服务才生效。"""
        settings = self._mcp_settings()
        settings.setValue("mcp/port", int(value))
        try:
            from .mcp_bridge import MCPBridge
            bridge = MCPBridge.get()
            if bridge.is_running() and bridge.port and int(bridge.port) != int(value):
                self._mcp_status_note(
                    "端口已保存为 %d，但当前仍监听 %d —— 端口需「停止服务 → 启动服务」后生效"
                    % (int(value), int(bridge.port)))
        except Exception as _e:  # noqa: BLE001
            logger.debug("检查 MCP 端口变更失败: %s", _e, exc_info=True)

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
            self.leMcpToken.setCursorPosition(0)
        self._mcp_token_last_applied = token
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
            QMessageBox.warning(self.dockwidget, _translate("QGISAgent", "MCP 服务启动失败"), message)

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
            self.leMcpToken.setCursorPosition(0)
            self._mcp_token_last_applied = token
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
        self.leMcpToken.setCursorPosition(0)
        self._mcp_token_last_applied = token
        settings = self._mcp_settings()
        settings.setValue("mcp/token", token)
        # 复用同一条热更新通道，别再自己写一份 try/except（这里原来只在服务运行时才热更新）。
        self._apply_mcp_live(token=token)
        self._refresh_mcp_status()
        QMessageBox.information(
            self.dockwidget, _translate("QGISAgent", "令牌已更新"),
            _translate("QGISAgent", "已生成新的访问令牌，并立即生效（服务运行中无需重启）。\n"
            "请把新令牌同步到 MCP 客户端配置（点「复制客户端配置」即可拿到）。")
        )

    def _on_mcp_copy_token(self):
        token = self.leMcpToken.text().strip()
        if not token:
            return
        QApplication.clipboard().setText(token)
        _set_status = getattr(self.dockwidget, "_set_status", None)
        if callable(_set_status):
            _set_status("访问令牌已复制到剪贴板")

    def _on_mcp_token_reveal_toggled(self, revealed):
        """在明文与星号之间切换访问令牌的显示方式。

        只改 EchoMode 这一个呈现层属性：令牌值、QSettings 里的持久值、
        「复制令牌」取到的内容都不受影响。切换后把光标压回开头，
        避免 QLineEdit 重排时把视口滚到尾部长住不动。
        """
        self.leMcpToken.setEchoMode(
            QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password
        )
        self.btnMcpTokenReveal.setText(_translate("QGISAgent", "隐藏") if revealed else _translate("QGISAgent", "显示"))
        self.leMcpToken.setCursorPosition(0)

    def _on_mcp_copy_config(self):
        try:
            from .mcp_bridge import MCPBridge
            import json as _json
            bridge = MCPBridge.get()
            config = bridge.client_config()
            text = _json.dumps(config, ensure_ascii=False, indent=2)
            QApplication.clipboard().setText(text)
            # 解释器被自动替换过就一并说明 —— 否则用户看到 command 不是 QGIS 的
            # 路径会以为复制错了，或者反过来把 command 手动改回 QGIS 主程序。
            hint = str(getattr(bridge, "last_python_hint", "") or "")
            extra = ("\n\n" + hint) if hint else ""
            QMessageBox.information(
                self.dockwidget, _translate("QGISAgent", "客户端配置已复制"),
                _translate("QGISAgent", "已复制 Claude Desktop / Cursor 的 mcpServers 配置片段：\n\n")
                + text + extra + "\n\n粘贴到客户端的配置文件后重启客户端即可。"
            )
        except Exception as _e:
            QMessageBox.warning(self.dockwidget, _translate("QGISAgent", "复制失败"), _translate("QGISAgent", "生成配置片段失败：%s") % _e)

    def _on_mcp_selfcheck(self):
        """用一个真能跑的解释器执行 MCP Server 自检，确认整条链路通。

        ⚠️ 必须走 ``resolve_python_executable``，不能直接用 ``sys.executable``：
        macOS 上它是 QGIS 的 GUI 主程序，拿它去跑脚本等于**再启动一个 QGIS 界面**
        （自检会永远等不到输出，用户屏幕上还会多出一个窗口）。这与「复制客户端
        配置」是同一个坑，必须共用同一套解析逻辑。
        """
        # 仅用于跑 MCP 服务脚本自检：列表传参、不经 shell。
        import subprocess  # nosec B404
        server_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "mcp_server", "qgis_agent_mcp_server.py",
        )
        if not os.path.exists(server_script):
            QMessageBox.warning(self.dockwidget, _translate("QGISAgent", "找不到 MCP Server"),
                                _translate("QGISAgent", "未找到 %s") % server_script)
            return
        try:
            from .mcp_bridge import resolve_python_executable
            python, _note = resolve_python_executable(server_script=server_script)
        except Exception as _e:  # noqa: BLE001
            logger.debug("解析自检解释器失败，回退当前进程: %s", _e, exc_info=True)
            python = sys.executable
        try:
            # 参数以列表传入、不使用 shell；python 与脚本路径都来自插件自身配置。
            proc = subprocess.run(  # nosec B603
                [python, server_script, "--check"],
                capture_output=True, text=True, timeout=30,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
        except Exception as _e:
            QMessageBox.warning(self.dockwidget, _translate("QGISAgent", "自检失败"), _translate("QGISAgent", "无法运行自检：%s") % _e)
            return
        box = QMessageBox(self.dockwidget)
        box.setWindowTitle(_translate("QGISAgent", "MCP 连通性自检"))
        box.setText(
            _translate("QGISAgent", "自检%s")
            % (_translate("QGISAgent", "通过") if proc.returncode == 0
               else _translate("QGISAgent", "未通过")))
        box.setDetailedText(_translate("QGISAgent", "解释器：%s\n\n%s")
                            % (python, output.strip()))
        box.exec()


    def _on_test_connection(self):
        """测试连接与诊断：后台跑四项检查（地址 / 模型名 / 上下文 / 工具支持）。

        与旧实现的区别：旧版只发一条纯文本消息，因此**模型不支持工具调用时它照样
        报「连接成功」**，而真实对话每次都失败 —— 用户看到的现象就是「模型在别处
        能用，这里不行」。现在会额外发一次带 tools 的请求，把这个差异显式暴露出来。
        """
        llm_id = self._get_selected_llm_id()
        if not llm_id:
            QMessageBox.warning(
                None, _translate("QGISAgent", "无可用模型"), _translate("QGISAgent", "请先在「模型配置」标签页添加并选中一个模型。")
            )
            return
        try:
            provider, model_name = self.dataloader.get_llm_info(llm_id)
            name, endpoint, api_key = self.dataloader.fetch_llm_info(llm_id)
        except Exception as _e:
            QMessageBox.warning(
                None, _translate("QGISAgent", "读取模型失败"),
                _translate("QGISAgent", "无法读取模型配置：%s") % _e)
            return

        btn = getattr(self, "btnTestConnection", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText(_translate("QGISAgent", "诊断中…"))
        _set_status = getattr(self.dockwidget, "_set_status", None)
        if callable(_set_status):
            _set_status("🔍 正在诊断连接…")

        # 诊断要反映真实请求栈：勾了浏览器兼容 TLS 就按同一栈去探
        effective_tls = False
        try:
            from .llm_providers import resolve_browser_tls
            requested = bool(QSettings("QGIS", "QGISAgent").value("use_browser_tls", False))
            effective_tls = bool(resolve_browser_tls(requested)[0])
        except Exception as _e:
            logger.debug("读取浏览器兼容 TLS 设置失败，按未启用处理: %s", _e, exc_info=True)

        worker = _EndpointDiagnoseWorker(
            provider, model_name, api_key, endpoint, timeout=8, browser_tls=effective_tls
        )
        self._diagnose_worker = worker  # 保持引用，避免被 GC 回收

        def _on_done(ok, headline, report):
            try:
                if btn is not None:
                    btn.setEnabled(True)
                    btn.setText(_translate("QGISAgent", "🔌 测试连接与诊断"))
                if callable(_set_status):
                    _set_status("✅ 诊断通过" if ok else "⚠ 诊断发现问题")
                dlg = DiagnosisDialog(headline, report, self.dockwidget)
                dlg.exec()
            except Exception as _e:
                logger.debug("诊断回调异常: %s", _e, exc_info=True)

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
        key_widget.setPlaceholderText(_translate("QGISAgent", "输入 API Key"))
        key_widget.setStyleSheet("QLineEdit { border: none; padding: 2px; }")
        # 单元格宽度不足以再放一个「显示/隐藏」按钮，此处不做明文切换；
        # 目前只有 MCP「访问令牌」那一行（_build_mcp_settings_ui）带该开关。
        key_widget.setClearButtonEnabled(False)
        table.setCellWidget(row_idx, 2, key_widget)

        # 第3列：删除按钮
        del_btn = QPushButton(_translate("QGISAgent", "删除"))
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
        self.setWindowTitle(_translate("QGISAgent", "添加模型 — 参考信息"))
        self.setMinimumSize(520, 440)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 标题
        title_lbl = QLabel(_translate("QGISAgent", "选择参考模板（可修改任何字段）"))
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title_lbl)

        # 参考信息下拉选择
        ref_layout = QHBoxLayout()
        ref_layout.addWidget(QLabel(_translate("QGISAgent", "参考:")))
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
        layout.addWidget(QLabel(_translate("QGISAgent", "模型名称:")))
        self.ptName = QLineEdit()
        self.ptName.setPlaceholderText(_translate("QGISAgent", "例如: gpt-4o"))
        layout.addWidget(self.ptName)

        # API 端点
        layout.addWidget(QLabel(_translate("QGISAgent", "API 端点:")))
        self.ptEndpoint = QLineEdit()
        self.ptEndpoint.setPlaceholderText(_translate("QGISAgent", "例如: https://api.openai.com/v1"))
        layout.addWidget(self.ptEndpoint)

        # API Key（密码模式）
        layout.addWidget(QLabel("API Key:"))
        self.ptApiKey = QLineEdit()
        self.ptApiKey.setEchoMode(QLineEdit.EchoMode.Password)
        self.ptApiKey.setPlaceholderText(_translate("QGISAgent", "输入 API Key"))
        layout.addWidget(self.ptApiKey)

        layout.addStretch()

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btnOk = QPushButton(_translate("QGISAgent", "添加"))
        self.btnOk.setStyleSheet(
            "QPushButton { background-color: #4A90D9; color: white; border-radius: 4px; padding: 6px 24px; }"
            " QPushButton:hover { background-color: #357ABD; }"
        )
        self.btnOk.clicked.connect(self.accept)
        self.btnCancel = QPushButton(_translate("QGISAgent", "取消"))
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
            self.lblRefInfo.setText(_translate(
                "QGISAgent",
                "<b>可用模型:</b> %s<br>"
                "<b>API 端点:</b> %s<br>"
                "<b>说明:</b> %s")
                % (ref["models"], ref["endpoint"], ref["note"]))
            # 预填端点（用户可修改）
            self.ptEndpoint.setText(ref["endpoint"])
            # 不清除已输入的名称和 key，但如果是第一个端点模板则填入建议
            if not self.ptName.text():
                # 取第一个模型名作为建议
                first_model = ref["models"].split(",")[0].strip()
                self.ptName.setPlaceholderText(
                    _translate("QGISAgent", "例如: %s") % first_model)

    def get_values(self):
        """返回 (name, endpoint, api_key)"""
        return (
            self.ptName.text().strip(),
            self.ptEndpoint.text().strip(),
            self.ptApiKey.text().strip(),
        )
