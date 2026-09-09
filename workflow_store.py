# -*- coding: utf-8 -*-
"""
工作流录制 / 回放存储（JSON 文件）。

存储路径：用户主目录下 .qgis_agent/workflows（用 os.path.expanduser + 插件名拼出），
不依赖 PyQt / QGIS，可在裸 Python 环境中导入与单测。

设计要点：
- 录制：start_recording() 打开开关并清空缓冲；每次工具成功后 record_step() 把
  (tool_name + args + result) 追加进当前缓冲；stop_recording() 关闭开关。
- 保存：stop_recording() 后由调用方用 save_workflow(name, steps) 命名保存（steps 通常用当前缓冲）。
- 回放：run_workflow(name, tool_executor) 读取已保存步骤，按序调用 tool_executor 重放。

注意：本文件不引入任何新第三方依赖，不使用 subprocess / eval / os.system。
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# 插件名（拼接存储目录用）
_PLUGIN_NAME = "qgis_agent"
# 默认存储目录：~/.qgis_agent/workflows
_DEFAULT_STORE_DIR = os.path.join(os.path.expanduser("~"), "." + _PLUGIN_NAME, "workflows")


class WorkflowStore:
    """基于 JSON 文件的工作流录制/回放存储。"""

    def __init__(self, store_dir: str = None):
        self.store_dir = store_dir or _DEFAULT_STORE_DIR
        # 确保存储目录存在（文件不存在则创建）
        try:
            os.makedirs(self.store_dir, exist_ok=True)
        except Exception as _e:
            logger.debug("创建工作流目录失败: %s", _e, exc_info=True)

        self._recording = False
        # 当前录制缓冲：list[{"tool_name", "args", "result"}]
        self._buffer: list = []

    # ── 录制开关 ──
    def start_recording(self):
        """开始录制：打开开关并清空缓冲。"""
        self._recording = True
        self._buffer = []

    def stop_recording(self):
        """停止录制：关闭开关（缓冲保留，等待调用方 save_workflow 命名保存）。"""
        self._recording = False

    def is_recording(self) -> bool:
        """当前是否处于录制状态。"""
        return self._recording

    def get_recording_buffer(self) -> list:
        """返回当前录制缓冲的副本（供调用方命名保存）。"""
        return list(self._buffer)

    # ── 录制步骤 ──
    def record_step(self, tool_name, args, result):
        """把一次成功工具调用追加进当前录制缓冲（仅在录制中生效）。"""
        if not self._recording:
            return
        try:
            # result 可能是任意对象（dict / str 等），尽量转为可序列化结构保存
            if isinstance(result, dict):
                saved_result = result
            else:
                saved_result = str(result)
            self._buffer.append({
                "tool_name": tool_name,
                "args": args if isinstance(args, (dict, list)) else {"__raw__": str(args)},
                "result": saved_result,
            })
        except Exception as _e:
            logger.debug("录制步骤失败（已忽略）: %s", _e, exc_info=True)

    # ── 持久化 ──
    def _workflow_path(self, name: str) -> str:
        """把工作流名称映射为安全文件名。"""
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))
        if not safe:
            safe = "workflow"
        return os.path.join(self.store_dir, safe + ".json")

    def save_workflow(self, name: str, steps: list):
        """保存一组步骤为命名工作流（JSON 文件）。"""
        path = self._workflow_path(name)
        data = {
            "name": name,
            "steps": steps if isinstance(steps, list) else [],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as _e:
            logger.debug("保存工作流失败: %s", _e, exc_info=True)
            raise

    def list_workflows(self) -> list:
        """列出所有已保存工作流的名称（按文件名排序）。"""
        try:
            names = []
            for fn in os.listdir(self.store_dir):
                if fn.endswith(".json"):
                    names.append(fn[: -len(".json")])
            return sorted(names)
        except Exception as _e:
            logger.debug("列举工作流失败: %s", _e, exc_info=True)
            return []

    def load_workflow(self, name: str) -> list:
        """读取命名工作流的步骤列表；不存在返回空列表。"""
        path = self._workflow_path(name)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("steps", []) if isinstance(data, dict) else []
        except Exception as _e:
            logger.debug("读取工作流失败: %s", _e, exc_info=True)
            return []

    # ── 回放 ──
    def run_workflow(self, name: str, tool_executor) -> dict:
        """按序重放已保存工作流；tool_executor 形如 call_tool(tool_name, args) -> result。

        返回汇总 dict：{name, total, success, results:[{tool_name, args, result, ok}]}
        """
        steps = self.load_workflow(name)
        results = []
        ok_count = 0
        for step in steps:
            tool_name = step.get("tool_name")
            args = step.get("args", {}) or {}
            try:
                res = tool_executor(tool_name, args)
                results.append({"tool_name": tool_name, "args": args, "result": res, "ok": True})
                ok_count += 1
            except Exception as _e:
                logger.debug("回放步骤失败: %s", _e, exc_info=True)
                results.append({"tool_name": tool_name, "args": args, "result": f"(回放失败: {_e})", "ok": False})
        return {
            "name": name,
            "total": len(steps),
            "success": ok_count,
            "results": results,
        }
