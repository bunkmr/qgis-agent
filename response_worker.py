import sqlite3
import traceback
from qgis.PyQt.QtCore import QRunnable, pyqtSignal, QObject


class WorkerSignals(QObject):
    finished = pyqtSignal(object, object)
    error = pyqtSignal(object)


class StreamWorkerSignals(QObject):
    """流式 Worker 信号，增加 thinking 信号用于实时显示"""
    finished = pyqtSignal(object, object)
    thinking = pyqtSignal(str)
    error = pyqtSignal(object)


class ToolAgentSignals(QObject):
    """Agent 工具调用 Worker 信号"""
    finished = pyqtSignal(object, object)
    thinking = pyqtSignal(str)
    tool_status = pyqtSignal(str)
    error = pyqtSignal(object)


class ResponseWorker(QRunnable):
    def __init__(self, processor, user_input, response_type):
        super().__init__()
        self.processor = processor
        self.user_input = user_input
        self.response_type = response_type
        self.signals = WorkerSignals()
        self._main_connection = processor.dataloader.connection
        self._main_cursor = processor.dataloader.cursor

    def run(self):
        try:
            # 在工作线程中创建独立的数据库连接，避免跨线程冲突
            worker_conn = sqlite3.connect(
                self.processor.dataloader.database_path, check_same_thread=False
            )
            self.processor.dataloader.connection = worker_conn
            self.processor.dataloader.cursor = worker_conn.cursor()
            response, workflow = self.processor.response(self.user_input, self.response_type)
            self.signals.finished.emit(response, workflow)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            # 恢复主线程的数据库连接
            if self.processor.dataloader.connection:
                self.processor.dataloader.connection.close()
            self.processor.dataloader.connection = self._main_connection
            self.processor.dataloader.cursor = self._main_cursor


class ResponseStreamWorker(QRunnable):
    """流式响应 Worker：实时发送思考内容到主线程"""
    def __init__(self, processor, user_input, response_type):
        super().__init__()
        self.processor = processor
        self.user_input = user_input
        self.response_type = response_type
        self.signals = StreamWorkerSignals()
        self._main_connection = processor.dataloader.connection
        self._main_cursor = processor.dataloader.cursor

    def run(self):
        try:
            worker_conn = sqlite3.connect(
                self.processor.dataloader.database_path, check_same_thread=False
            )
            self.processor.dataloader.connection = worker_conn
            self.processor.dataloader.cursor = worker_conn.cursor()

            def on_thinking(text):
                self.signals.thinking.emit(text)

            response, workflow = self.processor.response_stream(self.user_input, self.response_type, on_thinking)
            self.signals.finished.emit(response, workflow)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            if self.processor.dataloader.connection:
                self.processor.dataloader.connection.close()
            self.processor.dataloader.connection = self._main_connection
            self.processor.dataloader.cursor = self._main_cursor


class ReflectWorker(QRunnable):
    def __init__(self, processor, executed_code, log_message, response_type):
        super().__init__()
        self.processor = processor
        self.log_message = log_message
        self.executed_code = executed_code
        self.response_type = response_type
        self.signals = WorkerSignals()
        self._main_connection = processor.dataloader.connection
        self._main_cursor = processor.dataloader.cursor

    def run(self):
        try:
            # 在工作线程中创建独立的数据库连接，避免跨线程冲突
            worker_conn = sqlite3.connect(
                self.processor.dataloader.database_path, check_same_thread=False
            )
            self.processor.dataloader.connection = worker_conn
            self.processor.dataloader.cursor = worker_conn.cursor()
            response, workflow = self.processor.reflect(self.log_message, self.executed_code, self.response_type)
            self.signals.finished.emit(response, workflow)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            # 恢复主线程的数据库连接
            if self.processor.dataloader.connection:
                self.processor.dataloader.connection.close()
            self.processor.dataloader.connection = self._main_connection
            self.processor.dataloader.cursor = self._main_cursor


class ReflectStreamWorker(QRunnable):
    """流式反思 Worker：实时发送修正思考内容到主线程"""
    def __init__(self, processor, executed_code, log_message, response_type):
        super().__init__()
        self.processor = processor
        self.log_message = log_message
        self.executed_code = executed_code
        self.response_type = response_type
        self.signals = StreamWorkerSignals()
        self._main_connection = processor.dataloader.connection
        self._main_cursor = processor.dataloader.cursor

    def run(self):
        try:
            worker_conn = sqlite3.connect(
                self.processor.dataloader.database_path, check_same_thread=False
            )
            self.processor.dataloader.connection = worker_conn
            self.processor.dataloader.cursor = worker_conn.cursor()

            def on_thinking(text):
                self.signals.thinking.emit(text)

            response, workflow = self.processor.reflect_stream(self.log_message, self.executed_code, self.response_type, on_thinking)
            self.signals.finished.emit(response, workflow)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            if self.processor.dataloader.connection:
                self.processor.dataloader.connection.close()
            self.processor.dataloader.connection = self._main_connection
            self.processor.dataloader.cursor = self._main_cursor


class ToolAgentWorker(QRunnable):
    """Agent 工具调用 Worker：在后台线程中运行 Agent 对话循环"""
    def __init__(self, processor, user_input):
        super().__init__()
        self.processor = processor
        self.user_input = user_input
        self.signals = ToolAgentSignals()
        self._main_connection = processor.dataloader.connection
        self._main_cursor = processor.dataloader.cursor

    def run(self):
        try:
            worker_conn = sqlite3.connect(
                self.processor.dataloader.database_path, check_same_thread=False
            )
            self.processor.dataloader.connection = worker_conn
            self.processor.dataloader.cursor = worker_conn.cursor()

            def on_thinking(text):
                self.signals.thinking.emit(text)

            def on_tool_status(text):
                self.signals.tool_status.emit(text)

            response, workflow = self.processor.agent_chat(
                self.user_input,
                thinking_callback=on_thinking,
                tool_status_callback=on_tool_status,
            )

            self.signals.finished.emit(response, workflow)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            if self.processor.dataloader.connection:
                self.processor.dataloader.connection.close()
            self.processor.dataloader.connection = self._main_connection
            self.processor.dataloader.cursor = self._main_cursor
