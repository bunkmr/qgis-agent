"""
Import tool documentation from SpatialAnalysisAgent
Converts JSON format to TOML and integrates with QGIS Agent
"""

import json
import os
from typing import Dict, List


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


if __name__ == "__main__":
    # Paths
    spatial_agent_json = "C:/Users/zhaos/AppData/Roaming/QGIS/QGIS3/profiles/default/python/plugins/SpatialAnalysisAgent-master/SpatialAnalysisAgent/Tools_Documentation/qgis_tools_for_rag.json"
    output_dir = "D:/work/qgis_agent/tool_docs"
    output_json = "D:/work/qgis_agent/tool_docs_index.json"

    # Import tools
    import_tools_from_json(spatial_agent_json, output_dir)

    # Create index
    create_tools_index_json(output_dir, output_json)
