# -*- coding: utf-8 -*-
"""
可折叠的思考内容组件

实现类似 Claude Code 的思考显示效果：
- 思考过程中实时显示内容
- 思考结束后可折叠/展开
- 美观的样式设计
"""

import html as html_module


class ThinkingWidget:
    """
    可折叠的思考内容管理器

    使用 HTML <details> 标签实现折叠效果，
    配合 JavaScript 实现实时更新。
    """

    # 样式常量
    THINKING_HEADER_COLOR = "#6baad1"
    THINKING_CONTENT_COLOR = "#888888"
    COLLAPSED_COLOR = "#666666"

    def __init__(self):
        self._thinking_id = 0
        self._current_html = ""

    def start_thinking(self, timestamp: str = "") -> str:
        """
        开始新的思考块

        Args:
            timestamp: 时间戳

        Returns:
            HTML 字符串
        """
        self._thinking_id += 1
        time_text = f" · {timestamp}" if timestamp else ""

        # 使用 <details> 标签实现折叠
        # open 属性表示默认展开
        html = f'''
        <div class="thinking-block" id="thinking-{self._thinking_id}" style="margin: 8px 0;">
            <details open>
                <summary style="cursor: pointer; padding: 6px 10px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 6px; border-left: 3px solid {self.THINKING_HEADER_COLOR};">
                    <span style="color: {self.THINKING_HEADER_COLOR}; font-weight: 500;">
                        🧠 思考中...{time_text}
                    </span>
                    <span style="color: #666; font-size: 11px; margin-left: 8px;">点击展开/折叠</span>
                </summary>
                <div class="thinking-content" style="padding: 10px 12px; background: #1a1a2e; border-radius: 0 0 6px 6px; border-left: 3px solid #333; margin-top: -1px;">
                    <div id="thinking-content-{self._thinking_id}" style="color: {self.THINKING_CONTENT_COLOR}; font-size: 13px; line-height: 1.6; white-space: pre-wrap; font-family: 'Consolas', 'Monaco', monospace;">
                    </div>
                </div>
            </details>
        </div>
        '''
        return html

    def update_content(self, content: str) -> str:
        """
        更新思考内容

        Args:
            content: 新的思考内容

        Returns:
            用于替换的 JavaScript 代码
        """
        # 转义 HTML 特殊字符
        safe_content = html_module.escape(content)

        # 返回 JavaScript 代码来更新内容
        js = f'''
        <script>
        (function() {{
            var contentDiv = document.getElementById('thinking-content-{self._thinking_id}');
            if (contentDiv) {{
                contentDiv.innerHTML = '{safe_content}'.replace(/\\n/g, '<br>');
                // 自动滚动到底部
                contentDiv.scrollTop = contentDiv.scrollHeight;
            }}
        }})();
        </script>
        '''
        return js

    def finalize_thinking(self, title: str = "思考完成") -> str:
        """
        完成思考，更新标题

        Args:
            title: 完成后的标题

        Returns:
            用于更新的 JavaScript 代码
        """
        js = f'''
        <script>
        (function() {{
            var details = document.getElementById('thinking-{self._thinking_id}');
            if (details) {{
                // 获取 summary 元素
                var summary = details.querySelector('summary');
                if (summary) {{
                    summary.innerHTML = '<span style="color: {self.COLLAPSED_COLOR};">💭 {title}</span><span style="color: #555; font-size: 11px; margin-left: 8px;">点击展开/折叠</span>';
                }}
                // 默认折叠
                details.removeAttribute('open');
            }}
        }})();
        </script>
        '''
        return js


class StreamingThinkingDisplay:
    """
    流式思考显示管理器

    管理聊天历史中的思考块显示。
    """

    def __init__(self):
        self._widget = ThinkingWidget()
        self._history_html = ""
        self._thinking_html = ""

    def add_user_message(self, content: str, timestamp: str, font_color: str) -> str:
        """添加用户消息"""
        safe_content = html_module.escape(content)
        msg = f'''
        <div style="margin: 8px 0; padding: 8px 12px; text-align: right;">
            <div style="margin: 0 0 4px 0; font-size: 11px; color: #6baad1;">
                👤 用户 · {timestamp}
            </div>
            <div style="margin: 0; color: {font_color}; line-height: 1.5;">
                {safe_content}
            </div>
        </div>
        '''
        self._history_html += msg
        self._thinking_html = ""
        return msg

    def start_thinking(self, timestamp: str = "") -> str:
        """开始思考"""
        self._thinking_html = self._widget.start_thinking(timestamp)
        return self._history_html + self._thinking_html

    def update_thinking(self, content: str) -> str:
        """更新思考内容"""
        js = self._widget.update_content(content)
        return self._history_html + self._thinking_html + js

    def finalize_thinking(self, title: str = "思考完成") -> str:
        """完成思考"""
        js = self._widget.finalize_thinking(title)
        result = self._history_html + self._thinking_html + js
        # 将思考块添加到历史记录（保持折叠状态）
        self._history_html += self._thinking_html.replace(
            '<details open>',
            '<details>'
        ).replace(
            '思考中...',
            title
        )
        self._thinking_html = ""
        return result

    def add_assistant_message(self, content: str, timestamp: str, font_color: str) -> str:
        """添加助手消息"""
        from .utils import create_markdown
        rendered_content = create_markdown(content)
        msg = f'''
        <div style="margin: 8px 0; padding: 8px 12px;">
            <div style="margin: 0 0 4px 0; font-size: 11px; color: #FD8A8A;">
                🤖 QGIS Agent · {timestamp}
            </div>
            <div style="margin: 0; color: {font_color}; line-height: 1.5;">
                {rendered_content}
            </div>
        </div>
        '''
        self._history_html += msg
        return self._history_html

    def add_tool_status(self, tool_name: str, status: str) -> str:
        """添加工具状态"""
        msg = f'''
        <div style="margin: 4px 0; padding: 4px 10px; background: #2d2d2d; border-left: 3px solid #4A90D9; border-radius: 4px; font-family: Consolas, monospace; font-size: 12px;">
            <span style="color: #4A90D9;">🔧 {html_module.escape(tool_name)}</span>
            <span style="color: #b0b0b0; margin-left: 8px;">{html_module.escape(status)}</span>
        </div>
        '''
        self._history_html += msg
        return self._history_html

    def get_full_html(self) -> str:
        """获取完整 HTML"""
        return self._history_html + self._thinking_html

    def clear(self):
        """清空"""
        self._history_html = ""
        self._thinking_html = ""
