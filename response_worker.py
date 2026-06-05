import traceback
from qgis.PyQt.QtCore import QRunnable, pyqtSignal, QObject


class WorkerSignals(QObject):
    finished = pyqtSignal(object, object)
    error = pyqtSignal(object)


class ResponseWorker(QRunnable):
    def __init__(self, processor, user_input, response_type):
        super().__init__()
        self.processor = processor
        self.user_input = user_input
        self.response_type = response_type
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.processor.dataloader.connect()
            response, workflow = self.processor.response(self.user_input, self.response_type)
            self.signals.finished.emit(response, workflow)
            self.processor.dataloader.close()
        except Exception as e:
            self.signals.error.emit(traceback.format_exc())


class ReflectWorker(QRunnable):
    def __init__(self, processor, executed_code, log_message, response_type):
        super().__init__()
        self.processor = processor
        self.log_message = log_message
        self.executed_code = executed_code
        self.response_type = response_type
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.processor.dataloader.connect()
            response, workflow = self.processor.reflect(self.log_message, self.executed_code, self.response_type)
            self.signals.finished.emit(response, workflow)
            self.processor.dataloader.close()
        except Exception as e:
            self.signals.error.emit(traceback.format_exc())
