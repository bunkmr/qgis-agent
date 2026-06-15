"""
Clarification Manager for QGIS Agent
Identifies ambiguous or incomplete requests and asks clarifying questions.
Makes the agent more interactive and user-friendly.
"""

import re
from typing import Dict, List, Optional


class ClarificationManager:
    """Manages clarification questions for ambiguous user requests"""

    # Patterns that indicate ambiguous requests
    AMBIGUOUS_PATTERNS = {
        "missing_path": {
            "patterns": [
                r"添加.*图层",
                r"加载.*数据",
                r"打开.*文件",
                r"add.*layer",
                r"load.*data",
            ],
            "question": "请提供数据文件的完整路径（例如：D:/data/roads.shp）",
            "parameter": "file_path"
        },
        "missing_layer": {
            "patterns": [
                r"对.*图层",
                r"处理.*图层",
                r"分析.*图层",
                r"select.*layer",
                r"process.*layer",
            ],
            "question": "请指定要操作的图层名称",
            "parameter": "layer_name"
        },
        "missing_distance": {
            "patterns": [
                r"缓冲.*(\d+)?",
                r"buffer.*(\d+)?",
                r"创建.*缓冲",
            ],
            "question": "请指定缓冲区距离（单位：米）",
            "parameter": "distance"
        },
        "missing_field": {
            "patterns": [
                r"按.*字段",
                r"根据.*字段",
                r"select.*field",
                r"filter.*field",
            ],
            "question": "请指定要使用的字段名称",
            "parameter": "field_name"
        },
        "missing_output": {
            "patterns": [
                r"保存.*结果",
                r"输出.*文件",
                r"export.*",
                r"save.*",
            ],
            "question": "请指定输出文件路径",
            "parameter": "output_path"
        },
        "vague_analysis": {
            "patterns": [
                r"分析一下",
                r"看看",
                r"检查",
                r"analyze",
                r"check",
            ],
            "question": "请具体说明要分析什么内容",
            "parameter": "analysis_type"
        },
        "multiple_layers": {
            "patterns": [
                r"两个图层",
                r"多个图层",
                r"合并",
                r"叠加",
                r"two.*layers",
                r"multiple.*layers",
            ],
            "question": "请指定所有相关图层的名称",
            "parameter": "layer_names"
        }
    }

    # Patterns that indicate complete requests
    COMPLETE_PATTERNS = {
        "with_path": [
            r"[A-Za-z]:\\[\w\\]+\.\w+",
            r"/[\w/]+\.\w+",
            r"\.shp",
            r"\.tif",
            r"\.gpkg",
        ],
        "with_layer_name": [
            r"图层\s*[\w一-龥]+",
            r"layer\s*[\w]+",
        ],
        "with_parameters": [
            r"\d+\s*(米|m|km|公里)",
            r"field\s*=\s*[\w]+",
        ]
    }

    def __init__(self, llm=None):
        self.llm = llm

    def analyze_request(self, user_input: str, context: Dict = None) -> Dict:
        """
        Analyze user request for completeness

        Args:
            user_input: User's request
            context: Additional context (loaded layers, etc.)

        Returns:
            Analysis result with clarifications needed
        """
        analysis = {
            "is_complete": True,
            "ambiguities": [],
            "clarification_questions": [],
            "missing_parameters": [],
            "suggestions": []
        }

        # Check for ambiguous patterns
        for category, info in self.AMBIGUOUS_PATTERNS.items():
            for pattern in info["patterns"]:
                if re.search(pattern, user_input, re.IGNORECASE):
                    # Check if the parameter is already provided
                    if not self._is_parameter_provided(user_input, category, context):
                        analysis["is_complete"] = False
                        analysis["ambiguities"].append(category)
                        analysis["clarification_questions"].append(info["question"])
                        analysis["missing_parameters"].append(info["parameter"])

        # Check completeness indicators
        completeness_score = self._calculate_completeness_score(user_input, context)

        # Generate suggestions
        if completeness_score < 0.5:
            analysis["suggestions"] = self._generate_suggestions(user_input, analysis)

        return analysis

    def _is_parameter_provided(self, user_input: str, category: str,
                               context: Dict = None) -> bool:
        """Check if a parameter is already provided in the request"""
        context = context or {}

        if category == "missing_path":
            # Check if path is in user input or context
            return bool(re.search(r'[A-Za-z]:\\', user_input)) or \
                bool(re.search(r'/[\w/]+', user_input)) or \
                "file_path" in context

        elif category == "missing_layer":
            # Check if layer name is specified
            return bool(re.search(r'图层\s*[\w一-龥]+', user_input)) or \
                "layer_name" in context

        elif category == "missing_distance":
            # Check if distance is specified
            return bool(re.search(r'\d+\s*(米|m|km)', user_input)) or \
                "distance" in context

        elif category == "missing_field":
            # Check if field is specified
            return bool(re.search(r'字段\s*[\w]+', user_input)) or \
                "field_name" in context

        return False

    def _calculate_completeness_score(self, user_input: str,
                                      context: Dict = None) -> float:
        """Calculate how complete the user request is"""
        score = 0.0
        context = context or {}

        # Check for file path
        if re.search(r'[A-Za-z]:\\[\w\\]+\.\w+', user_input) or \
           re.search(r'/[\w/]+\.\w+', user_input):
            score += 0.3

        # Check for layer name
        if re.search(r'图层\s*[\w一-龥]+', user_input):
            score += 0.2

        # Check for parameters
        if re.search(r'\d+\s*(米|m|km)', user_input):
            score += 0.2

        # Check for context
        if context.get("loaded_layers"):
            score += 0.1
        if context.get("current_project"):
            score += 0.1

        # Check for specific action
        action_keywords = ["添加", "删除", "分析", "缓冲", "裁剪", "合并",
                           "add", "remove", "analyze", "buffer", "clip", "merge"]
        for keyword in action_keywords:
            if keyword in user_input.lower():
                score += 0.1
                break

        return min(score, 1.0)

    def _generate_suggestions(self, user_input: str, analysis: Dict) -> List[str]:
        """Generate suggestions for completing the request"""
        suggestions = []

        if "missing_path" in analysis["ambiguities"]:
            suggestions.append("提供完整的文件路径，如：D:/data/roads.shp")

        if "missing_layer" in analysis["ambiguities"]:
            suggestions.append("指定图层名称，或使用 '当前图层' 指代选中的图层")

        if "missing_distance" in analysis["ambiguities"]:
            suggestions.append("指定距离参数，如：100米")

        if "missing_field" in analysis["ambiguities"]:
            suggestions.append("指定字段名称，如：按 NAME 字段筛选")

        if "vague_analysis" in analysis["ambiguities"]:
            suggestions.append("具体说明分析内容，如：统计图层的面积、长度等")

        return suggestions

    def generate_clarification_prompt(self, user_input: str,
                                      analysis: Dict) -> str:
        """Generate a prompt for LLM to ask clarification questions"""
        if analysis["is_complete"]:
            return ""

        prompt = f"""用户请求不够明确，需要向用户确认以下信息：

用户输入："{user_input}"

需要澄清的内容：
"""
        for i, question in enumerate(analysis["clarification_questions"], 1):
            prompt += f"{i}. {question}\n"

        if analysis["suggestions"]:
            prompt += "\n建议用户：\n"
            for suggestion in analysis["suggestions"]:
                prompt += f"- {suggestion}\n"

        prompt += "\n请生成一个友好的澄清问题，一次询问多个缺失信息。"
        return prompt

    def get_clarification_response(self, user_input: str,
                                   context: Dict = None) -> Optional[str]:
        """
        Generate clarification response if needed

        Args:
            user_input: User's request
            context: Additional context

        Returns:
            Clarification question or None if request is complete
        """
        analysis = self.analyze_request(user_input, context)

        if analysis["is_complete"]:
            return None

        # Use LLM to generate natural clarification
        if self.llm and len(analysis["clarification_questions"]) > 0:
            try:
                from langchain_core.messages import HumanMessage
                prompt = self.generate_clarification_prompt(user_input, analysis)
                response = self.llm.invoke([HumanMessage(content=prompt)])
                return response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                print(f"LLM clarification failed: {e}")

        # Fallback: combine all questions
        if analysis["clarification_questions"]:
            return "我需要更多信息来完成这个任务：\n" + \
                   "\n".join(f"• {q}" for q in analysis["clarification_questions"])

        return None
