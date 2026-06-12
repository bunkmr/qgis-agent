# -*- coding: utf-8 -*-
"""
RAG 引擎 — 检索增强生成。

集成 API 文档检索和 Cookbook 案例检索，为 LLM 提供上下文增强。
"""

from typing import Optional
from ..rag import DocStore, APIDocRetriever, Cookbook


class RAGEngine:
    """
    RAG 引擎 — 统一管理 API 文档检索和 Cookbook 案例检索。

    核心功能:
    1. 在工具调用前检索相关 API 文档
    2. 在任务开始前检索相似历史案例
    3. 在任务完成后归档成功案例
    """

    def __init__(self, doc_store: Optional[DocStore] = None):
        """
        Args:
            doc_store: 文档存储实例（可选，默认创建新实例）
        """
        self.doc_store = doc_store or DocStore()
        self.retriever = APIDocRetriever(self.doc_store)
        self.cookbook = Cookbook(self.doc_store)

    def search_api_docs(self, query: str, top_k: int = 5) -> str:
        """
        搜索 API 文档。

        Args:
            query: 搜索查询
            top_k: 返回结果数量

        Returns:
            格式化的 API 文档上下文
        """
        try:
            results = self.retriever.search(query, top_k=top_k)
            if results:
                return self.retriever.format_as_context(results)
        except Exception:
            pass
        return ""

    def search_for_tool_call(self, tool_name: str, arguments: dict) -> str:
        """
        为工具调用搜索相关 API 文档。

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            格式化的 API 文档上下文
        """
        try:
            results = self.retriever.search_for_tool_call(tool_name, arguments)
            if results:
                return self.retriever.format_as_context(results, max_chars=3000)
        except Exception:
            pass
        return ""

    def search_similar_tasks(self, user_input: str, top_k: int = 2) -> str:
        """
        搜索相似历史任务。

        Args:
            user_input: 用户输入
            top_k: 返回结果数量

        Returns:
            格式化的相似案例上下文
        """
        try:
            results = self.cookbook.search_for_task(user_input, top_k=top_k)
            if results:
                return self.cookbook.format_as_context(results)
        except Exception:
            pass
        return ""

    def archive_task(self, user_input: str, tool_calls: list, code_snippet: str = "",
                     success: bool = True):
        """
        归档任务到 Cookbook。

        Args:
            user_input: 用户输入
            tool_calls: 工具调用列表
            code_snippet: 代码片段
            success: 是否成功
        """
        try:
            self.cookbook.archive_from_agent_result(
                user_input=user_input,
                tools_used=tool_calls,
                code_snippet=code_snippet,
                success=success,
            )
        except Exception:
            pass

    def get_stats(self) -> dict:
        """获取 RAG 统计信息"""
        try:
            return self.doc_store.get_stats()
        except Exception:
            return {"api_docs": 0, "cookbook_entries": 0}
