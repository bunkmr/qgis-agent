# -*- coding: utf-8 -*-
"""
思考内容显示模块

实现类似 Claude Code 的思考显示效果：
- 思考过程中实时显示内容
- 思考结束后可折叠/展开
"""

import html as html_module


def get_theme_css() -> str:
    """
    从 QApplication 的 palette 派生一组主题 CSS 自定义属性（CSS 变量）。

    取色规则（全部 Qt5/Qt6 双兼容，角色名用作用域写法 QPalette.ColorRole.X）：
        --qa-bg       ← QPalette.ColorRole.Base          文本区背景
        --qa-fg       ← QPalette.ColorRole.Text          主文本
        --qa-border   ← QPalette.ColorRole.Mid           边框
        --qa-muted    ← QPalette.ColorRole.PlaceholderText（取不到用 Mid）
        --qa-code-bg  ← QPalette.ColorRole.AlternateBase 代码块背景
        --qa-code-fg  ← QPalette.ColorRole.Text          代码块文本
        --qa-accent   ← QPalette.ColorRole.Highlight     强调色

    永不抛异常：若没有 QApplication 实例（或取色失败），回退到一组
    中性灰（浅色默认）CSS，保证调用方始终拿到合法字符串。
    """
    fallback = (
        ":root, .qa-theme {\n"
        "  --qa-bg: #ffffff;\n"
        "  --qa-fg: #333333;\n"
        "  --qa-border: #cccccc;\n"
        "  --qa-muted: #888888;\n"
        "  --qa-code-bg: #f5f5f5;\n"
        "  --qa-code-fg: #333333;\n"
        "  --qa-accent: #2d7fb8;\n"
        "}"
    )

    # 必须用 qgis.PyQt（QGIS 自带绑定），不能用独立的 PyQt5/PyQt6：
    #   - QGIS4/Qt6 环境下没有 PyQt5；
    #   - 独立的 PyQt6 与 QGIS 内部 PyQt6 命名空间冲突，且可能与 QGIS 编译的 Qt 版本不一致。
    # 懒加载 + ImportError 回退：无 QGIS（如单元测试）时返回中性灰 fallback。
    try:
        from qgis.PyQt.QtWidgets import QApplication
        from qgis.PyQt.QtGui import QPalette
    except ImportError:
        return fallback

    try:
        app = QApplication.instance()
        if app is None:
            return fallback
        pal = app.palette()

        def _role(role_name: str, default: str) -> str:
            try:
                role = getattr(QPalette.ColorRole, role_name)
                return pal.color(role).name()
            except Exception:
                return default

        bg = _role("Base", "#ffffff")
        fg = _role("Text", "#333333")
        border = _role("Mid", "#cccccc")
        muted = _role("PlaceholderText", border)
        code_bg = _role("AlternateBase", "#f5f5f5")
        accent = _role("Highlight", "#2d7fb8")
    except Exception:
        return fallback

    return (
        ":root, .qa-theme {\n"
        f"  --qa-bg: {bg};\n"
        f"  --qa-fg: {fg};\n"
        f"  --qa-border: {border};\n"
        f"  --qa-muted: {muted};\n"
        f"  --qa-code-bg: {code_bg};\n"
        f"  --qa-code-fg: {fg};\n"
        f"  --qa-accent: {accent};\n"
        "}"
    )


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
    theme_css = get_theme_css()

    if is_final:
        # 最终状态：折叠
        details_open = ""
        header_color = "var(--qa-muted)"
        status_text = "💭 思考完成"
        hint_text = "点击展开"
        # 折叠态额外提供「复制」入口，由 DockWidget 的 anchorClicked 处理
        copy_entry = ' <a href="#copy-thinking" style="color: var(--qa-accent); font-size: 11px;">[复制]</a>'
    else:
        # 思考中：展开
        details_open = " open"
        header_color = "var(--qa-accent)"
        status_text = f"🧠 思考中...{time_text}"
        hint_text = "点击折叠"
        copy_entry = ""

    # 简化 HTML 结构，使用更兼容的样式；颜色全部走主题 CSS 变量（见 get_theme_css）
    # 用 .qa-theme 包裹，使注入的 :root/.qa-theme 变量对块内元素生效
    html = f'''<style>{theme_css}</style><div class="qa-theme" style="margin: 8px 0; padding: 0;">
<details{details_open}>
<summary style="cursor: pointer; padding: 8px 12px; background-color: var(--qa-bg); border-left: 4px solid {header_color}; border-radius: 4px;"><span style="color: {header_color}; font-weight: bold;">{status_text}</span> <span style="color: var(--qa-muted); font-size: 11px;">[{hint_text}]</span>{copy_entry}</summary>
<div style="padding: 10px 12px; background-color: var(--qa-code-bg); border-left: 4px solid var(--qa-border); margin-top: 2px; min-height: 20px;"><pre style="color: var(--qa-code-fg); font-size: 12px; line-height: 1.5; margin: 0; white-space: pre-wrap; word-wrap: break-word; font-family: Consolas, Monaco, monospace;">{safe_content}</pre></div>
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
        self._current_timestamp = ""
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
        self._current_timestamp = timestamp or ""

        # 生成思考块 HTML
        html = create_thinking_block("", self._current_timestamp, is_final=False)
        self._history.append(html)

        return html

    def update(self, content: str) -> str:
        """
        更新思考内容

        注意：这里传入的是「累积后的完整文本」而不是新增片段，
        片段累积由调用方（DockWidget 的 _thinking_buffer）负责。

        Args:
            content: 累积后的完整思考内容

        Returns:
            更新后的 HTML
        """
        self._current_content = content or ""

        # 重新生成最后一个思考块（沿用开始时的时间戳）
        if self._history:
            self._history[-1] = create_thinking_block(
                self._current_content, self._current_timestamp, is_final=False
            )

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
        self._current_timestamp = ""
        self._history.clear()
