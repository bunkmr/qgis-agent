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
        """中断当前正在进行的 LLM 调用"""
        if self.processor:
            self.processor.cancel()
        self.llm_finished = True

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

    def _update_llm_response(self, message, response_type):
        self.llm_finished = False
        self.processor.response_ready.connect(self._on_response_ready)
        self.processor.error_signal.connect(self._on_response_interrupted)
        self.processor.async_response(message, response_type)

    def _on_response_ready(self, message, response_type, response, workflow):
        self.processor.response_ready.disconnect(self._on_response_ready)
        # 始终断开 error_signal，防止连接泄漏
        try:
            self.processor.error_signal.disconnect(self._on_response_interrupted)
        except TypeError:
            pass
        self.llm_finished = True
        self.modified = get_current_timestamp()
        self.llm_response.emit(response, workflow, None)

    def _on_response_interrupted(self, error):
        self.processor.response_ready.disconnect(self._on_response_ready)
        self.processor.error_signal.disconnect(self._on_response_interrupted)
        self.llm_interrupted.emit(error)
        self.llm_finished = True

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
            self.processor.reflection_ready.connect(self._on_reflection_ready)
            self.processor.async_reflect(log_message, executed_code, response_type)

    def _on_reflection_ready(self, log_message, response_type, response, workflow):
        self.processor.reflection_ready.disconnect(self._on_reflection_ready)
        self.llm_finished = True
        self.modified = get_current_timestamp()
        self.llm_reflection.emit(response, workflow, None)
