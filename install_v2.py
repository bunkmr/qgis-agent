# -*- coding: utf-8 -*-
"""
安装脚本 - 启用可折叠思考显示功能

自动将新文件复制到 QGIS 插件目录，并修改必要的导入语句。
"""

import os
import shutil
from pathlib import Path

# QGIS 插件目录
QGIS_PLUGIN_DIR = Path(os.path.expanduser("~")) / "AppData/Roaming/QGIS/QGIS3/profiles/default/python/plugins/qgis_agent"

# 项目目录
PROJECT_DIR = Path(__file__).parent


def copy_file(src_name: str, dst_name: str = None):
    """复制文件到 QGIS 插件目录"""
    src = PROJECT_DIR / src_name
    dst = QGIS_PLUGIN_DIR / (dst_name or src_name)

    if not src.exists():
        print(f"[SKIP] Source file not found: {src}")
        return False

    try:
        shutil.copy2(src, dst)
        print(f"[OK] Copied: {src_name}")
        return True
    except Exception as e:
        print(f"[FAIL] Copy failed: {src_name} - {e}")
        return False


def modify_qgis_agent():
    """修改 qgis_agent.py 以使用新的 DockWidget"""
    qgis_agent_path = QGIS_PLUGIN_DIR / "qgis_agent.py"

    if not qgis_agent_path.exists():
        print(f"[ERROR] File not found: {qgis_agent_path}")
        return False

    try:
        with open(qgis_agent_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否已经修改过
        if 'qgis_agent_dockwidget_v2' in content:
            print("[INFO] qgis_agent.py already using v2 version")
            return True

        # 查找导入语句
        old_import = "from .qgis_agent_dockwidget import QGISAgentDockWidget"
        new_import = """try:
    from .qgis_agent_dockwidget_v2 import QGISAgentDockWidgetV2 as QGISAgentDockWidget
except ImportError:
    from .qgis_agent_dockwidget import QGISAgentDockWidget"""

        if old_import in content:
            content = content.replace(old_import, new_import)
            with open(qgis_agent_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print("[OK] Modified qgis_agent.py")
            return True
        else:
            print("[SKIP] Import statement not found, please modify manually")
            return False

    except Exception as e:
        print(f"[FAIL] Modification failed: {e}")
        return False


def main():
    """主安装流程"""
    print("=" * 50)
    print("Install Collapsible Thinking Display")
    print("=" * 50)
    print()

    # 检查 QGIS 插件目录
    if not QGIS_PLUGIN_DIR.exists():
        print(f"[ERROR] QGIS plugin directory not found: {QGIS_PLUGIN_DIR}")
        print("Please ensure QGIS is installed and plugin directory exists")
        return

    print(f"[INFO] QGIS plugin directory: {QGIS_PLUGIN_DIR}")
    print()

    # 复制文件
    print("[STEP] Copying files...")
    files_to_copy = [
        ("thinking_display.py", None),
        ("qgis_agent_dockwidget_v2.py", None),
    ]

    success_count = 0
    for src_name, dst_name in files_to_copy:
        if copy_file(src_name, dst_name):
            success_count += 1

    print()

    # 修改 qgis_agent.py
    print("[STEP] Modifying configuration...")
    if modify_qgis_agent():
        success_count += 1

    print()
    print("=" * 50)

    if success_count == len(files_to_copy) + 1:
        print("[OK] Installation completed!")
        print()
        print("Please restart QGIS to enable the new feature.")
    else:
        print("[WARNING] Some installations failed, please check error messages")

    print("=" * 50)


if __name__ == "__main__":
    main()
