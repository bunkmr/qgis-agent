# -*- coding: utf-8 -*-
"""
Agent Loop 架构模块 — 基于 Loop Engineering 思想的重构。

核心组件:
- state: 状态管理（AgentState, StepResult, ToolCall）
- tools: 工具注册系统（ToolRegistry, Tool, ToolResult）
- memory: 记忆系统（MemoryManager, ShortTermMemory, LongTermMemory）
- loop: 核心循环（AgentLoop）
- rag: RAG 增强（RAGEngine）
"""

# 先导入不依赖外部库的模块
from .state import AgentState, StepResult, ToolCall, LoopStatus
from .tools import Tool, ToolResult, ToolRegistry, get_tool_registry

# 延迟导入依赖外部库的模块
__all__ = [
    "AgentState", "StepResult", "ToolCall", "LoopStatus",
    "Tool", "ToolResult", "ToolRegistry", "get_tool_registry",
]

def __getattr__(name):
    """延迟导入"""
    if name == "MemoryManager":
        from .memory import MemoryManager
        return MemoryManager
    elif name == "ShortTermMemory":
        from .memory import ShortTermMemory
        return ShortTermMemory
    elif name == "LongTermMemory":
        from .memory import LongTermMemory
        return LongTermMemory
    elif name == "AgentLoop":
        from .loop import AgentLoop
        return AgentLoop
    elif name == "RAGEngine":
        from .rag import RAGEngine
        return RAGEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
