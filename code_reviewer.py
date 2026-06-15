"""
Code Reviewer for QGIS Agent
Reviews generated PyQGIS/Processing code for correctness.
Inspired by SpatialAnalysisAgent's code review system.
"""

from typing import Dict


class CodeReviewer:
    """Reviews generated code for correctness and best practices"""

    REVIEW_PROMPT = """You are a QGIS/PyQGIS expert. Review the following code for correctness, best practices, and potential issues.

Code to Review:
```python
{code}
```

Context:
- Tool Used: {tool_name}
- Tool ID: {tool_id}
- User Request: {user_query}

Please review the code and provide:

1. **Correctness**: Is the code syntactically correct? Will it run without errors?
2. **Best Practices**: Does it follow QGIS/PyQGIS best practices?
3. **Potential Issues**: Any potential runtime errors or warnings?
4. **Improvements**: Suggest improvements if needed.

Provide your review in the following JSON format:
```json
{{
  "is_correct": true/false,
  "confidence": 0.0-1.0,
  "issues": ["issue1", "issue2"],
  "suggestions": ["suggestion1", "suggestion2"],
  "reviewed_code": "corrected code if needed, otherwise original code"
}}
```
"""

    def __init__(self, llm=None):
        self.llm = llm

    def review_code(self, code: str, tool_name: str, tool_id: str,
                    user_query: str) -> Dict:
        """
        Review generated code

        Args:
            code: Code to review
            tool_name: Name of the tool used
            tool_id: ID of the tool used
            user_query: Original user query

        Returns:
            Review results
        """
        prompt = self.REVIEW_PROMPT.format(
            code=code,
            tool_name=tool_name,
            tool_id=tool_id,
            user_query=user_query
        )

        if self.llm:
            try:
                from langchain_core.messages import HumanMessage
                response = self.llm.invoke([HumanMessage(content=prompt)])
                return self._parse_review_response(response.content)
            except Exception as e:
                print(f"Code review failed: {e}")
                return self._fallback_review(code)
        else:
            return self._fallback_review(code)

    def _parse_review_response(self, response: str) -> Dict:
        """Parse LLM review response"""
        import json
        import re

        try:
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                review = json.loads(json_match.group())
                return {
                    "is_correct": review.get("is_correct", True),
                    "confidence": review.get("confidence", 0.5),
                    "issues": review.get("issues", []),
                    "suggestions": review.get("suggestions", []),
                    "reviewed_code": review.get("reviewed_code", "")
                }
        except json.JSONDecodeError:
            pass

        # Fallback: return basic review
        return self._fallback_review(response)

    def _fallback_review(self, code: str) -> Dict:
        """Basic code review without LLM"""
        issues = []
        suggestions = []

        # Basic checks
        if "import" not in code:
            issues.append("Missing import statements")

        if "processing.run" in code and "parameters" not in code:
            issues.append("Missing parameters for processing.run")

        if "QgsVectorLayer" in code and "isValid()" not in code:
            suggestions.append("Consider checking layer validity with isValid()")

        if "QgsProject.instance().addMapLayer" not in code:
            suggestions.append("Consider adding the result layer to the project")

        return {
            "is_correct": len(issues) == 0,
            "confidence": 0.6,
            "issues": issues,
            "suggestions": suggestions,
            "reviewed_code": code
        }

    def get_review_summary(self, review: Dict) -> str:
        """Generate human-readable review summary"""
        summary = []

        if review["is_correct"]:
            summary.append("✅ Code review passed")
        else:
            summary.append("❌ Code review found issues")

        summary.append(f"Confidence: {review['confidence'] *100:.1f}%")

        if review["issues"]:
            summary.append("\nIssues found:")
            for issue in review["issues"]:
                summary.append(f"  - {issue}")

        if review["suggestions"]:
            summary.append("\nSuggestions:")
            for suggestion in review["suggestions"]:
                summary.append(f"  - {suggestion}")

        return "\n".join(summary)
