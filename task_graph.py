"""
Task Graph Visualization for QGIS Agent
Provides workflow visualization using NetworkX and PyVis.
Inspired by SpatialAnalysisAgent's Solution Graph.
"""

import os
import json
from typing import Dict, List, Optional, Any
from datetime import datetime

try:
    import networkx as nx
    from pyvis.network import Network
    HAS_VISUALIZATION = True
except ImportError:
    HAS_VISUALIZATION = False


class TaskStep:
    """Represents a single step in a task workflow"""

    def __init__(self, step_id: str, name: str, description: str,
                 status: str = "pending", tool_used: str = None,
                 result: Any = None, error: str = None):
        self.step_id = step_id
        self.name = name
        self.description = description
        self.status = status  # pending, running, completed, failed
        self.tool_used = tool_used
        self.result = result
        self.error = error
        self.timestamp = datetime.now().isoformat()
        self.duration = None

    def to_dict(self) -> Dict:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "tool_used": self.tool_used,
            "result": str(self.result) if self.result else None,
            "error": self.error,
            "timestamp": self.timestamp,
            "duration": self.duration
        }


class TaskGraph:
    """Manages task workflow visualization"""

    def __init__(self, task_name: str, save_dir: str = None):
        self.task_name = task_name
        self.save_dir = save_dir or os.path.join(os.getcwd(), "task_graphs")
        self.steps: List[TaskStep] = []
        self.graph = None
        self.status = "initialized"  # initialized, running, completed, failed

        # Create save directory if it doesn't exist
        os.makedirs(self.save_dir, exist_ok=True)

        # Initialize graph if visualization is available
        if HAS_VISUALIZATION:
            self.graph = nx.DiGraph()
            self.graph.add_node("start", label="Start", status="completed")
            self.graph.add_node("end", label="End", status="pending")

    def add_step(self, step_id: str, name: str, description: str,
                 depends_on: str = None) -> TaskStep:
        """Add a step to the workflow"""
        step = TaskStep(step_id, name, description)
        self.steps.append(step)

        if self.graph:
            # Add node
            self.graph.add_node(step_id, label=name, description=description,
                                status="pending", shape="box")

            # Add edge
            if depends_on:
                self.graph.add_edge(depends_on, step_id)
            else:
                self.graph.add_edge("start", step_id)

        return step

    def update_step(self, step_id: str, status: str, result: Any = None,
                    error: str = None, tool_used: str = None):
        """Update step status"""
        for step in self.steps:
            if step.step_id == step_id:
                step.status = status
                step.result = result
                step.error = error
                if tool_used:
                    step.tool_used = tool_used

                if self.graph and step_id in self.graph.nodes:
                    self.graph.nodes[step_id]["status"] = status
                    if status == "completed":
                        self.graph.add_edge(step_id, "end")
                break

    def complete(self, success: bool = True):
        """Mark the task as complete"""
        self.status = "completed" if success else "failed"
        if self.graph:
            self.graph.nodes["end"]["status"] = "completed" if success else "failed"

    def visualize(self, output_file: str = None) -> str:
        """Generate interactive visualization"""
        if not HAS_VISUALIZATION:
            return self._generate_text_summary()

        if not self.graph:
            return self._generate_text_summary()

        # Create PyVis network
        net = Network(notebook=False, height="600px", width="100%")
        net.from_nx(self.graph)

        # Style nodes based on status
        status_colors = {
            "pending": "#cccccc",
            "running": "#ffcc00",
            "completed": "#66cc66",
            "failed": "#cc6666",
            "initialized": "#6699cc"
        }

        for node in net.nodes:
            status = node.get("status", "pending")
            node["color"] = status_colors.get(status, "#cccccc")
            node["font"] = {"size": 12}

        # Save to file
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(self.save_dir,
                                       f"{self.task_name}_{timestamp}.html")

        net.save_graph(output_file)
        return output_file

    def _generate_text_summary(self) -> str:
        """Generate text summary when visualization is not available"""
        summary = f"Task: {self.task_name}\n"
        summary += f"Status: {self.status}\n"
        summary += f"Steps: {len(self.steps)}\n\n"

        for i, step in enumerate(self.steps, 1):
            status_icon = {
                "pending": "[ ]",
                "running": "[~]",
                "completed": "[x]",
                "failed": "[!]"
            }.get(step.status, "[?]")

            summary += f"{i}. {status_icon} {step.name}\n"
            summary += f"   {step.description}\n"
            if step.tool_used:
                summary += f"   Tool: {step.tool_used}\n"
            if step.error:
                summary += f"   Error: {step.error}\n"
            summary += "\n"

        return summary

    def to_json(self) -> str:
        """Export graph as JSON"""
        data = {
            "task_name": self.task_name,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
            "timestamp": datetime.now().isoformat()
        }
        return json.dumps(data, indent=2)

    def save(self, filename: str = None):
        """Save graph to file"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(self.save_dir,
                                    f"{self.task_name}_{timestamp}.json")

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(self.to_json())

        return filename

    def get_summary(self) -> Dict:
        """Get task summary statistics"""
        total = len(self.steps)
        completed = sum(1 for s in self.steps if s.status == "completed")
        failed = sum(1 for s in self.steps if s.status == "failed")
        pending = sum(1 for s in self.steps if s.status == "pending")

        return {
            "task_name": self.task_name,
            "status": self.status,
            "total_steps": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "completion_rate": f"{(completed /total *100):.1f}%" if total > 0 else "0%"
        }


class WorkflowManager:
    """Manages multiple task workflows"""

    def __init__(self, save_dir: str = None):
        self.save_dir = save_dir or os.path.join(os.getcwd(), "task_graphs")
        self.workflows: Dict[str, TaskGraph] = {}

    def create_workflow(self, task_name: str) -> TaskGraph:
        """Create a new workflow"""
        workflow = TaskGraph(task_name, self.save_dir)
        self.workflows[task_name] = workflow
        return workflow

    def get_workflow(self, task_name: str) -> Optional[TaskGraph]:
        """Get workflow by name"""
        return self.workflows.get(task_name)

    def list_workflows(self) -> List[Dict]:
        """List all workflows"""
        return [
            {
                "name": name,
                "status": workflow.status,
                "steps": len(workflow.steps)
            }
            for name, workflow in self.workflows.items()
        ]
