# -*- coding: utf-8 -*-
"""
Agent Loop 单元测试

测试 Agent Loop 架构的核心组件:
- state: 状态管理
- tools: 工具注册系统
"""

import sys
import os
import unittest
from datetime import datetime

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 只导入不依赖 QGIS 和 langchain 的模块
from agent_loop.state import AgentState, StepResult, ToolCall, ToolResult, LoopStatus
from agent_loop.tools import Tool, ToolResult, ToolRegistry, get_tool_registry, reset_tool_registry


class TestToolResult(unittest.TestCase):
    """测试 ToolResult 数据类"""

    def test_success_result(self):
        result = ToolResult(success=True, output="test output")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "test output")
        self.assertIsNone(result.error)

    def test_error_result(self):
        result = ToolResult(success=False, output=None, error="test error")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "test error")


class TestToolCall(unittest.TestCase):
    """测试 ToolCall 数据类"""

    def test_creation(self):
        call = ToolCall(
            id="test_id",
            tool_name="test_tool",
            arguments={"key": "value"},
        )
        self.assertEqual(call.id, "test_id")
        self.assertEqual(call.tool_name, "test_tool")
        self.assertEqual(call.arguments, {"key": "value"})
        self.assertIsNone(call.result)

    def test_with_result(self):
        result = ToolResult(success=True, output="output")
        call = ToolCall(
            id="test_id",
            tool_name="test_tool",
            arguments={},
            result=result,
        )
        self.assertEqual(call.result, result)


class TestStepResult(unittest.TestCase):
    """测试 StepResult 数据类"""

    def test_creation(self):
        step = StepResult(step_number=1, status=LoopStatus.THINKING)
        self.assertEqual(step.step_number, 1)
        self.assertEqual(step.status, LoopStatus.THINKING)
        self.assertFalse(step.has_tool_calls)

    def test_with_tool_calls(self):
        call = ToolCall(id="1", tool_name="test", arguments={})
        step = StepResult(step_number=1, status=LoopStatus.TOOL_CALLING, tool_calls=[call])
        self.assertTrue(step.has_tool_calls)
        self.assertEqual(len(step.tool_calls), 1)


class TestAgentState(unittest.TestCase):
    """测试 AgentState 数据类"""

    def test_creation(self):
        state = AgentState(
            conversation_id="conv_1",
            user_input="test input",
        )
        self.assertEqual(state.conversation_id, "conv_1")
        self.assertEqual(state.user_input, "test input")
        self.assertEqual(state.status, LoopStatus.IDLE)
        self.assertEqual(state.current_step, 0)

    def test_add_step(self):
        state = AgentState(conversation_id="conv_1", user_input="test")
        step = StepResult(step_number=1, status=LoopStatus.THINKING)
        state.add_step(step)
        self.assertEqual(state.current_step, 1)
        self.assertEqual(len(state.steps), 1)

    def test_get_tool_calls_history(self):
        state = AgentState(conversation_id="conv_1", user_input="test")
        call1 = ToolCall(id="1", tool_name="tool1", arguments={})
        call2 = ToolCall(id="2", tool_name="tool2", arguments={})

        step1 = StepResult(step_number=1, status=LoopStatus.TOOL_CALLING, tool_calls=[call1])
        step2 = StepResult(step_number=2, status=LoopStatus.TOOL_CALLING, tool_calls=[call2])

        state.add_step(step1)
        state.add_step(step2)

        history = state.get_tool_calls_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].tool_name, "tool1")
        self.assertEqual(history[1].tool_name, "tool2")

    def test_to_dict(self):
        state = AgentState(conversation_id="conv_1", user_input="test")
        d = state.to_dict()
        self.assertEqual(d["conversation_id"], "conv_1")
        self.assertEqual(d["user_input"], "test")
        self.assertEqual(d["status"], "idle")


class TestTool(unittest.TestCase):
    """测试 Tool 数据类"""

    def test_creation(self):
        def handler(x):
            return x * 2

        tool = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
            handler=handler,
        )
        self.assertEqual(tool.name, "test_tool")
        self.assertEqual(tool.description, "A test tool")

    def test_execute_success(self):
        def handler(x):
            return x * 2

        tool = Tool(
            name="double",
            description="Doubles input",
            parameters={},
            handler=handler,
        )

        result = tool.execute(x=5)
        self.assertTrue(result.success)
        self.assertEqual(result.output, 10)
        self.assertEqual(tool.call_count, 1)

    def test_execute_error(self):
        def handler():
            raise ValueError("test error")

        tool = Tool(
            name="failing",
            description="Always fails",
            parameters={},
            handler=handler,
        )

        result = tool.execute()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "test error")
        self.assertEqual(tool.error_count, 1)

    def test_statistics(self):
        def handler():
            return "ok"

        tool = Tool(name="test", description="test", parameters={}, handler=handler)
        tool.execute()
        tool.execute()

        self.assertEqual(tool.call_count, 2)
        self.assertEqual(tool.error_count, 0)
        self.assertEqual(tool.success_rate, 1.0)

    def test_to_langchain_schema(self):
        tool = Tool(
            name="test",
            description="test tool",
            parameters={"type": "object"},
            handler=lambda: None,
        )
        schema = tool.to_langchain_schema()
        self.assertEqual(schema["name"], "test")
        self.assertEqual(schema["description"], "test tool")


class TestToolRegistry(unittest.TestCase):
    """测试 ToolRegistry"""

    def setUp(self):
        self.registry = ToolRegistry()

    def test_register_tool(self):
        tool = Tool(
            name="test",
            description="test",
            parameters={},
            handler=lambda: None,
        )
        self.registry.register(tool)
        self.assertEqual(self.registry.get("test"), tool)

    def test_register_duplicate_raises(self):
        tool = Tool(name="test", description="test", parameters={}, handler=lambda: None)
        self.registry.register(tool)
        with self.assertRaises(ValueError):
            self.registry.register(tool)

    def test_unregister_tool(self):
        tool = Tool(name="test", description="test", parameters={}, handler=lambda: None)
        self.registry.register(tool)
        self.registry.unregister("test")
        self.assertIsNone(self.registry.get("test"))

    def test_get_by_category(self):
        tool1 = Tool(name="t1", description="t1", parameters={}, handler=lambda: None, category="cat1")
        tool2 = Tool(name="t2", description="t2", parameters={}, handler=lambda: None, category="cat1")
        tool3 = Tool(name="t3", description="t3", parameters={}, handler=lambda: None, category="cat2")

        self.registry.register(tool1)
        self.registry.register(tool2)
        self.registry.register(tool3)

        cat1_tools = self.registry.get_by_category("cat1")
        self.assertEqual(len(cat1_tools), 2)

        cat2_tools = self.registry.get_by_category("cat2")
        self.assertEqual(len(cat2_tools), 1)

    def test_get_all(self):
        for i in range(5):
            tool = Tool(name=f"t{i}", description=f"t{i}", parameters={}, handler=lambda: None)
            self.registry.register(tool)

        all_tools = self.registry.get_all()
        self.assertEqual(len(all_tools), 5)

    def test_get_langchain_definitions(self):
        tool = Tool(
            name="test",
            description="test tool",
            parameters={"type": "object"},
            handler=lambda: None,
        )
        self.registry.register(tool)

        defs = self.registry.get_langchain_definitions()
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0]["name"], "test")

    def test_statistics(self):
        tool = Tool(name="test", description="test", parameters={}, handler=lambda: None)
        self.registry.register(tool)
        tool.execute()

        stats = self.registry.get_statistics()
        self.assertEqual(stats["total_tools"], 1)
        self.assertEqual(stats["tools"]["test"]["call_count"], 1)

    def test_clear(self):
        for i in range(3):
            tool = Tool(name=f"t{i}", description=f"t{i}", parameters={}, handler=lambda: None)
            self.registry.register(tool)

        self.registry.clear()
        self.assertEqual(len(self.registry.get_all()), 0)


class TestGlobalRegistry(unittest.TestCase):
    """测试全局注册表单例"""

    def setUp(self):
        reset_tool_registry()

    def tearDown(self):
        reset_tool_registry()

    def test_singleton(self):
        reg1 = get_tool_registry()
        reg2 = get_tool_registry()
        self.assertIs(reg1, reg2)

    def test_reset(self):
        reg1 = get_tool_registry()
        reset_tool_registry()
        reg2 = get_tool_registry()
        self.assertIsNot(reg1, reg2)


if __name__ == "__main__":
    unittest.main()
