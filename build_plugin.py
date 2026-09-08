# -*- coding: utf-8 -*-
"""
QGIS 插件打包脚本

按 QGIS 官方插件标准格式打包 ZIP 安装包。
QGIS 插件 ZIP 格式要求：ZIP 根目录直接包含一个与插件同名的文件夹（如 qgis_agent/），
插件文件在该文件夹内。

过滤策略（两层）：
- EXCLUDE_PATTERNS：明确排除的目录 / 文件 / 通配符（测试、运行时数据、安装脚本等）。
- INCLUDE_PATTERNS：扩展名白名单，仅允许这些类型的文件进入包体，防止误打包临时 / 二进制文件。
- NO_EXT_ALLOW：无扩展名但必须打包的文件（如 LICENSE）。
"""

import os
import zipfile
from datetime import datetime
import logging
logger = logging.getLogger(__name__)


# 插件名称（同时作为 ZIP 内的顶层文件夹名）
PLUGIN_NAME = "qgis_agent"

# 允许打包的文件扩展名（白名单）
INCLUDE_PATTERNS = [
    "*.py", "*.json", "*.png", "*.svg", "*.ui", "*.qrc", "*.qm",
    "*.md", "*.txt", "*.toml", "*.html", "*.ico", "*.cfg", "*.ini",
]

# 无扩展名但允许打包的文件
NO_EXT_ALLOW = {"LICENSE"}

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
    ".codebuddy",
    ".workbuddy",
    ".github",
    "._*",  # macOS AppleDouble 元数据文件
    "*.egg-info",
    "build",
    "dist",
    ".pytest_cache",
    "data",  # 运行时生成的数据
    "tests",  # 测试文件
    "scripts",  # 脚本文件
    "help",  # 帮助文档源（rst/Makefile）
    # 注意：i18n 不再排除。这里放的是编译产物 *.qm（INCLUDE_PATTERNS 白名单已放行），
    # 源翻译 *.ts 不在白名单内，不会进包。此前排除 i18n 导致翻译文件永不进 ZIP。
    # ZIP / 旧版本文件
    "*.zip",
    "qgis_agent_v*",
    # 安装 / 构建脚本
    "install_to_qgis.bat",
    "install_to_qgis.ps1",
    "install_v2.py",
    "build_plugin.py",
    "test_official_docs.py",
    # 开发期文档（不随插件发布）
    "TEST_INSTRUCTIONS.md",
    "THINKING_DISPLAY_README.md",
    "requirements.txt",
    "V2.1_OPTIMIZATIONS.md",
    "RAG_SYSTEMS.md",
    "INTERACTIVE_FEATURES.md",
    "PLAN_*",  # 发布方案文档（如 PLAN_v2.1.3_RELEASE.md）
    "CLAUDE.md",  # 开发期项目说明（不随插件发布）
    "import_tool_docs.py",  # 开发期工具文档导入脚本
]


def should_include(filepath: str) -> bool:
    """判断文件是否应该包含进包体

    先按 EXCLUDE_PATTERNS 排除，再按 INCLUDE_PATTERNS 白名单放行。
    """
    filename = os.path.basename(filepath)

    # 1) 排除模式优先
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            # 通配符后缀，如 *.pyc
            if filename.endswith(pattern[1:]):
                return False
        elif pattern.endswith("*"):
            # 前缀匹配，如 qgis_agent_v* / PLAN_
            if filename.startswith(pattern[:-1]):
                return False
        else:
            # 精确匹配文件名，或目录名出现在路径中
            if filename == pattern or pattern in filepath.split(os.sep):
                return False

    # 2) 无扩展名文件仅允许白名单中的（如 LICENSE）
    if "." not in filename:
        return filename in NO_EXT_ALLOW

    # 3) 其余文件必须匹配扩展名白名单
    return any(filename.endswith(p[1:]) for p in INCLUDE_PATTERNS if p.startswith("*"))


def get_version() -> str:
    """从 metadata.txt 获取版本号（版本号的唯一真源）。

    解析失败时直接抛异常：宁可打包失败，也不能静默回退成错误版本号
    （版本号错误会导致 QGIS 插件仓库拒收）。
    """
    metadata_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "metadata.txt"
    )
    if not os.path.exists(metadata_path):
        raise RuntimeError(f"找不到 metadata.txt，无法确定插件版本号：{metadata_path}")

    # 用 utf-8-sig 兼容可能存在的 BOM
    with open(metadata_path, "r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith("["):
                continue  # 跳过 section 头
            if line.startswith("version="):
                version = line.split("=", 1)[1].strip()
                if not version:
                    raise RuntimeError(f"metadata.txt 中的 version 字段为空：{metadata_path}")
                return version

    raise RuntimeError(f"metadata.txt 中缺少 version 字段：{metadata_path}")


def build_plugin_zip():
    """构建插件 ZIP 包（QGIS 标准格式：顶层含插件名文件夹）"""
    version = get_version()
    timestamp = datetime.now().strftime("%Y%m%d")

    output_file = f"{PLUGIN_NAME}_v{version}_{timestamp}.zip"

    print(f"Building QGIS plugin: {PLUGIN_NAME} v{version}")
    print(f"Output: {output_file}")
    print("-" * 50)

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    files_to_pack = []

    for root, dirs, files in os.walk(plugin_dir):
        # 跳过排除的目录；同时一刀切跳过所有点开头的隐藏目录
        # （.workbuddy / .git / .github / .claude 等，避免把开发期记忆文件打进发布包）
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in EXCLUDE_PATTERNS
        ]
        # 跳过点开头的隐藏文件（macOS 的 ._xxx、.DS_Store、.env 等）
        files[:] = [f for f in files if not f.startswith(".")]

        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, plugin_dir)

            if should_include(rel_path):
                files_to_pack.append(rel_path)

    print(f"Files to pack: {len(files_to_pack)}")

    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filepath in sorted(files_to_pack):
            # ZIP 内路径：插件名/文件路径
            zip_path = f"{PLUGIN_NAME}/{filepath}"
            full_path = os.path.join(plugin_dir, filepath)
            zipf.write(full_path, zip_path)
            print(f"  + {zip_path}")

    print("-" * 50)
    print(f"Build complete: {output_file}")
    print(f"Size: {os.path.getsize(output_file) / 1024:.1f} KB")

    return output_file


def build_plugin_zip_flat():
    """向后兼容别名。

    本项目只产出标准 QGIS 格式（顶层包含与插件同名的文件夹），
    原先的 flat / 非 flat 两份产物内容完全一致，故合并为单一入口。
    """
    return build_plugin_zip()


if __name__ == "__main__":
    print("=" * 50)
    print("QGIS Plugin Builder")
    print("=" * 50)
    print()

    zip_path = build_plugin_zip()

    print()
    print("=" * 50)
    print("Build complete!")
    print("=" * 50)
    print()
    print("To install in QGIS:")
    print("1. Open QGIS")
    print("2. Go to Plugins -> Manage and Install Plugins")
    print("3. Click 'Install from ZIP'")
    print(f"4. Select {zip_path}")
