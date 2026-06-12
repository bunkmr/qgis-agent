# -*- coding: utf-8 -*-
"""
新版本 Processor — 基于 Agent Loop 架构。

替代原有的 processor.py，提供更清晰的架构和更好的可维护性。
"""

import logging
from qgis.PyQt.QtCore import QThreadPool, pyqtSignal, QObject

from ..llm_providers import get_llm_instance
from ..utils import get_current_timestamp, pack
from ..response_worker import ToolAgentWorker
from ..qgis_tools import TOOL_DEFINITIONS, call_tool

from .loop import AgentLoop
from .tools import get_tool_registry
from .qgis_adapter import register_qgis_tools

logger = logging.getLogger(__name__)


class AgentLoopProcessor(QObject):
    """
    Agent Loop 处理器 — 基于新架构的处理器。

    与原 Processor 相比:
    1. 更清晰的状态管理
    2. 可扩展的工具注册系统
    3. 集成的 RAG 引擎
    4. 更好的错误处理
    """

    response_ready = pyqtSignal(str, str, str, str)
    reflection_ready = pyqtSignal(str, str, str, str)
    thinking = pyqtSignal(str)
    tool_status = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, llm_id: str, conversation_id: str, dataloader, temperature: float = 0.0):
        super().__init__()
        self.llm_id = llm_id
        self.conversation_id = conversation_id
        self.dataloader = dataloader
        self.temperature = temperature
        self.max_tool_rounds = 10
        self._cancelled = False
        self._code_confirm_callback = None

        # 初始化工具注册表
        self._init_tools()

        # RAG 组件
        self._init_rag()

    def _init_tools(self):
        """初始化工具注册表"""
        from ..qgis_tools import _init_main_thread_bridge

        # 确保主线程桥接器已初始化
        _init_main_thread_bridge()

        # 注册所有 QGIS 工具
        register_qgis_tools()

    def _init_rag(self):
        """初始化 RAG 组件"""
        try:
            from ..rag import DocStore, init_retriever, generate_pyqgis_docs

            self.doc_store = DocStore()
            init_retriever(self.doc_store)

            # 检查索引是否需要构建
            stats = self.doc_store.get_stats()
            if stats["api_docs"] == 0:
                logger.info("API 文档索引为空，将在后台构建")
        except Exception as e:
            logger.warning(f"RAG 初始化失败: {e}")

    def cancel(self):
        """取消当前执行"""
        self._cancelled = True
        self.threadpool.clear() if hasattr(self, 'threadpool') else None

    def set_code_confirm_callback(self, callback):
        """设置代码确认回调"""
        self._code_confirm_callback = callback

    def agent_chat(self, user_input: str, thinking_callback=None, tool_status_callback=None) -> tuple:
        """
        Agent 对话 — 使用新的 Agent Loop 架构。

        Args:
            user_input: 用户输入
            thinking_callback: 思考回调
            tool_status_callback: 工具状态回调

        Returns:
            (最终回复, workflow_tag)
        """
        import os
        from qgis.core import QgsApplication

        # 获取记忆路径
        memory_path = os.path.join(
            QgsApplication.qgisSettingsDirPath(),
            "python", "plugins", "qgis_agent", "MEMORY.md"
        )

        # 创建 Agent Loop
        loop = AgentLoop(
            llm_id=self.llm_id,
            conversation_id=self.conversation_id,
            dataloader=self.dataloader,
            memory_path=memory_path,
            temperature=self.temperature,
            max_steps=self.max_tool_rounds,
        )

        # 设置回调
        loop.set_callbacks(
            thinking_callback=thinking_callback,
            tool_status_callback=tool_status_callback,
            code_confirm_callback=self._code_confirm_callback,
        )

        # 运行循环
        return loop.run(user_input)

    def async_response(self, user_input: str):
        """异步响应入口"""
        from ..response_worker import ToolAgentWorker

        worker = ToolAgentWorker(self, user_input)
        worker.signals.finished.connect(self._on_worker_finished)
        worker.signals.error.connect(self._on_worker_error)

        self.threadpool = QThreadPool()
        self.threadpool.start(worker)

    def _on_worker_finished(self, result):
        """Worker 完成回调"""
        response, workflow, model_path = result
        self.response_ready.emit(response, workflow, model_path, get_current_timestamp())

    def _on_worker_error(self, error):
        """Worker 错误回调"""
        self.error_signal.emit(str(error))

    def get_statistics(self) -> dict:
        """获取处理器统计信息"""
        registry = get_tool_registry()
        return {
            "tools": registry.get_statistics(),
            "llm_id": self.llm_id,
            "temperature": self.temperature,
        }
