# -*- coding: utf-8 -*-
"""
核心循环模块 — Agent Loop 的主引擎。

实现 observe → think → act → observe 的循环模式。
"""

import json
import logging
from typing import Callable, Optional
from datetime import datetime

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from ..llm_providers import get_llm_instance
from ..utils import get_current_timestamp
from .state import AgentState, StepResult, ToolCall, LoopStatus
from .tools import Tool, ToolResult, ToolRegistry, get_tool_registry
from .memory import MemoryManager
from .rag import RAGEngine

logger = logging.getLogger(__name__)


# 默认系统提示词
DEFAULT_SYSTEM_PROMPT = """你是一个 QGIS 地理信息系统智能助手，运行在 QGIS 桌面版内部。

## 你的能力
你可以通过调用工具直接操作 QGIS，包括：
- 查看当前项目状态（图层列表、坐标系等）
- 添加/移除矢量图层和栅格图层
- 查看图层属性表和要素数据
- 缩放到指定图层
- 设置图层标注（Labeling）
- 执行 QGIS Processing 处理算法（缓冲区、裁剪、相交、字段计算等）
- 直接执行 PyQGIS 代码完成复杂操作
- 保存/加载项目文件
- 渲染地图为图片
- 检索 PyQGIS API 文档（search_pyqgis_api）

## 工作方式
1. 收到用户请求后，先调用 get_qgis_info 了解当前 QGIS 项目状态
2. 在执行 execute_pyqgis 或 execute_processing 之前，**强烈建议先调用 search_pyqgis_api 检索相关 API 文档**
3. 根据需要调用其他工具执行操作
4. 每次工具调用后，根据返回结果决定下一步
5. 最终向用户汇报操作结果

## 重要规则
- 始终用中文回复用户
- 操作文件时使用绝对路径
- 执行操作前确认图层存在
- 如果工具返回错误，分析原因并尝试修复
- 对于复杂的多步骤任务，逐步执行并汇报进度
"""


class AgentLoop:
    """
    Agent Loop — 核心执行引擎。

    实现了经典的 ReAct (Reasoning + Acting) 循环：
    1. Observe: 观察当前状态
    2. Think: LLM 决定下一步
    3. Act: 执行工具调用
    4. Observe: 观察执行结果

    支持:
    - 多轮工具调用
    - 流式思考反馈
    - 中断/取消
    - RAG 增强
    - 记忆管理
    """

    def __init__(
        self,
        llm_id: str,
        conversation_id: str,
        dataloader,
        memory_path: str,
        temperature: float = 0.0,
        max_steps: int = 10,
    ):
        """
        Args:
            llm_id: LLM 配置 ID
            conversation_id: 对话 ID
            dataloader: DataLoader 实例
            memory_path: MEMORY.md 文件路径
            temperature: 温度参数
            max_steps: 最大步数
        """
        # LLM 配置
        self.llm_id = llm_id
        self.model_name, self.endpoint, self.api_key = dataloader.fetch_llm_info(llm_id)
        self.provider = llm_id.split("::", 1)[0]
        self.temperature = temperature

        # 创建 LLM 实例
        self.llm = get_llm_instance(
            self.provider, self.model_name, self.api_key, self.endpoint,
            temperature=temperature
        )

        # 核心组件
        self.dataloader = dataloader
        self.conversation_id = conversation_id
        self.max_steps = max_steps

        # 记忆系统
        self.memory = MemoryManager(dataloader, conversation_id, memory_path)

        # RAG 引擎
        self.rag = RAGEngine()

        # 工具注册表
        self.tool_registry = get_tool_registry()

        # 状态
        self._state: Optional[AgentState] = None
        self._cancelled = False

        # 回调
        self._thinking_callback: Optional[Callable[[str], None]] = None
        self._tool_status_callback: Optional[Callable[[str], None]] = None
        self._code_confirm_callback: Optional[Callable[[str, str], bool]] = None

    def set_callbacks(
        self,
        thinking_callback: Optional[Callable[[str], None]] = None,
        tool_status_callback: Optional[Callable[[str], None]] = None,
        code_confirm_callback: Optional[Callable[[str, str], bool]] = None,
    ):
        """设置回调函数"""
        self._thinking_callback = thinking_callback
        self._tool_status_callback = tool_status_callback
        self._code_confirm_callback = code_confirm_callback

    def cancel(self):
        """取消当前执行"""
        self._cancelled = True

    def run(self, user_input: str) -> tuple[str, str]:
        """
        运行 Agent Loop。

        Args:
            user_input: 用户输入

        Returns:
            (最终回复, workflow_tag)
        """
        # 初始化状态
        self._state = AgentState(
            conversation_id=self.conversation_id,
            user_input=user_input,
            max_steps=self.max_steps,
        )
        self._cancelled = False

        # 构建上下文
        self._build_context()

        # 主循环
        final_response = ""
        workflow = "agent_loop"

        for step_num in range(1, self._state.max_steps + 1):
            # 检查中断
            if self._cancelled:
                final_response = "⏹ 用户中断了操作。"
                workflow = "cancelled"
                break

            # 创建步骤
            step = StepResult(step_number=step_num, status=LoopStatus.THINKING)
            self._state.add_step(step)

            # Think: LLM 推理
            if self._tool_status_callback:
                self._tool_status_callback(f"🔄 步骤 {step_num}: LLM 思考中...")

            try:
                llm_with_tools = self.llm.bind_tools(
                    self.tool_registry.get_langchain_definitions()
                )
                response = llm_with_tools.invoke(self._state.messages)
            except (AttributeError, TypeError, NotImplementedError):
                # 模型不支持 tool calling
                if self._thinking_callback:
                    self._thinking_callback("[思考中...]\n")
                response = self.llm.invoke(self._state.messages)
                final_response = response.content if hasattr(response, 'content') else str(response)
                if self._thinking_callback:
                    self._thinking_callback(final_response)
                step.status = LoopStatus.COMPLETED
                step.llm_response = final_response
                step.completed_at = datetime.now()
                break

            # 检查是否有工具调用
            if not response.tool_calls:
                # 没有工具调用，返回最终回复
                final_response = response.content if hasattr(response, 'content') else str(response)
                step.status = LoopStatus.COMPLETED
                step.llm_response = final_response
                step.completed_at = datetime.now()
                break

            # Act: 执行工具调用
            step.status = LoopStatus.TOOL_CALLING
            self._state.messages.append(response)

            for tool_call in response.tool_calls:
                # 检查中断
                if self._cancelled:
                    break

                # 创建工具调用记录
                call = ToolCall(
                    id=tool_call["id"],
                    tool_name=tool_call["name"],
                    arguments=tool_call["args"],
                )

                if self._tool_status_callback:
                    self._tool_status_callback(f"🔧 执行: {call.tool_name}")

                # RAG: 检索相关 API 文档
                rag_context = self.rag.search_for_tool_call(call.tool_name, call.arguments)
                if rag_context and self._thinking_callback:
                    self._thinking_callback(f"[RAG 检索到 {len(rag_context)} 字符的 API 文档]\n")

                # 执行工具
                tool_result = self._execute_tool(call)
                call.result = tool_result
                step.tool_calls.append(call)

                # 添加工具结果到消息
                self._state.messages.append(ToolMessage(
                    content=json.dumps(tool_result.output, ensure_ascii=False) if tool_result.success else f"Error: {tool_result.error}",
                    tool_call_id=call.id,
                ))

                # 保存记忆
                self.memory.save_from_tool_result(call.tool_name, call.arguments, tool_result)

            step.status = LoopStatus.COMPLETED
            step.completed_at = datetime.now()

        # 归档任务到 Cookbook
        tool_calls_history = self._state.get_tool_calls_history()
        if tool_calls_history:
            tool_names = [tc.tool_name for tc in tool_calls_history]
            code_snippet = self._extract_code_from_history(tool_calls_history)
            self.rag.archive_task(
                user_input=user_input,
                tool_calls=tool_names,
                code_snippet=code_snippet,
                success=True,
            )

        # 保存到数据库
        self._save_interaction(user_input, final_response, workflow)

        self._state.final_response = final_response
        self._state.status = LoopStatus.COMPLETED

        return final_response, workflow

    def _build_context(self):
        """构建 LLM 上下文"""
        # 系统提示词
        system_prompt = DEFAULT_SYSTEM_PROMPT

        # 加载记忆
        memory_context = self.memory.get_context_for_llm()
        if memory_context:
            system_prompt += f"\n\n{memory_context}"

        # 检索相似案例
        cookbook_context = self.rag.search_similar_tasks(self._state.user_input)
        if cookbook_context:
            system_prompt += f"\n\n{cookbook_context}"

        self._state.system_prompt = system_prompt
        self._state.messages = [SystemMessage(content=system_prompt)]

        # 加载历史消息
        history = self.memory.short_term.get_messages()
        for msg in history:
            if msg["role"] == "user":
                self._state.messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                self._state.messages.append(AIMessage(content=msg["content"]))

        # 添加当前用户输入
        self._state.messages.append(HumanMessage(content=self._state.user_input))

    def _execute_tool(self, call: ToolCall) -> ToolResult:
        """
        执行工具调用。

        Args:
            call: 工具调用记录

        Returns:
            ToolResult: 执行结果
        """
        tool = self.tool_registry.get(call.tool_name)
        if not tool:
            return ToolResult(
                success=False,
                output=None,
                error=f"Tool '{call.tool_name}' not found",
            )

        # 需要确认的工具
        if tool.requires_confirm and self._code_confirm_callback:
            code_preview = json.dumps(call.arguments, ensure_ascii=False, indent=2)
            if not self._code_confirm_callback(call.tool_name, code_preview):
                return ToolResult(
                    success=False,
                    output=None,
                    error="用户取消了操作",
                )

        # 执行工具
        return tool.execute(**call.arguments)

    def _extract_code_from_history(self, tool_calls: list[ToolCall]) -> str:
        """从工具调用历史中提取代码片段"""
        for call in tool_calls:
            if call.tool_name == "execute_pyqgis" and call.result and call.result.success:
                code = call.arguments.get("code", "")
                if code:
                    return code
        return ""

    def _save_interaction(self, user_input: str, response: str, workflow: str):
        """保存交互记录到数据库"""
        try:
            from ..utils import generate_unique_id

            interaction_id = f"{self.conversation_id}_{generate_unique_id()}"
            self.dataloader.insert_interaction({
                "ID": interaction_id,
                "conversationID": self.conversation_id,
                "promptID": "",
                "typeMessage": "input",
                "requestText": user_input,
                "responseText": "",
                "workflow": workflow,
                "executionLog": "",
                "created": get_current_timestamp(),
            })

            self.dataloader.insert_interaction({
                "ID": f"{interaction_id}_response",
                "conversationID": self.conversation_id,
                "promptID": "",
                "typeMessage": "return",
                "requestText": "",
                "responseText": response,
                "workflow": workflow,
                "executionLog": "",
                "created": get_current_timestamp(),
            })
        except Exception as e:
            logger.error(f"Failed to save interaction: {e}")

    @property
    def state(self) -> Optional[AgentState]:
        """获取当前状态"""
        return self._state
