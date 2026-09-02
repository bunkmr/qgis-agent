"""
Import tool documentation from SpatialAnalysisAgent
Converts JSON format to TOML and integrates with QGIS Agent
"""

import json
import os
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 时回退到 tomli
    import tomli as tomllib


def import_tools_from_json(json_path: str, output_dir: str):
    """
    Import tools from SpatialAnalysisAgent's JSON format

    Args:
        json_path: Path to qgis_tools_for_rag.json
        output_dir: Directory to save TOML files
    """
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Read JSON
    with open(json_path, 'r', encoding='utf-8') as f:
        tools = json.load(f)

    print(f"Importing {len(tools)} tools...")

    # Convert each tool to TOML format
    for tool in tools:
        tool_id = tool.get("tool_id", "")
        if not tool_id:
            continue

        # Create TOML content
        toml_content = f'''tool_ID = "{tool_id}"
tool_name = "{tool.get('toolname', '')}"
brief_description = """{tool.get('tool_description', '')}"""
full_description = """{tool.get('tool_description', '')}"""
parameters = """
{tool.get('parameters', '')}
"""
code_example = """
{tool.get('code_example', '')}
"""
'''

        # Save TOML file
        toml_filename = f"{tool_id.replace(':', '_')}.toml"
        toml_path = os.path.join(output_dir, toml_filename)

        with open(toml_path, 'w', encoding='utf-8') as f:
            f.write(toml_content)

    print(f"Imported {len(tools)} tools to {output_dir}")


def create_tools_index_json(tools_dir: str, output_json: str):
    """
    Create a JSON index of all tools for fast retrieval

    Args:
        tools_dir: Directory containing TOML files
        output_json: Output JSON file path
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11 时回退到 tomli
        import tomli as tomllib

    tools_index = {}

    for filename in os.listdir(tools_dir):
        if filename.endswith(".toml"):
            filepath = os.path.join(tools_dir, filename)
            try:
                with open(filepath, "rb") as f:
                    doc = tomllib.load(f)

                tool_id = doc.get("tool_ID", "")
                if tool_id:
                    tools_index[tool_id] = {
                        "tool_id": tool_id,
                        "tool_name": doc.get("tool_name", ""),
                        "brief_description": doc.get("brief_description", ""),
                        "parameters": doc.get("parameters", ""),
                        "code_example": doc.get("code_example", "")
                    }
            except Exception as e:
                print(f"Error loading {filepath}: {e}")

    # Save index
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(tools_index, f, indent=2, ensure_ascii=False)

    print(f"Created index with {len(tools_index)} tools")


def _safe_dump_toml(fields: dict) -> str:
    """将字段安全序列化为合法 TOML（多行块中对引号做转义，避免 \"\"\" 破坏结构）。"""
    lines = []
    for k in ("tool_ID", "tool_name"):
        lines.append(f'{k} = "{fields.get(k, "")}"')
    for k in ("brief_description", "full_description", "parameters", "code_example"):
        val = fields.get(k, "")
        # 先转义反斜杠，再转义双引号，保证三重引号块内不会出现裸 \"\"\"
        val = val.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{k} = """\n{val}\n"""')
    return "\n".join(lines) + "\n"


def _read_text(path: str) -> str:
    """以容错方式读取文本：依次尝试 utf-8 / gbk / latin-1，最后用替换兜底。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def repair_tool_docs(tool_docs_dir: str):
    """将因 code_example 含 \"\"\" 而非法（标准解析失败）的 TOML 重写为合法 TOML。

    不删除任何文件，仅修复格式错误的那些。
    """
    if not os.path.isdir(tool_docs_dir):
        print(f"目录不存在: {tool_docs_dir}")
        return
    repaired = 0
    for filename in sorted(os.listdir(tool_docs_dir)):
        if not filename.endswith(".toml"):
            continue
        filepath = os.path.join(tool_docs_dir, filename)
        try:
            with open(filepath, "rb") as f:
                tomllib.load(f)
            continue  # 已是合法 TOML，跳过
        except Exception:
            pass
        # 宽松解析后按合法 TOML 重写
        text = _read_text(filepath)
        fields = _lenient_parse(text)
        if not fields.get("tool_ID"):
            print(f"跳过（无 tool_ID）: {filename}")
            continue
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(_safe_dump_toml(fields))
        repaired += 1
    print(f"已修复 {repaired} 个非法 TOML 文件")


def _lenient_parse(text: str) -> dict:
    """宽松解析：按字段名定位多行字符串块（与 DocStore._lenient_parse_toml 同逻辑）。"""
    fields = {}
    for key in ("tool_ID", "tool_name"):
        m = re.search(rf'^{key}\s*=\s*"([^"\n]*)"', text, re.M)
        if m:
            fields[key] = m.group(1)
    pattern = re.compile(r'^([A-Za-z_]+)\s*=\s"""', re.M)
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].lstrip("\n")
        if content.rstrip().endswith('"""'):
            content = content.rstrip()[:-3]
        fields[name] = content
    return fields


if __name__ == "__main__":
    import argparse

    repo_root = os.path.dirname(os.path.abspath(__file__))
    default_output_dir = os.path.join(repo_root, "tool_docs")

    parser = argparse.ArgumentParser(description="从 SpatialAnalysisAgent 导入工具文档为 TOML")
    parser.add_argument(
        "--json",
        default=os.path.join(
            os.path.expanduser("~"),
            "AppData", "Roaming", "QGIS", "QGIS3", "profiles", "default",
            "python", "plugins", "SpatialAnalysisAgent-master", "SpatialAnalysisAgent",
            "Tools_Documentation", "qgis_tools_for_rag.json",
        ),
        help="qgis_tools_for_rag.json 路径（源数据，默认指向本机 SpatialAnalysisAgent 插件目录）",
    )
    parser.add_argument("--output-dir", default=default_output_dir, help="TOML 输出目录")
    parser.add_argument(
        "--index",
        default=os.path.join(default_output_dir, "tool_docs_index.json"),
        help="生成的 JSON 索引路径",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="将 tool_docs 中格式非法的 TOML 重写为合法 TOML（不删除文件）",
    )
    args = parser.parse_args()

    if args.repair:
        repair_tool_docs(args.output_dir)
    else:
        import_tools_from_json(args.json, args.output_dir)
        create_tools_index_json(args.output_dir, args.index)
