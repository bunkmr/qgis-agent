from datetime import datetime
from functools import wraps
from typing import Literal
import uuid
import re
import os

try:
    from qgis.core import Qgis
    from qgis.PyQt.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QScrollArea, QWidget
    from qgis.PyQt.QtGui import QColor
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


def get_system_info():
    import psutil
    mac_addresses = []
    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac_addresses.append(addr.address)
    eth_interfaces = [
        iface
        for iface, addrs in psutil.net_if_addrs().items()
        if any(addr.family == psutil.AF_LINK for addr in addrs)
    ]
    return {
        "macID": mac_addresses[0] if mac_addresses else "N/A",
        "ethInterfaces": ", ".join(eth_interfaces),
        "qgisVersion": get_qgis_version(),
    }


def set_font_color(bg_color):
    if not _HAS_QGIS:
        return "#181C14"
    luminance = (0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue()) / 255
    return "#F1F0E9" if luminance < 0.5 else "#181C14"


def create_markdown(markdown_text: str) -> str:
    """将 Markdown 文本转换为 HTML，支持 QTextBrowser 渲染"""
    import html as html_module

    text = markdown_text

    # 0. 先提取代码块并保护起来（避免被后续规则误处理）
    code_blocks = {}
    code_counter = [0]

    def _protect_code(m):
        placeholder = f"<!--CODEBLOCK_{code_counter[0]}-->"
        lang = m.group(1) or ""
        code = m.group(2)
        code_blocks[placeholder] = f'<pre style="background-color:#1e1e1e;color:#d4d4d4;padding:10px;border-radius:6px;overflow-x:auto;font-family:Consolas,monospace;font-size:12px;line-height:1.5;"><code>{html_module.escape(code)}</code></pre>'
        code_counter[0] += 1
        return placeholder

    text = re.sub(r"```(\w*)\n(.*?)```", _protect_code, text, flags=re.DOTALL)

    # 1. 转义 HTML 特殊字符（保护已转义的内容）
    text = html_module.escape(text, quote=False)

    # 2. 标题 (# ## ### 等)
    text = re.sub(r"^#### (.+)$", r"<h5 style='margin:4px 0;font-size:13px;'>\1</h5>", text, flags=re.MULTILINE)
    text = re.sub(r"^### (.+)$", r"<h4 style='margin:4px 0;font-size:14px;'>\1</h4>", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", r"<h3 style='margin:6px 0;font-size:15px;'>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^# (.+)$", r"<h2 style='margin:8px 0;font-size:16px;'>\1</h2>", text, flags=re.MULTILINE)

    # 3. 加粗 **text** 和 __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 4. 斜体 *text* 和 _text_（避免匹配到加粗的 **）
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", text)

    # 5. 行内代码 `code`
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background-color:#2d2d2d;color:#e6db74;padding:2px 5px;border-radius:3px;font-family:Consolas,monospace;font-size:12px;">\1</code>',
        text
    )

    # 6. 无序列表 - item 或 * item
    text = re.sub(r"^[\-*] (.+)$", r"<li style='margin-left:20px;'>\1</li>", text, flags=re.MULTILINE)

    # 7. 有序列表 1. item
    text = re.sub(r"^\d+\. (.+)$", r"<li style='margin-left:20px;'>\1</li>", text, flags=re.MULTILINE)

    # 8. 水平线 --- 或 ***
    text = re.sub(r"^(---|\*\*\*)$", r"<hr style='border:none;border-top:1px solid #555;margin:8px 0;'>", text, flags=re.MULTILINE)

    # 9. 段落：将连续的换行转为段落分隔
    text = re.sub(r"\n\n+", "<br><br>", text)
    text = re.sub(r"\n", "<br>", text)

    # 10. 恢复代码块
    for placeholder, html_code in code_blocks.items():
        text = text.replace(placeholder, html_code)

    return text


def format_description(description: str) -> str:
    return description + "\n"
