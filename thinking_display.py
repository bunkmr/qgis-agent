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
    ⚠️ 已废弃：输出的是 `--qa-*` **CSS 自定义属性**，而 QTextDocument 不解析
    `var()`（整条声明连同 fallback 一起被丢弃，实测 `color:var(--f,#f00)`
    最终渲染为默认黑色）—— 对聊天渲染完全无效。

    配色的唯一真源是 ``utils.chat_colors()``；样式表请走
    ``utils.create_markdown()``（内部已改为字面色值）。本函数仅为兼容外部
    调用而保留，不要在插件内部继续使用。

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


def _mono_font_stack():
    """等宽字体栈：Consolas 在 macOS 不存在（会触发 Qt 字体别名回退告警），排到后面。"""
    return '"SF Mono", Menlo, Consolas, Monaco, monospace'


def create_thinking_block(content: str, timestamp: str = "", is_final: bool = False,
                          collapsed: bool = True) -> str:
    """
    创建思考块

    Args:
        content: 思考内容
        timestamp: 时间戳
        is_final: 是否是最终状态（已结束）
        collapsed: 最终状态下是否折叠正文（仅 is_final=True 时有意义）

    Returns:
        HTML 字符串

    ⚠️ 这里**不用** <details>/<summary>：
        QTextDocument（QTextBrowser 的引擎）不支持该标签，会把正文照常渲染出来，
        于是「点击展开」是假的、折叠态也永远折叠不起来。真正的折叠由 DockWidget
        负责 —— 它按 collapsed 决定是否把正文塞进来，并处理 #toggle-thinking 锚点。

    ⚠️ 颜色一律用字面色值：
        QTextDocument 不解析 CSS 自定义属性，`var(--qa-accent)` 这类声明会被整条
        丢弃，边框/文字颜色落到无效值（实测标题渲染成粉紫色）。故配色改由
        utils.chat_colors() 在 Python 侧算好再写进行内样式。
    """
    try:
        from utils import chat_colors
    except ImportError:  # 以包方式导入时
        from .utils import chat_colors

    try:
        c = chat_colors()
    except Exception:
        from utils import _FALLBACK_CHAT_COLORS as c  # type: ignore

    safe_content = html_module.escape(content) if content else ""
    n_chars = len(content or "")
    time_text = f" · {timestamp}" if timestamp else ""
    mono = _mono_font_stack()

    if is_final:
        # 结束态：按 collapsed 决定正文是否随块一起输出（真正的折叠见 DockWidget）
        header_color = c["muted"]
        status_text = "💭 思考完成" + (f" · {n_chars} 字" if n_chars else "")
        hint_text = "展开" if collapsed else "收起"
        body = "" if collapsed else (
            '<tr><td style="background-color: %(code_bg)s; padding: 8px 10px;">'
            '<pre style="color: %(fg)s; font-size: 12px; line-height: 1.5; margin: 0;'
            ' white-space: pre-wrap; word-wrap: break-word;'
            ' font-family: %(mono)s;">%(txt)s</pre></td></tr>'
        ) % {"code_bg": c["code_bg"], "fg": c["fg"], "mono": mono,
             "txt": safe_content or "&nbsp;"}
    else:
        header_color = c["user_edge"]
        status_text = "💭 思考中…" + time_text
        hint_text = ""
        body = (
            '<tr><td style="background-color: %(code_bg)s; padding: 8px 10px;">'
            '<pre style="color: %(fg)s; font-size: 12px; line-height: 1.5; margin: 0;'
            ' white-space: pre-wrap; word-wrap: break-word;'
            ' font-family: %(mono)s;">%(txt)s</pre></td></tr>'
        ) % {"code_bg": c["code_bg"], "fg": c["fg"], "mono": mono,
             "txt": safe_content or "&nbsp;"}

    # 折叠态额外提供「复制」入口，由 DockWidget 的 anchorClicked 处理
    copy_entry = (' <a href="#copy-thinking" style="color: %s; font-size: 11px;'
                  ' text-decoration: none;">[复制]</a>' % c["user_edge"])
    # 展开/收起只在结束态提供：思考中每帧都会重建该块，切换状态会被下一帧覆盖
    toggle_entry = ""
    if is_final:
        toggle_entry = (' <a href="#toggle-thinking" style="color: %s; font-size: 11px;'
                        ' text-decoration: none;">[%s]</a>' % (c["user_edge"], hint_text))

    return (
        '<table width="88%%" cellpadding="0" cellspacing="0"'
        ' style="margin-top: 4px; margin-bottom: 6px;">'
        '<tr><td style="background-color: %(header_bg)s; border-left: 3px solid'
        ' %(edge)s; padding: 4px 9px;">'
        '<span style="color: %(header_color)s; font-size: 12px;">%(status)s</span>'
        '%(toggle)s%(copy)s'
        '</td></tr>%(body)s</table>'
    ) % {
        "header_bg": c["tool_bg"], "edge": header_color,
        "header_color": header_color, "status": status_text,
        "toggle": toggle_entry, "copy": copy_entry, "body": body,
    }


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
