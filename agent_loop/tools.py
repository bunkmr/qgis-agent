# -*- coding: utf-8 -*-
"""
工具注册系统 — 可扩展的工具管理。

支持动态注册、参数验证、执行权限控制。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from datetime import datetime

from .state import ToolResult


@dataclass
class Tool:
    """
    工具定义 — 封装工具的元数据和执行逻辑。

    使用示例:
        tool = Tool(
            name="get_qgis_info",
            description="获取 QGIS 基本信息",
            parameters={...},
            handler=my_handler,
        )
    """
    name: str                        # 工具名称（唯一标识）
    description: str                 # 工具描述（给 LLM 看）
    parameters: dict                 # JSON Schema 格式的参数定义
    handler: Callable[..., Any]      # 实际执行函数

    # 元数据
    category: str = "general"        # 工具分类
    requires_confirm: bool = False   # 是否需要用户确认
    dangerous: bool = False          # 是否是危险工具

    # 执行统计
    call_count: int = 0
    total_duration_ms: float = 0
    error_count: int = 0

    @property
    def avg_duration_ms(self) -> float:
        """平均执行时间"""
        if self.call_count == 0:
            return 0
        return self.total_duration_ms / self.call_count

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.call_count == 0:
            return 1.0
        return 1.0 - (self.error_count / self.call_count)

    def to_langchain_schema(self) -> dict:
        """转换为 LangChain 工具调用格式"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def execute(self, **kwargs) -> ToolResult:
        """
        执行工具。

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        start_time = datetime.now()

        try:
            output = self.handler(**kwargs)
            duration = (datetime.now() - start_time).total_seconds() * 1000

            self.call_count += 1
            self.total_duration_ms += duration

            return ToolResult(
                success=True,
                output=output,
                metadata={"duration_ms": duration},
            )
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000

            self.call_count += 1
            self.total_duration_ms += duration
            self.error_count += 1

            return ToolResult(
                success=False,
                output=None,
                error=str(e),
                metadata={"duration_ms": duration, "exception_type": type(e).__name__},
            )


class ToolRegistry:
    """
    工具注册表 — 管理所有可用工具。

    支持:
    - 动态注册/注销工具
    - 按分类过滤工具
    - 获取 LangChain 格式的工具定义
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._categories: dict[str, list[str]] = {}

    def register(self, tool: Tool):
        """
        注册工具。

        Args:
            tool: 工具实例

        Raises:
            ValueError: 如果工具名已存在
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")

        self._tools[tool.name] = tool

        # 更新分类索引
        if tool.category not in self._categories:
            self._categories[tool.category] = []
        self._categories[tool.category].append(tool.name)

    def unregister(self, name: str):
        """注销工具"""
        if name not in self._tools:
            return

        tool = self._tools.pop(name)
        if tool.category in self._categories:
            self._categories[tool.category].remove(name)

    def get(self, name: str) -> Optional[Tool]:
        """获取工具"""
        return self._tools.get(name)

    def get_by_category(self, category: str) -> list[Tool]:
        """获取指定分类的工具"""
        tool_names = self._categories.get(category, [])
        return [self._tools[name] for name in tool_names if name in self._tools]

    def get_all(self) -> list[Tool]:
        """获取所有工具"""
        return list(self._tools.values())

    def get_langchain_definitions(self) -> list[dict]:
        """获取所有工具的 LangChain 格式定义"""
        return [tool.to_langchain_schema() for tool in self._tools.values()]

    def get_statistics(self) -> dict:
        """获取工具使用统计"""
        stats = {
            "total_tools": len(self._tools),
            "categories": list(self._categories.keys()),
            "tools": {}
        }

        for name, tool in self._tools.items():
            stats["tools"][name] = {
                "call_count": tool.call_count,
                "error_count": tool.error_count,
                "avg_duration_ms": tool.avg_duration_ms,
                "success_rate": tool.success_rate,
            }

        return stats

    def clear(self):
        """清空所有工具"""
        self._tools.clear()
        self._categories.clear()


# 全局单例
_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """获取全局工具注册表"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def reset_tool_registry():
    """重置全局工具注册表（用于测试）"""
    global _registry
    _registry = None
