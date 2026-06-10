# -*- coding: utf-8 -*-
"""
UI 增强模块 — 优化对话框显示。

提供改进的消息渲染、工具状态显示、Markdown 支持等功能。
"""

import html as html_module
from typing import Optional
from datetime import datetime


# 样式常量
class UIStyles:
    """UI 样式常量"""

    # 颜色
    USER_COLOR = "#6baad1"
    ASSISTANT_COLOR = "#FD8A8A"
    TOOL_COLOR = "#4A90D9"
    ERROR_COLOR = "#FF6B6B"
    SUCCESS_COLOR = "#66BB6A"

    # 背景色
    TOOL_BG = "#2d2d2d"
    ERROR_BG = "#3d2020"

    # 字体
    MONO_FONT = "Consolas, 'Courier New', monospace"
    DEFAULT_FONT = "Segoe UI, Arial, sans-serif"

    # 边框
    TOOL_BORDER_LEFT = f"3px solid {TOOL_COLOR}"
    ERROR_BORDER_LEFT = f"3px solid {ERROR_COLOR}"


def render_user_message(content: str, timestamp: str, font_color: str) -> str:
    """
    渲染用户消息。

    Args:
        content: 消息内容
        timestamp: 时间戳
        font_color: 字体颜色

    Returns:
        HTML 字符串
    """
    safe_content = html_module.escape(content)
    return f"""
    <div class="message user-message" style="margin:8px 0;padding:8px 12px;text-align:right;">
        <div style="margin:0 0 4px 0;font-size:11px;color:{UIStyles.USER_COLOR};">
            👤 用户 · {timestamp}
        </div>
        <div style="margin:0;color:{font_color};line-height:1.5;">
            {safe_content}
        </div>
    </div>
    """


def render_assistant_message(content: str, timestamp: str, font_color: str) -> str:
    """
    渲染助手消息（支持 Markdown）。

    Args:
        content: 消息内容
        timestamp: 时间戳
        font_color: 字体颜色

    Returns:
        HTML 字符串
    """
    from .utils import create_markdown

    rendered_content = create_markdown(content)
    return f"""
    <div class="message assistant-message" style="margin:8px 0;padding:8px 12px;">
        <div style="margin:0 0 4px 0;font-size:11px;color:{UIStyles.ASSISTANT_COLOR};">
            🤖 QGIS Agent · {timestamp}
        </div>
        <div style="margin:0;color:{font_color};line-height:1.5;">
            {rendered_content}
        </div>
    </div>
    """


def render_tool_status(tool_name: str, status: str, details: str = "") -> str:
    """
    渲染工具调用状态。

    Args:
        tool_name: 工具名称
        status: 状态文本
        details: 详细信息

    Returns:
        HTML 字符串
    """
    details_html = f'<div style="margin-top:4px;font-size:11px;color:#888;">{html_module.escape(details)}</div>' if details else ""

    return f"""
    <div class="tool-status" style="margin:4px 0;padding:6px 10px;background-color:{UIStyles.TOOL_BG};border-left:{UIStyles.TOOL_BORDER_LEFT};border-radius:4px;font-family:{UIStyles.MONO_FONT};font-size:12px;">
        <div style="color:{UIStyles.TOOL_COLOR};">🔧 {html_module.escape(tool_name)}</div>
        <div style="color:#b0b0b0;margin-top:2px;">{html_module.escape(status)}</div>
        {details_html}
    </div>
    """


def render_tool_result(tool_name: str, success: bool, output: str, duration_ms: float = 0) -> str:
    """
    渲染工具执行结果。

    Args:
        tool_name: 工具名称
        success: 是否成功
        output: 输出内容
        duration_ms: 执行时间（毫秒）

    Returns:
        HTML 字符串
    """
    status_color = UIStyles.SUCCESS_COLOR if success else UIStyles.ERROR_COLOR
    status_icon = "✅" if success else "❌"
    duration_text = f" · {duration_ms:.0f}ms" if duration_ms > 0 else ""

    # 截断过长输出
    if len(output) > 500:
        output = output[:500] + "..."

    safe_output = html_module.escape(output)

    return f"""
    <div class="tool-result" style="margin:2px 0 8px 0;padding:4px 10px;background-color:{UIStyles.TOOL_BG};border-left:3px solid {status_color};border-radius:4px;font-family:{UIStyles.MONO_FONT};font-size:11px;">
        <div style="color:{status_color};">{status_icon} {html_module.escape(tool_name)}{duration_text}</div>
        <div style="color:#999;margin-top:2px;white-space:pre-wrap;">{safe_output}</div>
    </div>
    """


def render_error_message(error: str) -> str:
    """
    渲染错误消息。

    Args:
        error: 错误信息

    Returns:
        HTML 字符串
    """
    return f"""
    <div class="error-message" style="margin:8px 0;padding:8px 12px;background-color:{UIStyles.ERROR_BG};border-left:{UIStyles.ERROR_BORDER_LEFT};border-radius:4px;">
        <div style="color:{UIStyles.ERROR_COLOR};font-weight:bold;">⚠️ 错误</div>
        <div style="color:#ff9999;margin-top:4px;">{html_module.escape(error)}</div>
    </div>
    """


def render_thinking(content: str, timestamp: str = "") -> str:
    """
    渲染思考中的内容（流式显示）。

    Args:
        content: 思考内容
        timestamp: 时间戳

    Returns:
        HTML 字符串
    """
    from .utils import create_markdown

    rendered_content = create_markdown(content) if content else ""
    time_text = f" · {timestamp}" if timestamp else ""

    return f"""
    <div class="thinking-message" style="margin:8px 0;padding:8px 12px;">
        <div style="margin:0 0 4px 0;font-size:11px;color:{UIStyles.ASSISTANT_COLOR};">
            🤖 QGIS Agent 思考中...{time_text}
        </div>
        <div style="color:#888;line-height:1.5;">
            {rendered_content}
            <span style="color:{UIStyles.ASSISTANT_COLOR};animation:blink 1s infinite;">▌</span>
        </div>
    </div>
    """


def render_cancellation() -> str:
    """渲染取消消息"""
    return """
    <div class="cancellation" style="margin:8px 0;padding:8px 12px;text-align:center;color:#888;">
        ⏹ 用户中断了操作
    </div>
    """


class MessageRenderer:
    """
    消息渲染器 — 管理聊天历史的 HTML 渲染。

    支持:
    - 增量更新（不重绘整个历史）
    - 流式显示
    - 工具状态显示
    """

    def __init__(self):
        self._messages: list[dict] = []
        self._streaming_html: str = ""

    def add_user_message(self, content: str, timestamp: str, font_color: str):
        """添加用户消息"""
        self._messages.append({
            "type": "user",
            "content": content,
            "timestamp": timestamp,
            "font_color": font_color,
        })
        self._streaming_html = ""

    def add_assistant_message(self, content: str, timestamp: str, font_color: str):
        """添加助手消息"""
        self._messages.append({
            "type": "assistant",
            "content": content,
            "timestamp": timestamp,
            "font_color": font_color,
        })
        self._streaming_html = ""

    def add_tool_status(self, tool_name: str, status: str, details: str = ""):
        """添加工具状态"""
        self._messages.append({
            "type": "tool_status",
            "tool_name": tool_name,
            "status": status,
            "details": details,
        })

    def add_tool_result(self, tool_name: str, success: bool, output: str, duration_ms: float = 0):
        """添加工具结果"""
        self._messages.append({
            "type": "tool_result",
            "tool_name": tool_name,
            "success": success,
            "output": output,
            "duration_ms": duration_ms,
        })

    def add_error(self, error: str):
        """添加错误消息"""
        self._messages.append({
            "type": "error",
            "error": error,
        })

    def render_full(self) -> str:
        """渲染完整历史"""
        html_parts = []
        for msg in self._messages:
            if msg["type"] == "user":
                html_parts.append(render_user_message(
                    msg["content"], msg["timestamp"], msg["font_color"]
                ))
            elif msg["type"] == "assistant":
                html_parts.append(render_assistant_message(
                    msg["content"], msg["timestamp"], msg["font_color"]
                ))
            elif msg["type"] == "tool_status":
                html_parts.append(render_tool_status(
                    msg["tool_name"], msg["status"], msg.get("details", "")
                ))
            elif msg["type"] == "tool_result":
                html_parts.append(render_tool_result(
                    msg["tool_name"], msg["success"], msg["output"], msg.get("duration_ms", 0)
                ))
            elif msg["type"] == "error":
                html_parts.append(render_error_message(msg["error"]))

        return "\n".join(html_parts)

    def render_streaming(self, content: str, timestamp: str = "") -> str:
        """渲染流式内容"""
        self._streaming_html = render_thinking(content, timestamp)
        return self.render_full() + self._streaming_html

    def clear_streaming(self):
        """清除流式内容"""
        self._streaming_html = ""

    def clear(self):
        """清空所有消息"""
        self._messages.clear()
        self._streaming_html = ""
