from qgis.PyQt.QtCore import QThreadPool, pyqtSignal, QObject

from langchain.prompts import ChatPromptTemplate
from langchain.schema.output_parser import StrOutputParser
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage

from .llm_providers import get_llm_instance
from .utils import get_current_timestamp, pack, extract_code
from .response_worker import ResponseWorker, ReflectWorker
from .dataloader import DataLoader


class Processor(QObject):
    response_ready = pyqtSignal(str, str, str, str)
    reflection_ready = pyqtSignal(str, str, str, str)
    error_signal = pyqtSignal(str)

    def __init__(self, llm_id, conversation_id, dataloader):
        super().__init__()
        self.latest_interaction_id = None
        self.llm_id = llm_id
        self.dataloader = dataloader
        self.provider, self.model_name = llm_id.split("::", 1)
        _, api_key = dataloader.fetch_api_key(llm_id)
        self.llm = get_llm_instance(self.provider, self.model_name, api_key, temperature=0)
        self.output_parser = StrOutputParser()
        self.threadpool = QThreadPool()

    def classifier(self, user_input: str) -> str:
        request_time = get_current_timestamp()
        classifier_prompt_row = {"template": "判断以下用户输入是否需要生成QGIS操作代码或模型，只回答yes或no：{input}", "ID": "classifier"}
        prompt = ChatPromptTemplate.from_template(classifier_prompt_row["template"])
        chain = prompt | self.llm | self.output_parser
        decision = chain.invoke({"input": user_input})
        response_time = get_current_timestamp()

        interaction_row = [self.llm_id, classifier_prompt_row["ID"], user_input, "", request_time, "input", decision, response_time, "empty", ""]
        self.dataloader.insert_interaction(interaction_row, self.llm_id)
        return decision.strip().lower()

    def reaction_router(self, user_input, response_type):
        decision = self.classifier(user_input)
        if "yes" in decision:
            return self.code_producer(user_input)
        else:
            return self.general_chat(user_input), "empty"

    def general_chat(self, user_input: str) -> str:
        request_time = get_current_timestamp()
        prompt_row = {
            "template": "你是一个QGIS地理信息系统助手。请用中文回答：{input}",
            "ID": "generalChat"
        }
        human_message = HumanMessage(content=prompt_row["template"].format(input=user_input))
        result = self.llm.invoke([human_message])
        response = self.output_parser.invoke(result)
        response_time = get_current_timestamp()

        interaction_row = [self.llm_id, prompt_row["ID"], user_input, "", request_time, "return", response, response_time, "empty", ""]
        self.dataloader.insert_interaction(interaction_row, self.llm_id)
        return response

    def code_producer(self, user_input: str):
        request_time = get_current_timestamp()
        prompt_row = {
            "template": "你是一个PyQGIS代码生成专家。根据以下用户需求生成PyQGIS Python代码，代码放在```python代码块中：{input}",
            "ID": "codeProducer"
        }
        human_message = HumanMessage(content=prompt_row["template"].format(input=user_input))
        result = self.llm.invoke([human_message])
        response = self.output_parser.invoke(result)
        response_time = get_current_timestamp()

        interaction_row = [self.llm_id, prompt_row["ID"], user_input, "", request_time, "return", response, response_time, "withCode", ""]
        interaction_id = self.dataloader.insert_interaction(interaction_row, self.llm_id)
        self.latest_interaction_id = interaction_id
        return response, "withCode"

    def response(self, user_input, response_type):
        return self.reaction_router(user_input, response_type)

    def async_response(self, user_input, response_type):
        worker = ResponseWorker(self, user_input, response_type)
        worker.signals.finished.connect(
            lambda resp, workflow: self.response_ready.emit(user_input, response_type, resp, workflow)
        )
        worker.signals.error.connect(self.error_signal.emit)
        self.threadpool.start(worker)

    def async_reflect(self, log_message, executed_code, response_type="code"):
        worker = ReflectWorker(self, executed_code, log_message, response_type)
        worker.signals.finished.connect(
            lambda resp, workflow: self.reflection_ready.emit(log_message, response_type, resp, workflow)
        )
        worker.signals.error.connect(self.error_signal.emit)
        self.threadpool.start(worker)

    def reflect(self, log_message, executed_code, response_type="code"):
        try:
            request_time = get_current_timestamp()
            latest_row = self.dataloader.select_latest_interaction(self.llm_id, self.latest_interaction_id)
            latest_interaction = pack(latest_row, "interaction")
            user_input = latest_interaction["requestText"]
            ai_response = latest_interaction["responseText"]

            prompt = f"""
            你生成的PyQGIS代码执行时出错。请分析错误并修复代码。

            原始需求: {user_input}
            生成的代码: {ai_response}
            实际执行的代码: {executed_code}
            错误信息: {log_message}

            请提供修正后的代码，放在```python代码块中。
            """
            human_message = HumanMessage(content=prompt)
            result = self.llm.invoke([human_message])
            response = self.output_parser.invoke(result)
            response_time = get_current_timestamp()

            interaction_row = [self.llm_id, "codeProducer", user_input, prompt, request_time, "return", response, response_time, "withCode", ""]
            interaction_id = self.dataloader.insert_interaction(interaction_row, self.llm_id)
            self.latest_interaction_id = interaction_id
            return response, "withCode"
        except Exception as e:
            return f"修正失败: {str(e)}", "empty"
