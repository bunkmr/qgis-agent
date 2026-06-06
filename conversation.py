from qgis.PyQt.QtCore import pyqtSignal, QObject

from .utils import get_current_timestamp, pack, extract_code
from .processor import Processor


class Conversation(QObject):
    llm_response = pyqtSignal(str, str, str)
    llm_reflection = pyqtSignal(str, str, str)
    llm_thinking = pyqtSignal(str)  # 流式思考内容
    llm_tool_status = pyqtSignal(str)  # 工具调用状态
    llm_interrupted = pyqtSignal(str)

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
        self.modified = get_current_timestamp()
        self.code_list = []

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
            self.messageCount += 1  # 在发送时 +1，不再在回调中重复 +1
            return self._update_llm_response(message, response_type)

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
        return (f"创建时间: {self.created} | 模型: {self.model_name} | "
                f"消息: {self.messageCount} | 工作流: {self.workflowCount}")

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
