# -*- coding: utf-8 -*-
"""
QGIS 工具适配器 — 将现有 qgis_tools.py 工具注册到 ToolRegistry。

提供便捷函数来初始化所有 QGIS 工具。
"""

from ..qgis_tools import (
    TOOL_DEFINITIONS,
    get_qgis_info,
    get_layer_features,
    add_vector_layer,
    add_raster_layer,
    remove_layer,
    zoom_to_layer,
    execute_processing,
    execute_pyqgis,
    save_project,
    load_project,
    set_layer_labeling,
    render_map,
    search_pyqgis_api,
    save_memory,
    load_memory,
)
from .tools import Tool, ToolRegistry, get_tool_registry


def register_qgis_tools(registry: ToolRegistry = None) -> ToolRegistry:
    """
    注册所有 QGIS 工具到 ToolRegistry。

    Args:
        registry: 工具注册表（可选，默认使用全局注册表）

    Returns:
        ToolRegistry: 工具注册表实例
    """
    if registry is None:
        registry = get_tool_registry()

    # 从 TOOL_DEFINITIONS 构建工具定义映射
    defs_map = {d["name"]: d for d in TOOL_DEFINITIONS}

    # 工具元数据
    _TOOL_META = {
        "get_qgis_info": {
            "category": "info",
            "requires_confirm": False,
            "dangerous": False,
        },
        "get_layer_features": {
            "category": "info",
            "requires_confirm": False,
            "dangerous": False,
        },
        "add_vector_layer": {
            "category": "layer",
            "requires_confirm": False,
            "dangerous": False,
        },
        "add_raster_layer": {
            "category": "layer",
            "requires_confirm": False,
            "dangerous": False,
        },
        "remove_layer": {
            "category": "layer",
            "requires_confirm": False,
            "dangerous": False,
        },
        "zoom_to_layer": {
            "category": "view",
            "requires_confirm": False,
            "dangerous": False,
        },
        "execute_processing": {
            "category": "processing",
            "requires_confirm": True,
            "dangerous": True,
        },
        "execute_pyqgis": {
            "category": "code",
            "requires_confirm": True,
            "dangerous": True,
        },
        "save_project": {
            "category": "project",
            "requires_confirm": False,
            "dangerous": False,
        },
        "load_project": {
            "category": "project",
            "requires_confirm": False,
            "dangerous": False,
        },
        "set_layer_labeling": {
            "category": "style",
            "requires_confirm": False,
            "dangerous": False,
        },
        "render_map": {
            "category": "export",
            "requires_confirm": False,
            "dangerous": False,
        },
        "search_pyqgis_api": {
            "category": "rag",
            "requires_confirm": False,
            "dangerous": False,
        },
        "save_memory": {
            "category": "memory",
            "requires_confirm": False,
            "dangerous": False,
        },
        "load_memory": {
            "category": "memory",
            "requires_confirm": False,
            "dangerous": False,
        },
    }

    # 工具处理函数映射
    _HANDLERS = {
        "get_qgis_info": get_qgis_info,
        "get_layer_features": get_layer_features,
        "add_vector_layer": add_vector_layer,
        "add_raster_layer": add_raster_layer,
        "remove_layer": remove_layer,
        "zoom_to_layer": zoom_to_layer,
        "execute_processing": execute_processing,
        "execute_pyqgis": execute_pyqgis,
        "save_project": save_project,
        "load_project": load_project,
        "set_layer_labeling": set_layer_labeling,
        "render_map": render_map,
        "search_pyqgis_api": search_pyqgis_api,
        "save_memory": save_memory,
        "load_memory": load_memory,
    }

    # 注册所有工具
    for name, handler in _HANDLERS.items():
        if name not in defs_map:
            continue

        defn = defs_map[name]
        meta = _TOOL_META.get(name, {})

        tool = Tool(
            name=name,
            description=defn["description"],
            parameters=defn["parameters"],
            handler=handler,
            category=meta.get("category", "general"),
            requires_confirm=meta.get("requires_confirm", False),
            dangerous=meta.get("dangerous", False),
        )

        registry.register(tool)

    return registry


def get_qgis_tools_summary() -> str:
    """获取 QGIS 工具摘要信息"""
    registry = get_tool_registry()
    stats = registry.get_statistics()

    lines = [f"共注册 {stats['total_tools']} 个工具:"]
    for category in stats["categories"]:
        tools = registry.get_by_category(category)
        tool_names = [t.name for t in tools]
        lines.append(f"  [{category}] {', '.join(tool_names)}")

    return "\n".join(lines)
