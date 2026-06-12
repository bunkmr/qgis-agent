"""
Tool Documentation Manager for QGIS Agent
Loads and manages TOML tool documentation for RAG retrieval.
Inspired by SpatialAnalysisAgent's Tools_Documentation system.
"""

import os
import json
from typing import Dict, List, Optional, Any


class ToolDocManager:
    """Manages TOML tool documentation for RAG retrieval"""

    def __init__(self, docs_dir: str = None):
        self.docs_dir = docs_dir or os.path.join(os.path.dirname(__file__), "tool_docs")
        self.tools_index: Dict[str, Dict] = {}
        self._load_all_docs()

    def _load_all_docs(self):
        """Load all TOML documentation files"""
        if not os.path.exists(self.docs_dir):
            print(f"Warning: Tool docs directory not found: {self.docs_dir}")
            return

        for filename in os.listdir(self.docs_dir):
            if filename.endswith(".toml"):
                filepath = os.path.join(self.docs_dir, filename)
                self._load_toml_doc(filepath)

    def _load_toml_doc(self, filepath: str):
        """Load a single TOML documentation file"""
        try:
            import tomli as tomllib
            with open(filepath, "rb") as f:
                doc = tomllib.load(f)

            tool_id = doc.get("tool_ID", "")
            if tool_id:
                self.tools_index[tool_id] = {
                    "tool_ID": tool_id,
                    "tool_name": doc.get("tool_name", ""),
                    "brief_description": doc.get("brief_description", ""),
                    "full_description": doc.get("full_description", ""),
                    "parameters": doc.get("parameters", ""),
                    "code_example": doc.get("code_example", ""),
                    "file_path": filepath
                }
                print(f"Loaded tool doc: {tool_id}")
        except Exception as e:
            print(f"Error loading {filepath}: {e}")

    def get_tool_doc(self, tool_id: str) -> Optional[Dict]:
        """Get documentation for a specific tool"""
        return self.tools_index.get(tool_id)

    def search_tools(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Search tools based on query

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of matching tools with scores
        """
        query_lower = query.lower()
        results = []

        for tool_id, doc in self.tools_index.items():
            score = 0

            # Check tool name
            if query_lower in doc["tool_name"].lower():
                score += 10

            # Check brief description
            if query_lower in doc["brief_description"].lower():
                score += 5

            # Check full description
            if query_lower in doc["full_description"].lower():
                score += 3

            # Check parameters
            if query_lower in doc["parameters"].lower():
                score += 2

            if score > 0:
                results.append({
                    "tool_id": tool_id,
                    "tool_name": doc["tool_name"],
                    "score": score,
                    "doc": doc
                })

        # Sort by score
        results.sort(key=lambda x: x["score"], reverse=True)

        return results[:top_k]

    def get_tool_context(self, tool_id: str) -> str:
        """Get formatted context for a tool (for LLM prompt)"""
        doc = self.get_tool_doc(tool_id)
        if not doc:
            return ""

        context = f"Tool: {doc['tool_name']}\n"
        context += f"ID: {doc['tool_ID']}\n"
        context += f"Description: {doc['brief_description']}\n"
        context += f"Parameters:\n{doc['parameters']}\n"
        context += f"Code Example:\n{doc['code_example']}\n"

        return context

    def get_tools_index_summary(self) -> str:
        """Get summary of all available tools"""
        summary = f"Total tools: {len(self.tools_index)}\n\n"
        summary += "Available tools:\n"

        for tool_id, doc in sorted(self.tools_index.items()):
            summary += f"- {doc['tool_name']} ({tool_id}): {doc['brief_description'][:50]}...\n"

        return summary

    def add_tool_doc(self, tool_id: str, tool_name: str, description: str,
                    parameters: str, code_example: str):
        """Add a new tool documentation"""
        self.tools_index[tool_id] = {
            "tool_ID": tool_id,
            "tool_name": tool_name,
            "brief_description": description,
            "full_description": description,
            "parameters": parameters,
            "code_example": code_example,
            "file_path": None
        }

    def export_to_json(self, output_path: str):
        """Export tools index to JSON format (for RAG)"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.tools_index, f, indent=2, ensure_ascii=False)
        print(f"Exported {len(self.tools_index)} tools to {output_path}")

    def get_code_examples_for_tools(self, tool_ids: List[str]) -> str:
        """Get code examples for multiple tools"""
        examples = []
        for tool_id in tool_ids:
            doc = self.get_tool_doc(tool_id)
            if doc and doc["code_example"]:
                examples.append(f"### {doc['tool_name']}\n{doc['code_example']}")
        return "\n\n".join(examples)


class ToolSelector:
    """Selects appropriate tools based on user query"""

    def __init__(self, doc_manager: ToolDocManager):
        self.doc_manager = doc_manager

    def select_tools(self, user_query: str, data_overview: str = None,
                    max_tools: int = 3) -> List[Dict]:
        """
        Select tools based on user query

        Args:
            user_query: User's request
            data_overview: Overview of available data
            max_tools: Maximum number of tools to select

        Returns:
            List of selected tools with context
        """
        # Search for relevant tools
        search_results = self.doc_manager.search_tools(user_query, top_k=max_tools * 2)

        # Filter and rank based on relevance
        selected = []
        for result in search_results[:max_tools]:
            tool_doc = result["doc"]
            selected.append({
                "tool_id": result["tool_id"],
                "tool_name": result["tool_name"],
                "relevance_score": result["score"],
                "context": self.doc_manager.get_tool_context(result["tool_id"])
            })

        return selected

    def get_tool_selection_prompt(self, user_query: str, available_tools: List[Dict]) -> str:
        """Generate prompt for LLM tool selection"""
        tools_list = "\n".join([
            f"- {t['tool_name']} ({t['tool_id']}): {t['brief_description'][:80]}"
            for t in available_tools
        ])

        prompt = f"""You are a GIS expert. Based on the user's request, select the most appropriate QGIS tool(s).

User Request: "{user_query}"

Available Tools:
{tools_list}

Select the best tool(s) for this task. Return a JSON list with format:
[
  {{"tool_id": "tool_id", "tool_name": "name", "reason": "why this tool"}}
]
"""
        return prompt
