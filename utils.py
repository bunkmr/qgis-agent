from datetime import datetime
from functools import wraps
from typing import Literal
import uuid
import re
import os

import psutil

try:
    from qgis.core import Qgis
    from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QScrollArea, QWidget
    from PyQt5.QtGui import QColor
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
    code_block_pattern = r"```(\w+)\n(.*?)```"
    def replacer(match):
        language = match.group(1)
        code = match.group(2)
        return f'<pre><code class="language-{language}">{code}</code></pre>'
    return re.sub(code_block_pattern, replacer, markdown_text, flags=re.DOTALL)


def format_description(description: str) -> str:
    return description + "\n"
