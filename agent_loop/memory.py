# -*- coding: utf-8 -*-
"""
记忆系统 — 短期记忆 + 长期记忆管理。

短期记忆：当前对话的历史消息（SQLite 存储）
长期记忆：用户偏好、项目配置（MEMORY.md 文件）
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class MemoryEntry:
    """记忆条目"""
    content: str                     # 记忆内容
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


class ShortTermMemory:
    """
    短期记忆 — 当前对话的历史消息。

    存储在 SQLite 数据库中，每次对话加载最近 N 条消息。
    """

    def __init__(self, dataloader, conversation_id: str, max_messages: int = 20):
        """
        Args:
            dataloader: DataLoader 实例
            conversation_id: 对话 ID
            max_messages: 最大消息数
        """
        self.dataloader = dataloader
        self.conversation_id = conversation_id
        self.max_messages = max_messages
        self._messages: list[dict] = []

    def load(self) -> list[dict]:
        """
        加载历史消息。

        Returns:
            消息列表，格式: [{"role": "user"/"assistant", "content": "..."}]
        """
        try:
            history_rows = self.dataloader.select_interaction(self.conversation_id)
            if not history_rows:
                return []

            # 取最近 N 条
            recent_rows = history_rows[-self.max_messages:]
            messages = []

            for row in history_rows:
                if row.get("typeMessage") == "input":
                    messages.append({
                        "role": "user",
                        "content": row.get("requestText", ""),
                    })
                elif row.get("typeMessage") == "return":
                    messages.append({
                        "role": "assistant",
                        "content": row.get("responseText", ""),
                    })

            self._messages = messages
            return messages
        except Exception:
            return []

    def add_user_message(self, content: str):
        """添加用户消息"""
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str):
        """添加助手消息"""
        self._messages.append({"role": "assistant", "content": content})

    def get_messages(self) -> list[dict]:
        """获取所有消息"""
        return self._messages.copy()

    def clear(self):
        """清空消息"""
        self._messages.clear()


class LongTermMemory:
    """
    长期记忆 — 用户偏好、项目配置。

    存储在 MEMORY.md 文件中，跨对话持久化。
    """

    def __init__(self, memory_path: str, max_chars: int = 4000):
        """
        Args:
            memory_path: MEMORY.md 文件路径
            max_chars: 注入时的最大字符数
        """
        self.memory_path = memory_path
        self.max_chars = max_chars
        self._content: Optional[str] = None

    def load(self) -> str:
        """
        加载长期记忆。

        Returns:
            记忆内容字符串
        """
        try:
            if not os.path.exists(self.memory_path):
                return ""

            with open(self.memory_path, "r", encoding="utf-8") as f:
                content = f.read().strip()

            # 截断过长记忆
            if len(content) > self.max_chars:
                content = content[:self.max_chars] + "\n\n...(记忆过长已截断)"

            self._content = content
            return content
        except Exception:
            return ""

    def save(self, content: str, append: bool = True):
        """
        保存记忆。

        Args:
            content: 记忆内容
            append: 是否追加模式
        """
        try:
            os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)

            if append and os.path.exists(self.memory_path):
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    existing = f.read().strip()

                # 去重检查
                if content in existing:
                    return

                with open(self.memory_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n{content}")
            else:
                with open(self.memory_path, "w", encoding="utf-8") as f:
                    f.write(content)

            self._content = None  # 清除缓存
        except Exception:
            pass

    def has_content(self) -> bool:
        """是否有记忆内容"""
        if self._content is not None:
            return bool(self._content)
        return os.path.exists(self.memory_path) and os.path.getsize(self.memory_path) > 0


class MemoryManager:
    """
    记忆管理器 — 统一管理短期和长期记忆。

    提供简洁的 API 来加载和使用记忆上下文。
    """

    def __init__(self, dataloader, conversation_id: str, memory_path: str):
        """
        Args:
            dataloader: DataLoader 实例
            conversation_id: 对话 ID
            memory_path: MEMORY.md 文件路径
        """
        self.short_term = ShortTermMemory(dataloader, conversation_id)
        self.long_term = LongTermMemory(memory_path)

    def load_all(self) -> dict:
        """
        加载所有记忆。

        Returns:
            {
                "short_term": [...],
                "long_term": "...",
            }
        """
        return {
            "short_term": self.short_term.load(),
            "long_term": self.long_term.load(),
        }

    def get_context_for_llm(self) -> str:
        """
        获取注入到 LLM 的记忆上下文。

        Returns:
            格式化的记忆上下文字符串
        """
        parts = []

        # 长期记忆
        long_term = self.long_term.load()
        if long_term:
            parts.append(f"## 长期记忆\n{long_term}")

        return "\n\n".join(parts)

    def save_from_tool_result(self, tool_name: str, arguments: dict, result):
        """
        从工具执行结果中提取并保存记忆。

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            result: 工具执行结果
        """
        if tool_name == "save_memory" and result.success:
            content = arguments.get("content", "")
            if content:
                self.long_term.save(content)
