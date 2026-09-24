import logging

from qgis.PyQt.QtCore import pyqtSignal, QObject

from .utils import get_current_timestamp, pack, extract_code, format_timestamp
from .processor import Processor
from .clarification_manager import ClarificationManager

logger = logging.getLogger(__name__)


class Conversation(QObject):
    llm_response = pyqtSignal(str, str, str)
    llm_reflection = pyqtSignal(str, str, str)
    llm_thinking = pyqtSignal(str)  # 流式思考内容
    llm_tool_status = pyqtSignal(str)  # 工具调用状态
    llm_workflow_update = pyqtSignal(dict)  # 工作流更新
    llm_code_update = pyqtSignal(str)  # 代码更新
    llm_execution_log = pyqtSignal(str)  # 执行日志
    llm_interrupted = pyqtSignal(str)
    clarificationRequested = pyqtSignal(str)  # 主动澄清：需要向用户追问

    def __init__(self, conversation_id: str, dataloader):
        super().__init__()
        meta_info = dataloader.select_conversation_info(conversation_id)
        self.meta_info = meta_info
        self.dataloader = dataloader
        self.llm_finished = True
        self.provider, self.model_name = dataloader.get_llm_info(self.llmID)
        self.processor = Processor(self.llmID, self.ID, dataloader)
        self.processor.thinking.connect(self.llm_thinking.emit)
        self.processor.tool_status.connect(self.llm_tool_status.emit)
        self.processor.workflow_update.connect(self.llm_workflow_update.emit)
        self.processor.code_update.connect(self.llm_code_update.emit)
        self.processor.execution_log.connect(self.llm_execution_log.emit)
        self.modified = get_current_timestamp()
        self.code_list = []
        # 本轮请求是否已收尾（幂等标志）。error_signal 与 response_ready 可能
        # 在同一轮里先后到达（工作线程先 emit error 再 emit finished 时，两个事件
        # 都已排进主线程队列，disconnect 拦不住已排队的事件），用它在回调里判重入，
        # 保证一轮请求只 emit 一次结果。
        self._response_handled = True
        # 主动澄清：模糊请求检测与待澄清的原始请求
        self.clarification_manager = ClarificationManager()
        self._pending_request = None
        self._pending_response_type = None

    @property
    def ID(self):
        return self.meta_info.get("ID", "")

    @property
    def llmID(self):
        return self.meta_info.get("llmID", "")

    @property
    def title(self):
        return self.meta_info.get("title", "")

    @title.setter
    def title(self, value):
        self.meta_info["title"] = value

    @property
    def description(self):
        return self.meta_info.get("description", "")

    @description.setter
    def description(self, value):
        self.meta_info["description"] = value

    @property
    def messageCount(self):
        return self.meta_info.get("messageCount", 0)

    @messageCount.setter
    def messageCount(self, value):
        self.meta_info["messageCount"] = value

    @property
    def workflowCount(self):
        return self.meta_info.get("workflowCount", 0)

    @workflowCount.setter
    def workflowCount(self, value):
        self.meta_info["workflowCount"] = value

    @property
    def created(self):
        return self.meta_info.get("created", "")

    @property
    def lastEdit(self):
        return self.meta_info.get("modified", "")

    @lastEdit.setter
    def lastEdit(self, value):
        self.meta_info["modified"] = value

    def stop(self):
        """请求中断当前正在进行的 LLM 调用（异步，不会立即结束）。

        ⚠️ 这里**不能**把 `llm_finished` 置 True。中断是异步的 —— `cancel()`
        只设中断标志并关掉 http 客户端，worker 还要过一会才真正结束。若此处
        抢先置 True 会造成两个真事故：
          1. 界面立刻允许再次发送，而旧 worker 仍在跑 → 下一次
             `_update_llm_response` 又连一遍信号，同一个槽被触发多次
             （现场表现就是 `TypeError: 'method' object is not connected`）；
          2. 判断「是否有在途调用」永远得到 False，「停止中…」中间态形同虚设。
        `llm_finished` 只由收尾回调（_on_response_ready / _on_response_interrupted）
        置位。
        """
        if self.processor:
            self.processor.cancel()

    def update_user_prompt(self, message, response_type):
        if self.llm_finished:
            # 主动澄清：若请求模糊，暂停发给 LLM 并请求用户补充
            try:
                question = self.clarification_manager.get_clarification_response(message)
            except Exception:
                logger.debug("clarification check failed; proceed as normal", exc_info=True)
                question = None
            if question:
                self._pending_request = message
                self._pending_response_type = response_type
                self.clarificationRequested.emit(question)
                return
            self.messageCount += 1  # 在发送时 +1，不再在回调中重复 +1
            return self._update_llm_response(message, response_type)

    def provide_clarification(self, answer: str):
        """用户提供澄清答案后，将「原请求 + 澄清答案」一起重新走正常流程"""
        if not self.llm_finished or not self._pending_request:
            return
        original_request = self._pending_request
        pending_type = self._pending_response_type
        self._pending_request = None
        self._pending_response_type = None
        combined_message = f"{original_request}\n{answer}"
        self.messageCount += 1
        self._update_llm_response(combined_message, pending_type)

    # ── 处理器信号：成对连接 / 幂等断开 ────────────────────────────────

    #: processor 上与本会话生命周期相关的信号 → 对应槽
    _PROCESSOR_SIGNAL_SLOTS = (
        ("response_ready", "_on_response_ready"),
        ("error_signal", "_on_response_interrupted"),
        ("reflection_ready", "_on_reflection_ready"),
    )

    def _detach_processor_signals(self, processor=None):
        """断开本会话在处理器上的全部槽（本来就没连时安全跳过）。

        Qt 在「槽未连接」时 disconnect 会抛 `TypeError: 'method' object is
        not connected`。此处必须逐条 try —— 否则第一行抛异常就会中断后续清理，
        残留的连接会让槽函数被触发多次（画面里表现为回复重复渲染）。

        `processor` 用于显式指定要在哪个对象上断开。**这一步是必需的**：
        `self.processor` 会在用户切换模型/温度时被整体替换（见
        QGISAgent._on_send_message），旧 worker 收尾时触发的是**旧对象**上的
        信号；若此时拿新的 self.processor 去 disconnect，就会报出用户看到的
        那个 TypeError。
        """
        proc = processor if processor is not None else self.processor
        if proc is None:
            return
        for signal_name, slot_name in self._PROCESSOR_SIGNAL_SLOTS:
            signal = getattr(proc, signal_name, None)
            if signal is None:
                continue
            try:
                signal.disconnect(getattr(self, slot_name))
            except (TypeError, RuntimeError) as _e:
                # 已断开 / 从未连接 —— Qt 两种情况都会抛，忽略即可
                logger.debug("断开 %s 时本就没有连接: %s", signal_name, _e)

    def _signal_sender_processor(self):
        """取出发出当前信号的那个 processor（非槽内调用时回退到 self.processor）。

        为什么需要它：`self.processor` 会被外部整体替换，而信号连接留在旧对象上。
        用 `self.sender()` 才能精确知道「这个回调是哪个 processor 发出来的」，
        从而既不在错误的对象上 disconnect，也不会把过期结果当成本轮回复渲染。
        """
        try:
            sender = self.sender()
        except Exception:
            sender = None
        if isinstance(sender, QObject):
            return sender
        return self.processor

    def release_processor(self):
        """对外接口：在 processor 被整体替换**之前**调用。

        断开旧对象上的全部连接并作废本轮，这样旧 worker 即使还在收尾也不会
        触发本会话的槽（否则过期结果会被当成新回复渲染，或 disconnect 直接报错）。
        """
        self._detach_processor_signals()
        self._response_handled = True
        self.llm_finished = True

    def _attach_response_signals(self, processor):
        """连接本轮所需的信号。调用前先 _detach_processor_signals(processor)，
        否则同一槽被连接多次会让一次响应触发多次回调。

        `processor` 必须显式传入并全程复用同一个对象：连接、async_response、
        以及回调里的断开，三者落在同一实例上才不会错位。
        """
        processor.response_ready.connect(self._on_response_ready)
        processor.error_signal.connect(self._on_response_interrupted)

    def _update_llm_response(self, message, response_type):
        self.llm_finished = False
        self._response_handled = False
        # 固定本轮使用的 processor：外部可能在别处整体替换 self.processor，
        # 这里取一次局部引用，保证「连接 / 发起请求」落在同一个对象上。
        processor = self.processor
        self._detach_processor_signals(processor)
        self._attach_response_signals(processor)
        processor.async_response(message, response_type)

    def _on_response_ready(self, message, response_type, response, workflow):
        # 先定位「是谁发的信号」，再幂等断开、判重入 —— 顺序不能反：
        # error_signal 与 response_ready 可能在同一轮里先后到达（工作线程先 emit
        # error 再 emit finished，两个事件都已排进主线程队列，disconnect 拦不住
        # 已排队的事件）。旧写法在 disconnect 上没有 try，第二个回调进来时槽已断开，
        # 第一行就抛 TypeError，导致后面的 emit 全部不执行 ——
        # 界面既不渲染最终回复、`llm_finished` 也不复位，整轮卡死。
        processor = self._signal_sender_processor()
        self._detach_processor_signals(processor)
        if processor is not self.processor:
            # 旧 processor（模型/温度切换后被整体替换）的迟到回调：
            # 只清理它自己的连接，绝不动新 processor 的连接、也不渲染它的结果。
            logger.debug("忽略过期 processor 的 response_ready（旧 worker 收尾）")
            return
        if self._response_handled:
            logger.debug("本轮请求已收尾，忽略重复的 response_ready")
            return
        self._response_handled = True
        self.llm_finished = True
        self.modified = get_current_timestamp()
        self.llm_response.emit(response, workflow, None)

    def _on_response_interrupted(self, error):
        processor = self._signal_sender_processor()
        self._detach_processor_signals(processor)
        if processor is not self.processor:
            logger.debug("忽略过期 processor 的 error_signal（旧 worker 收尾）")
            return
        if self._response_handled:
            logger.debug("本轮请求已收尾，忽略重复的 error_signal")
            return
        self._response_handled = True
        self.llm_finished = True
        self.modified = get_current_timestamp()
        self.llm_interrupted.emit(error)

    def fetch(self):
        interaction_history = self.dataloader.select_interaction(self.ID)
        for interaction in interaction_history:
            interaction_dict = pack(interaction, "interaction")
            if interaction_dict["workflow"] in ("withCode", "withModel"):
                self.code_list.append(extract_code(interaction_dict["responseText"]))
        return interaction_history

    def get_metadata(self):
        """对话头部的元信息摘要。

        时间戳存储格式是 `%m %d %Y %H:%M:%S`（如 `09 23 2026 20:11:02`），
        直接贴在界面上既长又难读，这里统一转成 `2026-09-23 20:11`。

        日期时间与「数字+单位」内部用不换行空格（U+00A0）连接：窄 dock 下
        元信息行需要折行时，断点会落在 `·` 分隔符处，而不是把「2 个工作流」
        拆成两行。
        """
        nb = "\u00a0"
        created = format_timestamp(self.created).replace(" ", nb)
        return (f"创建于 {created} · 模型{nb}{self.model_name} · "
                f"{self.messageCount}{nb}条消息 · {self.workflowCount}{nb}个工作流")

    def clear(self):
        self.dataloader.delete_conversation(self.ID)
        self.messageCount = 0
        self.workflowCount = 0

    def delete(self):
        self.dataloader.delete_conversation(self.ID)

    def update_reflection(self, log_message: str, executed_code: str, response_type: str = "code"):
        if self.llm_finished:
            self.messageCount += 1  # 在发送时 +1，不再在回调中重复 +1
            self.llm_finished = False
            self._response_handled = False
            processor = self.processor
            self._detach_processor_signals(processor)
            processor.reflection_ready.connect(self._on_reflection_ready)
            processor.async_reflect(log_message, executed_code, response_type)

    def _on_reflection_ready(self, log_message, response_type, response, workflow):
        processor = self._signal_sender_processor()
        self._detach_processor_signals(processor)
        if processor is not self.processor:
            logger.debug("忽略过期 processor 的 reflection_ready（旧 worker 收尾）")
            return
        if self._response_handled:
            logger.debug("本轮请求已收尾，忽略重复的 reflection_ready")
            return
        self._response_handled = True
        self.llm_finished = True
        self.modified = get_current_timestamp()
        self.llm_reflection.emit(response, workflow, None)
