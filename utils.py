from datetime import datetime
from functools import wraps
from typing import Literal
import uuid
import re

try:
    from qgis.core import Qgis
    from qgis.PyQt.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QScrollArea, QWidget  # noqa: F401
    from qgis.PyQt.QtGui import QColor  # noqa: F401
    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False


def generate_unique_id():
    return str(uuid.uuid4()).replace("-", "_")


def get_current_timestamp():
    return datetime.now().strftime("%m %d %Y %H:%M:%S")


def handle_none_conversation(func):
    @wraps(func)
    def wrapper(self, conversation, *args, **kwargs):
        if conversation is None:
            return
        return func(self, conversation, *args, **kwargs)
    return wrapper


def unpack(row_dict: dict, table: Literal["conversation", "interaction", "prompt"]) -> list:
    colname_map = {
        "conversation": ["ID", "llmID", "title", "description", "created", "modified", "messageCount", "workflowCount", "userID"],
        "interaction": ["ID", "conversationID", "promptID", "requestText", "contextText", "requestTime", "typeMessage", "responseText", "responseTime", "workflow", "executionLog"],
        "prompt": ["ID", "llmID", "version", "template", "promptType"],
    }
    if table not in colname_map:
        raise ValueError("必须指定表类型: conversation, interaction, prompt")
    colnames = colname_map[table]
    if set(row_dict.keys()) != set(colnames):
        raise KeyError(f"字典键不匹配: 期望 {set(colnames)}, 实际 {set(row_dict.keys())}")
    return [row_dict[name] for name in colnames]


def pack(row_tuple: tuple, table: Literal["conversation", "interaction", "prompt"]) -> dict:
    colname_map = {
        "conversation": ["ID", "llmID", "title", "description", "created", "modified", "messageCount", "workflowCount", "userID"],
        "interaction": ["ID", "conversationID", "promptID", "requestText", "contextText", "requestTime", "typeMessage", "responseText", "responseTime", "workflow", "executionLog"],
        "prompt": ["ID", "llmID", "version", "template", "promptType"],
    }
    if table not in colname_map:
        raise ValueError("必须指定表类型: conversation, interaction, prompt")
    colnames = colname_map[table]
    return {name: row_tuple[i] for i, name in enumerate(colnames)}


def get_qgis_version():
    if not _HAS_QGIS:
        return "0.0"
    fullVersion = Qgis.QGIS_VERSION
    return ".".join(fullVersion.split(".")[:2])


def tuple_to_dict(all_row_list: list[tuple], table: Literal["conversation", "interaction", "prompt"]) -> list[dict]:
    return [pack(row, table) for row in all_row_list]


def nested_dict_to_list(full_dict: dict) -> list:
    ans = []
    for key, sub_list in full_dict.items():
        for item in sub_list:
            ans.append(f"{key}::{item}")
    return ans


def extract_code(response: str) -> str:
    pattern = r"```python(.*?)```"
    match = re.search(pattern, response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def set_font_color(bg_color):
    if not _HAS_QGIS:
        return "#181C14"
    luminance = (0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue()) / 255
    return "#F1F0E9" if luminance < 0.5 else "#181C14"


# ── 聊天区配色：纯函数实现，方便脱离 QGIS 单测 ─────────────────────────
#
# 为什么不用 CSS 变量（--qa-bg 那套）：
#   QTextDocument（QTextBrowser 的富文本引擎）不解析 CSS 自定义属性，
#   `color: var(--x, #000)` 会被整条丢弃，底色更是直接不生效。
#   实测（macOS QGIS 3.44.14 / 4.2.1）只有「字面色值 + background-color /
#   border / padding / margin」可靠；border-radius 亦不支持，故气泡一律直角。
# 因此这里在 Python 侧把 QPalette 混出一组字面色值，直接写进行内样式。

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# 取不到 QPalette 时的兜底（浅色主题中性值）
_FALLBACK_CHAT_COLORS = {
    "chat_bg": "#F7F8FA",
    "ai_bg": "#F1F3F6",
    "user_bg": "#E4EEF9",
    "user_edge": "#4A90D9",
    "ai_edge": "#9DB4CC",
    "fg": "#181C14",
    "muted": "#6B7280",
    "border": "#DFE3E8",
    "code_bg": "#E6E8EC",
    "tool_bg": "#F2F6FA",
    "tool_edge": "#5B9BD5",
}


def normalize_hex(color, default="#000000"):
    """把任意 QColor / 字符串归一成 `#rrggbb`；非法输入返回 default。"""
    if color is None:
        return default
    try:
        # QColor / 类似对象：优先 name()
        name = color.name() if hasattr(color, "name") else str(color)
    except Exception:
        return default
    if not _HEX_RE.match(name or ""):
        return default
    name = name.lstrip("#")
    if len(name) == 3:
        name = "".join(ch * 2 for ch in name)
    return "#" + name.lower()


def _to_rgb(color, default="#000000"):
    h = normalize_hex(color, default).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix_hex(color_a, color_b, ratio):
    """线性混色：ratio=0 返回 color_a，ratio=1 返回 color_b（超界自动夹紧）。"""
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        ratio = 0.0
    ratio = max(0.0, min(1.0, ratio))
    ra, ga, ba = _to_rgb(color_a)
    rb, gb, bb = _to_rgb(color_b)
    return "#%02x%02x%02x" % (
        round(ra + (rb - ra) * ratio),
        round(ga + (gb - ga) * ratio),
        round(ba + (bb - ba) * ratio),
    )


def relative_luminance(color):
    """0（全黑）~ 1（全白）。用于判断浅色/深色主题。"""
    r, g, b = _to_rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def is_dark_color(color):
    return relative_luminance(color) < 0.5


def derive_chat_colors(base, text, alternate=None, highlight=None):
    """由背景/前景/备用底色/强调色派生出聊天区整套字面色值。

    纯函数：输入输出都是 `#rrggbb` 字符串，不依赖 Qt，便于单元测试。
    """
    base = normalize_hex(base, "#ffffff")
    text = normalize_hex(text, "#181c14")
    alternate = normalize_hex(alternate, base) if alternate else mix_hex(base, text, 0.06)
    highlight = normalize_hex(highlight, "#4a90d9") if highlight else "#4a90d9"

    dark = is_dark_color(base)
    # 深色主题下把强调色提亮一点，否则在深底上发闷
    edge_user = mix_hex(highlight, "#ffffff", 0.25) if dark else highlight
    # AI 气泡：在底色与前景之间取一点点色调差；深色主题下往「更亮」方向偏
    if dark:
        ai_bg = mix_hex(base, "#ffffff", 0.07)
        chat_bg = mix_hex(base, "#000000", 0.12)
        border = mix_hex(base, "#ffffff", 0.16)
        tool_bg = mix_hex(base, edge_user, 0.14)
        muted = mix_hex(text, base, 0.42)
        code_bg = mix_hex(base, "#000000", 0.30)
    else:
        ai_bg = mix_hex(base, alternate, 0.85)
        chat_bg = mix_hex(base, text, 0.03)
        border = mix_hex(base, text, 0.16)
        tool_bg = mix_hex(base, edge_user, 0.10)
        muted = mix_hex(text, base, 0.45)
        code_bg = mix_hex(base, text, 0.10)

    return {
        "chat_bg": chat_bg,
        "ai_bg": ai_bg,
        # 用户气泡用强调色的浅色调（深色主题下同样往底色调）
        "user_bg": mix_hex(base, edge_user, 0.30 if dark else 0.16),
        "user_edge": edge_user,
        "ai_edge": mix_hex(base, text, 0.18) if dark else edge_user,
        "fg": text,
        "muted": muted,
        "border": border,
        # 代码块必须与所在气泡底色拉开，否则 pre/code 直接「隐形」
        "code_bg": code_bg,
        "tool_bg": tool_bg,
        "tool_edge": edge_user,
    }


def chat_colors():
    """读取当前 QApplication 调色板并派生聊天区配色；任何异常都回退到中性值。"""
    if not _HAS_QGIS:
        return dict(_FALLBACK_CHAT_COLORS)
    try:
        from qgis.PyQt.QtGui import QPalette
        app = QApplication.instance()
        if app is None:
            return dict(_FALLBACK_CHAT_COLORS)
        pal = app.palette()
        base = normalize_hex(pal.color(QPalette.ColorRole.Base).name(), "#ffffff")
        text = normalize_hex(pal.color(QPalette.ColorRole.Text).name(), "#181c14")
        alt = normalize_hex(pal.color(QPalette.ColorRole.AlternateBase).name(), base)
        hl = normalize_hex(pal.color(QPalette.ColorRole.Highlight).name(), "#4a90d9")
        return derive_chat_colors(base, text, alt, hl)
    except Exception:
        return dict(_FALLBACK_CHAT_COLORS)


# 等宽字体栈：Qt 富文本按顺序回退到第一个可用的字体族
_MONO_FONT_STACK = '"SF Mono", Menlo, Consolas, Monaco, monospace'


def _component_css(colors=None):
    """create_markdown 输出的组件级 CSS —— **全部使用字面色值**。

    ⚠️ 不要改回 CSS 自定义属性：QTextDocument 不解析 `var(--x, #fallback)`，
    整条声明会被直接丢弃（**连 fallback 都不生效**，实测 `color:var(--f,#f00)`
    最终渲染成默认黑色）。历史上这里是 `var(--qa-*)` 写法，等于整份样式表失效，
    只是恰好被 DockWidget 的 `_chat_css()` 兜住才没露馅。
    另外 QTextDocument 同样不支持 `border-radius` / `overflow`，这里一律不写。
    """
    c = colors or chat_colors()
    return (
        ".qa-theme{color:%(fg)s;}"
        ".qa-theme a{color:%(accent)s;}"
        ".qa-theme h2,.qa-theme h3,.qa-theme h4,"
        ".qa-theme h5,.qa-theme h6{color:%(fg)s;}"
        ".qa-theme p{margin:4px 0;}"
        ".qa-theme table.qa-table{border-collapse:collapse;width:100%%;margin:6px 0;}"
        ".qa-theme table.qa-table th{background-color:%(code_bg)s;color:%(code_fg)s;"
        "border:1px solid %(border)s;padding:4px 8px;text-align:left;}"
        ".qa-theme table.qa-table td{border:1px solid %(border)s;padding:4px 8px;}"
        ".qa-theme pre{background-color:%(code_bg)s;color:%(code_fg)s;padding:10px;"
        "font-family:%(mono)s;font-size:12px;line-height:1.5;"
        "white-space:pre-wrap;word-break:break-word;}"
        ".qa-theme code.qa-code{font-family:%(mono)s;}"
        ".qa-theme code.qa-inline-code{background-color:%(code_bg)s;color:%(code_fg)s;"
        "padding:2px 5px;font-size:12px;font-family:%(mono)s;}"
        ".qa-theme blockquote{border-left:3px solid %(border)s;margin:6px 0;"
        "padding:2px 10px;color:%(muted)s;}"
    ) % {
        "fg": c["fg"], "accent": c["user_edge"], "border": c["border"],
        "code_bg": c["code_bg"], "code_fg": c.get("code_fg") or c["fg"],
        "muted": c["muted"], "mono": _MONO_FONT_STACK,
    }

# 标题层级映射（保留原实现视觉：# -> h2, ## -> h3 ...）
_HEADING_TAG = {1: "h2", 2: "h3", 3: "h4", 4: "h5", 5: "h6", 6: "h6"}


def _split_row(line: str) -> list:
    """将 `| a | b |` 形式的一行拆成单元格列表。"""
    return [p.strip() for p in line.strip().strip("|").split("|")]


def _looks_like_table_row(line: str) -> bool:
    """判断一行是否为 GitHub 表格行（表头或分隔行）。"""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    cells = _split_row(line)
    if not cells:
        return False
    # 分隔行：每个单元格形如 --- / :--: / --:
    if all(re.match(r"^:?-+:?$", c) for c in cells):
        return True
    # 表头行：至少两列且均非空
    return len(cells) >= 2 and all(c != "" for c in cells)


def create_markdown(markdown_text: str) -> str:
    """将 Markdown 文本转换为 HTML（增强版），供 QTextBrowser.setHtml 使用。

    支持特性：标题、有序/无序列表、加粗、斜体、行内代码、围栏代码块、
    GitHub 风格表格、链接自动识别（[文字](url) 与裸 http(s) 链接），
    并注入 thinking_display.get_theme_css() 派生的 --qa-* 主题变量，
    使所有聊天消息在深色主题下可读（修复 U15）。不引入任何第三方依赖。
    """
    import html as html_module

    # --- 行内规则：先转义文本节点，再套用 Markdown 语法（仅作用于非代码文本） ---
    def _inline(text: str) -> str:
        s = html_module.escape(text, quote=False)
        # 行内代码（必须在链接前，避免代码内 URL 被错误识别）
        s = re.sub(r"`([^`]+)`", r'<code class="qa-inline-code">\1</code>', s)
        # 加粗
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
        # 斜体（避免匹配加粗 **）
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", s)
        # [文字](url)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', s)
        # 裸 http(s):// 链接（跳过已存在于 href=" 或 > 之后的，防止二次包裹）
        def _link(m):
            url = m.group(1)
            trail = ""
            while url and url[-1] in ".,;:!?)":
                trail = url[-1] + trail
                url = url[:-1]
            return f'<a href="{url}">{url}</a>' + trail

        s = re.sub(r"(?<![\"'>])(https?://[^\s<]+)", _link, s)
        return s

    # --- 组件级 CSS：字面色值（QTextDocument 不认 var()，见 _component_css 说明）---
    try:
        theme_css = _component_css()
    except Exception:
        theme_css = ""

    lines = markdown_text.split("\n")
    out = []
    para = []
    in_code = False
    code_buf = []
    table_buf = []
    quote_buf = []
    list_open = None  # None | "ul" | "ol"

    def flush_para():
        if para:
            out.append('<p class="qa-p">' + "<br>".join(para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_open
        if list_open == "ul":
            out.append("</ul>")
        elif list_open == "ol":
            out.append("</ol>")
        list_open = None

    def flush_table():
        if not table_buf:
            return
        rows = [_split_row(l) for l in table_buf]
        table_buf.clear()
        if not rows:
            return
        header = rows[0]
        data_rows = rows[2:] if len(rows) > 2 else []
        html_tbl = '<table class="qa-table"><thead><tr>'
        html_tbl += "".join(f"<th>{_inline(c)}</th>" for c in header)
        html_tbl += "</tr></thead><tbody>"
        for dr in data_rows:
            html_tbl += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in dr) + "</tr>"
        html_tbl += "</tbody></table>"
        out.append(html_tbl)

    def flush_quote():
        """把连续的 `> ` 行合并成一个引用块。

        原先没有引用支持，模型输出里的 `> 提示：…` 会原样显示成一个孤零零的
        ">" 前缀（Qt 的富文本不会自动识别它），看起来像排版事故。
        """
        if not quote_buf:
            return
        inner = "<br>".join(_inline(q) for q in quote_buf)
        quote_buf.clear()
        out.append('<blockquote class="qa-quote">' + inner + "</blockquote>")

    for raw in lines:
        line = raw.rstrip()

        # 1) 代码围栏
        if not in_code and line.strip().startswith("```"):
            flush_para()
            close_list()
            flush_table()
            flush_quote()
            in_code = True
            code_buf = []
            continue
        if in_code:
            if line.strip().startswith("```"):
                in_code = False
                escaped = html_module.escape("\n".join(code_buf))
                out.append(f'<pre><code class="qa-code">{escaped}</code></pre>')
            else:
                code_buf.append(raw)
            continue

        # 2) 表格收集
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            if table_buf or _looks_like_table_row(line):
                if not table_buf and para:
                    flush_para()
                    close_list()
                flush_quote()
                table_buf.append(line)
                continue
        elif table_buf:
            flush_table()

        # 3) 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para()
            close_list()
            flush_quote()
            tag = _HEADING_TAG[min(len(m.group(1)), 6)]
            out.append(f"<{tag}>{_inline(m.group(2))}</{tag}>")
            continue

        # 4) 水平线
        if re.match(r"^(---|\*\*\*|___)$", line.strip()):
            flush_para()
            close_list()
            flush_quote()
            out.append('<hr style="border:none;border-top:1px solid var(--qa-border,#ccc);margin:8px 0;">')
            continue

        # 5) 引用块（合并连续行）
        m = re.match(r"^>\s?(.*)$", line)
        if m:
            flush_para()
            close_list()
            quote_buf.append(m.group(1))
            continue
        if quote_buf:
            flush_quote()

        # 6) 无序列表
        m = re.match(r"^[-*+]\s+(.*)$", line)
        if m:
            flush_para()
            if list_open != "ul":
                close_list()
                out.append('<ul class="qa-ul">')
                list_open = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # 7) 有序列表
        m = re.match(r"^\d+\.\s+(.*)$", line)
        if m:
            flush_para()
            if list_open != "ol":
                close_list()
                out.append('<ol class="qa-ol">')
                list_open = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # 8) 普通段落行
        if list_open:
            close_list()
        if line.strip() == "":
            flush_para()
        else:
            para.append(_inline(line))

    flush_para()
    close_list()
    flush_table()
    flush_quote()

    body = "".join(out)
    return f'<div class="qa-theme"><style>{theme_css}</style>{body}</div>'


def format_description(description: str) -> str:
    return description + "\n"


def format_timestamp(raw, fmt_in="%m %d %Y %H:%M:%S", fmt_out="%Y-%m-%d %H:%M"):
    """把存储用的时间戳（`%m %d %Y %H:%M:%S`，形如 `09 23 2026 20:11:02`）转成
    人看的格式（`2026-09-23 20:11`）。

    解析失败时原样返回 —— 这条串只用于显示，绝不能因为格式变动就抛异常。
    """
    if not raw:
        return ""
    try:
        return datetime.strptime(str(raw).strip(), fmt_in).strftime(fmt_out)
    except (ValueError, TypeError):
        return str(raw)
