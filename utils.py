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


# 主题回退 CSS：当无法从 thinking_display 取得调色板派生变量时使用中性浅色变量。
_FALLBACK_CSS = (
    ".qa-theme{"
    "--qa-bg:#ffffff;--qa-fg:#222222;--qa-border:#cccccc;"
    "--qa-muted:#666666;--qa-code-bg:#f4f4f4;--qa-code-fg:#333333;"
    "--qa-accent:#0a6ebd;"
    "}"
)

# 组件级 CSS：仅定义规则，变量优先用 thinking_display 注入的 --qa-*（带浅色兜底），
# 因此即使主题导入失败，消息仍可读。
_COMPONENT_CSS = """
.qa-theme{color:var(--qa-fg,#222);background:var(--qa-bg,#fff);}
.qa-theme a{color:var(--qa-accent,#0a6ebd);}
.qa-theme h2,.qa-theme h3,.qa-theme h4,.qa-theme h5,.qa-theme h6{color:var(--qa-fg,#222);}
.qa-theme p{margin:4px 0;}
.qa-theme table.qa-table{border-collapse:collapse;width:100%;margin:6px 0;}
.qa-theme table.qa-table th{background:var(--qa-code-bg,#f4f4f4);color:var(--qa-code-fg,#333);border:1px solid var(--qa-border,#ccc);padding:4px 8px;text-align:left;}
.qa-theme table.qa-table td{border:1px solid var(--qa-border,#ccc);padding:4px 8px;}
.qa-theme pre{background:var(--qa-code-bg,#f4f4f4);color:var(--qa-code-fg,#333);padding:10px;border-radius:6px;overflow-x:auto;font-family:Consolas,Monaco,monospace;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;}
.qa-theme code.qa-code{font-family:Consolas,Monaco,monospace;}
.qa-theme code.qa-inline-code{background:var(--qa-code-bg,#f4f4f4);color:var(--qa-code-fg,#333);padding:2px 5px;border-radius:3px;font-size:12px;font-family:Consolas,Monaco,monospace;}
"""

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

    # --- 主题 CSS：优先复用 thinking_display 的调色板派生变量 ---
    try:
        from thinking_display import get_theme_css
        theme_css = get_theme_css()
    except Exception:
        theme_css = _FALLBACK_CSS

    lines = markdown_text.split("\n")
    out = []
    para = []
    in_code = False
    code_buf = []
    table_buf = []
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

    for raw in lines:
        line = raw.rstrip()

        # 1) 代码围栏
        if not in_code and line.strip().startswith("```"):
            flush_para()
            close_list()
            flush_table()
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
                table_buf.append(line)
                continue
        elif table_buf:
            flush_table()

        # 3) 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para()
            close_list()
            tag = _HEADING_TAG[min(len(m.group(1)), 6)]
            out.append(f"<{tag}>{_inline(m.group(2))}</{tag}>")
            continue

        # 4) 水平线
        if re.match(r"^(---|\*\*\*|___)$", line.strip()):
            flush_para()
            close_list()
            out.append('<hr style="border:none;border-top:1px solid var(--qa-border,#ccc);margin:8px 0;">')
            continue

        # 5) 无序列表
        m = re.match(r"^[-*+]\s+(.*)$", line)
        if m:
            flush_para()
            if list_open != "ul":
                close_list()
                out.append('<ul class="qa-ul">')
                list_open = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # 6) 有序列表
        m = re.match(r"^\d+\.\s+(.*)$", line)
        if m:
            flush_para()
            if list_open != "ol":
                close_list()
                out.append('<ol class="qa-ol">')
                list_open = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # 7) 普通段落行
        if list_open:
            close_list()
        if line.strip() == "":
            flush_para()
        else:
            para.append(_inline(line))

    flush_para()
    close_list()
    flush_table()

    body = "".join(out)
    full_css = theme_css + "\n" + _COMPONENT_CSS
    return f'<div class="qa-theme"><style>{full_css}</style>{body}</div>'


def format_description(description: str) -> str:
    return description + "\n"
