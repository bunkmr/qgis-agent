"""
Workflow Executor for QGIS Agent
Executes saved workflow templates in new projects.
"""

import json
from typing import Dict
from datetime import datetime


class WorkflowExecutor:
    """Executes workflow templates"""

    def __init__(self, tool_executor=None):
        self.tool_executor = tool_executor
        self.execution_history = []

    def execute_workflow(self, template, parameters: Dict = None,
                         dry_run: bool = False) -> Dict:
        """
        Execute a workflow template

        Args:
            template: WorkflowTemplate to execute
            parameters: Parameters to substitute in workflow steps
            dry_run: If True, only show what would be executed

        Returns:
            Execution results
        """
        results = {
            "workflow_name": template.name,
            "started_at": datetime.now().isoformat(),
            "steps": [],
            "success": True,
            "output_files": []
        }

        print(f"Executing workflow: {template.name}")
        print(f"Steps: {len(template.steps)}")

        for step in template.steps:
            print(f"\n--- Step {step.step_id}: {step.tool_name} ---")

            # Substitute parameters in tool args
            step_args = self._substitute_parameters(step.tool_args, parameters or {})

            if dry_run:
                print(f"[DRY RUN] Would execute: {step.tool_name}")
                print(f"  Args: {json.dumps(step_args, indent=4)}")
                results["steps"].append({
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "status": "dry_run",
                    "args": step_args
                })
                continue

            # Execute tool
            try:
                if self.tool_executor:
                    result = self.tool_executor(step.tool_name, step_args)
                else:
                    result = {"executed": True, "mock": True}

                # Track output files
                if "OUTPUT" in step_args:
                    results["output_files"].append(step_args["OUTPUT"])

                results["steps"].append({
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "status": "success",
                    "result": result
                })

                print("✓ Step completed successfully")

            except Exception as e:
                results["steps"].append({
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "status": "failed",
                    "error": str(e)
                })
                results["success"] = False
                print(f"✗ Step failed: {e}")
                break

        results["completed_at"] = datetime.now().isoformat()
        self.execution_history.append(results)

        return results

    def _substitute_parameters(self, args: Dict, parameters: Dict) -> Dict:
        """Substitute parameters in tool arguments"""
        substituted = {}

        for key, value in args.items():
            if isinstance(value, str) and value.startswith("${"):
                # Extract parameter name
                param_name = value[2:-1]
                substituted[key] = parameters.get(param_name, value)
            else:
                substituted[key] = value

        return substituted

    def generate_workflow_code(self, template, parameters: Dict = None) -> str:
        """Generate Python code for executing the workflow"""
        code = f"""# Workflow: {template.name}
# Description: {template.description}
# Generated at: {datetime.now().isoformat()}

import processing
from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer

def execute_workflow():
    \"\"\"
    Execute workflow: {template.name}
    \"\"\"
    results = {{}}

"""

        for step in template.steps:
            step_args = self._substitute_parameters(step.tool_args, parameters or {})
            code += f"    # Step {step.step_id}: {step.description}\n"
            code += f"    print('Executing: {step.tool_name}')\n"

            if step.tool_name.startswith("native:") or step.tool_name.startswith("gdal:"):
                # Processing algorithm
                code += f"    result_{step.step_id} = processing.run('{step.tool_name}', {{\n"
                for key, value in step_args.items():
                    code += f"        '{key}': {repr(value)},\n"
                code += "    })\n"
                code += f"    results['{step.step_id}'] = result_{step.step_id}\n\n"
            else:
                # Custom tool
                code += f"    # Custom tool: {step.tool_name}\n"
                code += f"    results['{step.step_id}'] = {{'tool': '{step.tool_name}', 'args': {step_args}}}\n\n"

        code += """    print('Workflow completed!')
    return results

# Execute the workflow
if __name__ == '__main__':
    results = execute_workflow()
"""
        return code
