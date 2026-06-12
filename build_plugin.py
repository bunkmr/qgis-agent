# -*- coding: utf-8 -*-
"""
QGIS 插件打包脚本

按照 QGIS 插件标准格式打包 ZIP 安装包。

QGIS 插件 ZIP 格式要求：
- ZIP 根目录直接包含插件文件（不要有额外的父目录）
- 文件结构清晰
"""

import os
import zipfile
import shutil
from pathlib import Path
from datetime import datetime


# 插件名称
PLUGIN_NAME = "qgis_agent"

# 要包含的文件和目录
INCLUDE_PATTERNS = [
    # Python 文件
    "*.py",
    # 资源文件
    "*.json",
    "*.png",
    "*.svg",
    "*.ui",
    # 文档
    "*.md",
    "*.txt",
    # 配置
    "*.cfg",
    "*.ini",
]

# 要排除的文件和目录
EXCLUDE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".git",
    ".gitignore",
    ".vscode",
    ".idea",
    ".claude",
    "*.egg-info",
    "build",
    "dist",
    ".pytest_cache",
    "data",  # 运行时生成的数据
    "agent_loop",  # 实验性模块（可选）
    "skills",  # 实验性模块（可选）
    "tests",  # 测试文件
    "scripts",  # 脚本文件
    "help",  # 帮助文档
    "i18n",  # 翻译文件（如果不需要）
    # ZIP 文件
    "*.zip",
    # 旧版本文件
    "qgis_agent_v*.zip",
    # 安装脚本
    "install_to_qgis.bat",
    "install_to_qgis.ps1",
    "install_v2.py",
    "build_plugin.py",
    "test_official_docs.py",
    "TEST_INSTRUCTIONS.md",
    "THINKING_DISPLAY_README.md",
    "requirements.txt",  # 使用 setup.py 或 pyproject.toml 代替
]


def should_include(filepath: str) -> bool:
    """判断文件是否应该包含"""
    filename = os.path.basename(filepath)

    # 检查排除模式
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            # 通配符模式，如 *.pyc
            if filename.endswith(pattern[1:]):
                return False
        elif pattern.endswith("*"):
            # 前缀匹配，如 qgis_agent_v*
            prefix = pattern[:-1]
            if filename.startswith(prefix):
                return False
        else:
            # 精确匹配文件名
            if filename == pattern:
                return False
            # 或者目录名匹配
            if pattern in filepath.split(os.sep):
                return False

    return True


def get_version() -> str:
    """从 config.py 获取版本号"""
    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
            for line in content.split("\n"):
                if "PLUGIN_VERSION" in line:
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "1.2.0"


def build_plugin_zip():
    """构建插件 ZIP 包"""
    # 获取版本号
    version = get_version()
    timestamp = datetime.now().strftime("%Y%m%d")

    # 输出文件名
    output_file = f"{PLUGIN_NAME}_v{version}_{timestamp}.zip"

    print(f"Building QGIS plugin: {PLUGIN_NAME} v{version}")
    print(f"Output: {output_file}")
    print("-" * 50)

    # 收集要打包的文件
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    files_to_pack = []

    for root, dirs, files in os.walk(plugin_dir):
        # 跳过排除的目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_PATTERNS]

        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, plugin_dir)

            if should_include(rel_path):
                files_to_pack.append(rel_path)

    print(f"Files to pack: {len(files_to_pack)}")

    # 创建 ZIP 文件
    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filepath in sorted(files_to_pack):
            # 在 ZIP 中的路径（直接在根目录下）
            zip_path = f"{PLUGIN_NAME}/{filepath}"
            full_path = os.path.join(plugin_dir, filepath)

            zipf.write(full_path, zip_path)
            print(f"  + {zip_path}")

    print("-" * 50)
    print(f"Build complete: {output_file}")
    print(f"Size: {os.path.getsize(output_file) / 1024:.1f} KB")

    return output_file


def build_plugin_zip_flat():
    """
    构建扁平化的 ZIP 包（QGIS 标准格式）

    这种格式下，ZIP 根目录直接包含插件文件，
    不需要额外的父目录。
    """
    # 获取版本号
    version = get_version()
    timestamp = datetime.now().strftime("%Y%m%d")

    # 输出文件名
    output_file = f"{PLUGIN_NAME}_v{version}_{timestamp}_flat.zip"

    print(f"Building QGIS plugin (flat format): {PLUGIN_NAME} v{version}")
    print(f"Output: {output_file}")
    print("-" * 50)

    # 收集要打包的文件
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    files_to_pack = []

    for root, dirs, files in os.walk(plugin_dir):
        # 跳过排除的目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_PATTERNS]

        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, plugin_dir)

            if should_include(rel_path):
                files_to_pack.append(rel_path)

    print(f"Files to pack: {len(files_to_pack)}")

    # 创建 ZIP 文件（扁平化格式）
    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filepath in sorted(files_to_pack):
            # 直接使用相对路径（不添加插件名前缀）
            full_path = os.path.join(plugin_dir, filepath)

            zipf.write(full_path, filepath)
            print(f"  + {filepath}")

    print("-" * 50)
    print(f"Build complete: {output_file}")
    print(f"Size: {os.path.getsize(output_file) / 1024:.1f} KB")

    return output_file


if __name__ == "__main__":
    print("=" * 50)
    print("QGIS Plugin Builder")
    print("=" * 50)
    print()

    # 构建两种格式
    zip1 = build_plugin_zip()
    print()
    zip2 = build_plugin_zip_flat()

    print()
    print("=" * 50)
    print("Build complete!")
    print("=" * 50)
    print()
    print("To install in QGIS:")
    print("1. Open QGIS")
    print("2. Go to Plugins -> Manage and Install Plugins")
    print("3. Click 'Install from ZIP'")
    print(f"4. Select {zip2}")
