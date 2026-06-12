# -*- coding: utf-8 -*-
"""
思考内容显示模块

实现类似 Claude Code 的思考显示效果：
- 思考过程中实时显示内容
- 思考结束后可折叠/展开
"""

import html as html_module


def create_thinking_block(content: str, timestamp: str = "", is_final: bool = False) -> str:
    """
    创建可折叠的思考块

    使用 HTML <details> 标签实现折叠效果。

    Args:
        content: 思考内容
        timestamp: 时间戳
        is_final: 是否是最终状态（已折叠）

    Returns:
        HTML 字符串
    """
    safe_content = html_module.escape(content) if content else "&nbsp;"
    time_text = f" · {timestamp}" if timestamp else ""

    if is_final:
        # 最终状态：折叠
        details_open = ""
        header_color = "#666666"
        status_text = "💭 思考完成"
        hint_text = "点击展开"
    else:
        # 思考中：展开
        details_open = " open"
        header_color = "#6baad1"
        status_text = f"🧠 思考中...{time_text}"
        hint_text = "点击折叠"

    # 简化 HTML 结构，使用更兼容的样式
    html = f'''<div style="margin: 8px 0; padding: 0;">
<details{details_open}>
<summary style="cursor: pointer; padding: 8px 12px; background-color: #2d2d2d; border-left: 4px solid {header_color}; border-radius: 4px;"><span style="color: {header_color}; font-weight: bold;">{status_text}</span> <span style="color: #888; font-size: 11px;">[{hint_text}]</span></summary>
<div style="padding: 10px 12px; background-color: #1e1e1e; border-left: 4px solid #444; margin-top: 2px; min-height: 20px;"><pre style="color: #cccccc; font-size: 12px; line-height: 1.5; margin: 0; white-space: pre-wrap; word-wrap: break-word; font-family: Consolas, Monaco, monospace;">{safe_content}</pre></div>
</details>
</div>'''
    return html


def create_thinking_start(timestamp: str = "") -> str:
    """创建思考开始标记"""
    return f"<!-- THINKING_START {timestamp} -->"


def create_thinking_end() -> str:
    """创建思考结束标记"""
    return "<!-- THINKING_END -->"


def replace_thinking_content(html: str, old_content: str, new_content: str) -> str:
    """
    替换思考内容

    Args:
        html: 完整 HTML
        old_content: 旧内容
        new_content: 新内容

    Returns:
        更新后的 HTML
    """
    # 简单的字符串替换
    return html.replace(old_content, new_content)


class ThinkingManager:
    """
    思考内容管理器

    管理思考块的生命周期。
    """

    def __init__(self):
        self._thinking_id = 0
        self._current_content = ""
        self._history = []

    def start(self, timestamp: str = "") -> tuple[str, str]:
        """
        开始新的思考

        Args:
            timestamp: 时间戳

        Returns:
            (思考开始标记, 完整HTML)
        """
        self._thinking_id += 1
        self._current_content = ""

        # 生成思考块 HTML
        html = create_thinking_block("", timestamp, is_final=False)
        self._history.append(html)

        return html

    def update(self, content: str) -> str:
        """
        更新思考内容

        Args:
            content: 新内容

        Returns:
            更新后的 HTML
        """
        self._current_content = content

        # 重新生成最后一个思考块
        if self._history:
            self._history[-1] = create_thinking_block(content, is_final=False)

        return "".join(self._history)

    def finalize(self, title: str = "思考完成") -> str:
        """
        完成思考

        Args:
            title: 完成标题

        Returns:
            最终 HTML
        """
        if self._history:
            # 将最后一个思考块设为折叠状态
            self._history[-1] = create_thinking_block(
                self._current_content,
                is_final=True
            )

        return "".join(self._history)

    def get_history(self) -> str:
        """获取历史记录"""
        return "".join(self._history)

    def clear(self):
        """清空"""
        self._thinking_id = 0
        self._current_content = ""
        self._history.clear()
