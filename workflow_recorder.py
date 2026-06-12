"""
Workflow Recorder for QGIS Agent
Records tool call sequences and saves them as reusable workflows.
Inspired by SpatialAnalysisAgent's Cookbook and Solution Graph.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any


class WorkflowStep:
    """Represents a single step in a workflow"""

    def __init__(self, step_id: str, tool_name: str, tool_args: Dict,
                 description: str = None, result_template: str = None):
        self.step_id = step_id
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.description = description or f"Execute {tool_name}"
        self.result_template = result_template  # Template for result references

    def to_dict(self) -> Dict:
        return {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "description": self.description,
            "result_template": self.result_template
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'WorkflowStep':
        return cls(
            step_id=data["step_id"],
            tool_name=data["tool_name"],
            tool_args=data["tool_args"],
            description=data.get("description"),
            result_template=data.get("result_template")
        )


class WorkflowTemplate:
    """Represents a reusable workflow template"""

    def __init__(self, name: str, description: str = None,
                 steps: List[WorkflowStep] = None, tags: List[str] = None):
        self.name = name
        self.description = description or ""
        self.steps = steps or []
        self.tags = tags or []
        self.created_at = datetime.now().isoformat()
        self.usage_count = 0
        self.success_count = 0

    def add_step(self, step: WorkflowStep):
        self.steps.append(step)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [step.to_dict() for step in self.steps],
            "tags": self.tags,
            "created_at": self.created_at,
            "usage_count": self.usage_count,
            "success_count": self.success_count
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'WorkflowTemplate':
        steps = [WorkflowStep.from_dict(s) for s in data.get("steps", [])]
        template = cls(
            name=data["name"],
            description=data.get("description"),
            steps=steps,
            tags=data.get("tags", [])
        )
        template.created_at = data.get("created_at", datetime.now().isoformat())
        template.usage_count = data.get("usage_count", 0)
        template.success_count = data.get("success_count", 0)
        return template

    def get_success_rate(self) -> float:
        if self.usage_count == 0:
            return 0.0
        return self.success_count / self.usage_count


class WorkflowRecorder:
    """Records and manages workflow templates"""

    def __init__(self, save_dir: str = None):
        self.save_dir = save_dir or os.path.join(
            os.path.dirname(__file__), "workflows"
        )
        os.makedirs(self.save_dir, exist_ok=True)
        self.templates: Dict[str, WorkflowTemplate] = {}
        self._load_templates()

    def _load_templates(self):
        """Load all saved workflow templates"""
        for filename in os.listdir(self.save_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(self.save_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    template = WorkflowTemplate.from_dict(data)
                    self.templates[template.name] = template
                except Exception as e:
                    print(f"Error loading workflow {filename}: {e}")

    def record_from_conversation(self, tool_calls: List[Dict],
                                workflow_name: str = None,
                                description: str = None) -> WorkflowTemplate:
        """
        Record a workflow from conversation tool calls

        Args:
            tool_calls: List of tool calls from conversation
                [{"tool": "tool_name", "args": {...}, "result": {...}}, ...]
            workflow_name: Name for the workflow
            description: Description of the workflow

        Returns:
            Created WorkflowTemplate
        """
        # Generate workflow name if not provided
        if not workflow_name:
            workflow_name = self._generate_workflow_name(tool_calls)

        # Create steps
        steps = []
        for i, call in enumerate(tool_calls):
            step = WorkflowStep(
                step_id=f"step_{i+1}",
                tool_name=call.get("tool", ""),
                tool_args=call.get("args", {}),
                description=f"Step {i+1}: {call.get('tool', 'unknown')}"
            )
            steps.append(step)

        # Create template
        template = WorkflowTemplate(
            name=workflow_name,
            description=description or f"Workflow with {len(steps)} steps",
            steps=steps,
            tags=self._extract_tags(tool_calls)
        )

        # Save template
        self.templates[workflow_name] = template
        self._save_template(template)

        return template

    def _generate_workflow_name(self, tool_calls: List[Dict]) -> str:
        """Generate a descriptive workflow name"""
        tool_names = [call.get("tool", "unknown") for call in tool_calls]
        main_tool = tool_names[0] if tool_names else "workflow"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{main_tool}_{timestamp}"

    def _extract_tags(self, tool_calls: List[Dict]) -> List[str]:
        """Extract tags from tool calls"""
        tags = set()
        for call in tool_calls:
            tool_name = call.get("tool", "")
            if "buffer" in tool_name.lower():
                tags.add("buffer")
            elif "clip" in tool_name.lower():
                tags.add("clip")
            elif "intersection" in tool_name.lower():
                tags.add("intersection")
            elif "dissolve" in tool_name.lower():
                tags.add("dissolve")
            elif "extract" in tool_name.lower():
                tags.add("extraction")
            elif "join" in tool_name.lower():
                tags.add("join")
            elif "raster" in tool_name.lower():
                tags.add("raster")
            else:
                tags.add("other")
        return list(tags)

    def _save_template(self, template: WorkflowTemplate):
        """Save workflow template to file"""
        filename = f"{template.name.replace(' ', '_').replace(':', '_')}.json"
        filepath = os.path.join(self.save_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(template.to_dict(), f, indent=2, ensure_ascii=False)

    def search_workflows(self, query: str, top_k: int = 5) -> List[WorkflowTemplate]:
        """Search workflows by name, description, or tags"""
        query_lower = query.lower()
        results = []

        for name, template in self.templates.items():
            score = 0

            # Check name
            if query_lower in name.lower():
                score += 10

            # Check description
            if query_lower in template.description.lower():
                score += 5

            # Check tags
            for tag in template.tags:
                if query_lower in tag.lower():
                    score += 3

            # Check step tools
            for step in template.steps:
                if query_lower in step.tool_name.lower():
                    score += 2

            if score > 0:
                results.append((score, template))

        # Sort by score
        results.sort(key=lambda x: x[0], reverse=True)

        return [template for _, template in results[:top_k]]

    def get_workflow_context(self, template: WorkflowTemplate) -> str:
        """Get formatted context for a workflow (for LLM prompt)"""
        context = f"Workflow: {template.name}\n"
        context += f"Description: {template.description}\n"
        context += f"Steps ({len(template.steps)}):\n"

        for step in template.steps:
            context += f"  {step.step_id}: {step.tool_name}\n"
            context += f"    Args: {json.dumps(step.tool_args, indent=6)}\n"

        return context

    def record_success(self, workflow_name: str):
        """Record successful workflow execution"""
        if workflow_name in self.templates:
            self.templates[workflow_name].usage_count += 1
            self.templates[workflow_name].success_count += 1
            self._save_template(self.templates[workflow_name])

    def record_failure(self, workflow_name: str):
        """Record failed workflow execution"""
        if workflow_name in self.templates:
            self.templates[workflow_name].usage_count += 1
            self._save_template(self.templates[workflow_name])

    def get_popular_workflows(self, top_k: int = 10) -> List[WorkflowTemplate]:
        """Get most popular workflows"""
        sorted_templates = sorted(
            self.templates.values(),
            key=lambda t: t.usage_count,
            reverse=True
        )
        return sorted_templates[:top_k]
