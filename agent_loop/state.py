# -*- coding: utf-8 -*-
"""
状态管理模块 — Agent Loop 的核心状态机。

定义了 Agent 执行过程中的所有状态和数据结构。
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


class LoopStatus(Enum):
    """Agent 循环状态"""
    IDLE = "idle"                    # 空闲，等待用户输入
    THINKING = "thinking"            # LLM 正在思考
    TOOL_CALLING = "tool_calling"    # 正在执行工具
    WAITING_CONFIRM = "waiting_confirm"  # 等待用户确认
    COMPLETED = "completed"          # 任务完成
    ERROR = "error"                  # 出错
    CANCELLED = "cancelled"          # 用户取消


@dataclass
class ToolCall:
    """工具调用记录"""
    id: str                          # 唯一标识
    tool_name: str                   # 工具名称
    arguments: dict                  # 工具参数
    timestamp: datetime = field(default_factory=datetime.now)

    # 执行结果（执行后填充）
    result: Optional['ToolResult'] = None
    duration_ms: Optional[float] = None


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool                    # 是否成功
    output: Any                      # 输出内容
    error: Optional[str] = None      # 错误信息（如果失败）
    metadata: dict = field(default_factory=dict)  # 额外元数据


@dataclass
class StepResult:
    """单步执行结果"""
    step_number: int                 # 步骤编号
    status: LoopStatus               # 步骤状态

    # LLM 输出
    llm_response: Optional[str] = None     # LLM 文本回复
    tool_calls: list[ToolCall] = field(default_factory=list)  # 工具调用列表

    # RAG 上下文
    rag_context: Optional[str] = None      # RAG 检索结果

    # 时间戳
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    @property
    def has_tool_calls(self) -> bool:
        """是否有工具调用"""
        return len(self.tool_calls) > 0

    @property
    def duration_ms(self) -> Optional[float]:
        """步骤耗时（毫秒）"""
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return None


@dataclass
class AgentState:
    """
    Agent 状态 — 完整的执行上下文。

    在 Agent Loop 的每次迭代中，状态会不断更新。
    """
    # 基本信息
    conversation_id: str             # 对话 ID
    user_input: str                  # 用户输入

    # 循环控制
    status: LoopStatus = LoopStatus.IDLE
    current_step: int = 0
    max_steps: int = 10              # 最大步数

    # 历史记录
    steps: list[StepResult] = field(default_factory=list)

    # LLM 上下文
    messages: list = field(default_factory=list)  # LangChain 消息列表
    system_prompt: str = ""          # 系统提示词

    # RAG 上下文
    rag_context: str = ""            # 当前 RAG 检索结果

    # 记忆
    memory_context: str = ""         # 长期记忆内容
    cookbook_context: str = ""        # 相似案例

    # 最终结果
    final_response: Optional[str] = None

    # 元数据
    metadata: dict = field(default_factory=dict)

    def add_step(self, step: StepResult):
        """添加一个步骤结果"""
        self.steps.append(step)
        self.current_step = step.step_number

    def get_tool_calls_history(self) -> list[ToolCall]:
        """获取所有工具调用历史"""
        history = []
        for step in self.steps:
            history.extend(step.tool_calls)
        return history

    def get_last_step(self) -> Optional[StepResult]:
        """获取最后一个步骤"""
        return self.steps[-1] if self.steps else None

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            "conversation_id": self.conversation_id,
            "user_input": self.user_input,
            "status": self.status.value,
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "steps_count": len(self.steps),
            "final_response": self.final_response,
            "metadata": self.metadata,
        }
