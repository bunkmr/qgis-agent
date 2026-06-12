# -*- coding: utf-8 -*-
"""
技能安装器 - 从 URL 或本地文件安装技能

支持:
- 从 URL 安装技能
- 从本地文件安装技能
- 从 Git 仓库安装技能
"""

import os
import json
import shutil
import urllib.request
import zipfile
import tempfile
from pathlib import Path
from typing import Optional

from .skill_manager import Skill, SkillManager, get_skill_manager


class SkillInstaller:
    """技能安装器"""

    def __init__(self, manager: SkillManager = None):
        self.manager = manager or get_skill_manager()
        self._skills_dir = self.manager._skills_dir

    def install_from_url(self, url: str, name: str = None) -> dict:
        """
        从 URL 安装技能

        Args:
            url: 技能包 URL（支持 .zip 或 .py 文件）
            name: 技能名称（可选）

        Returns:
            安装结果
        """
        try:
            # 下载文件
            if url.endswith(".py"):
                return self._install_python_file(url, name)
            elif url.endswith(".zip"):
                return self._install_zip_file(url, name)
            else:
                return {"success": False, "error": "Unsupported file type. Use .py or .zip"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def install_from_file(self, filepath: str) -> dict:
        """
        从本地文件安装技能

        Args:
            filepath: 技能文件路径

        Returns:
            安装结果
        """
        try:
            if not os.path.exists(filepath):
                return {"success": False, "error": f"File not found: {filepath}"}

            if filepath.endswith(".py"):
                # 复制 Python 文件
                dest = os.path.join(self._skills_dir, os.path.basename(filepath))
                shutil.copy2(filepath, dest)
                self.manager.load_user_skills()
                return {"success": True, "message": f"Installed skill from {filepath}"}
            elif filepath.endswith(".zip"):
                return self._extract_and_install_zip(filepath)
            else:
                return {"success": False, "error": "Unsupported file type"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def install_from_github(self, repo_url: str, skill_name: str = None) -> dict:
        """
        从 GitHub 仓库安装技能

        Args:
            repo_url: GitHub 仓库 URL
            skill_name: 技能名称（可选）

        Returns:
            安装结果
        """
        try:
            # 构建 ZIP 下载 URL
            if repo_url.endswith("/"):
                repo_url = repo_url[:-1]

            zip_url = f"{repo_url}/archive/refs/heads/main.zip"
            return self._install_zip_file(zip_url, skill_name)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _install_python_file(self, url: str, name: str = None) -> dict:
        """安装 Python 技能文件"""
        # 下载文件
        response = urllib.request.urlopen(url, timeout=30)
        content = response.read().decode("utf-8")

        # 确定文件名
        if not name:
            name = url.split("/")[-1].replace(".py", "")

        # 保存文件
        filepath = os.path.join(self._skills_dir, f"{name}.py")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # 重新加载技能
        self.manager.load_user_skills()

        return {"success": True, "message": f"Installed skill: {name}"}

    def _install_zip_file(self, url: str, name: str = None) -> dict:
        """安装 ZIP 技能包"""
        # 下载 ZIP 文件
        response = urllib.request.urlopen(url, timeout=30)
        zip_data = response.read()

        # 保存到临时文件
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(zip_data)
            tmp_path = tmp.name

        try:
            return self._extract_and_install_zip(tmp_path, name)
        finally:
            os.unlink(tmp_path)

    def _extract_and_install_zip(self, zip_path: str, name: str = None) -> dict:
        """解压并安装 ZIP 文件"""
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # 列出 ZIP 中的文件
            files = zip_ref.namelist()

            # 查找技能文件
            skill_files = [f for f in files if f.endswith(".py") and not f.startswith("__")]

            if not skill_files:
                return {"success": False, "error": "No Python skill files found in archive"}

            # 解压所有技能文件
            installed = []
            for skill_file in skill_files:
                # 读取文件内容
                content = zip_ref.read(skill_file).decode("utf-8")

                # 确定目标文件名
                filename = os.path.basename(skill_file)
                if name and len(skill_files) == 1:
                    filename = f"{name}.py"

                # 保存文件
                dest = os.path.join(self._skills_dir, filename)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)

                installed.append(filename)

            # 重新加载技能
            self.manager.load_user_skills()

            return {
                "success": True,
                "message": f"Installed {len(installed)} skill(s)",
                "files": installed
            }

    def list_installed(self) -> list[dict]:
        """列出已安装的用户技能"""
        skills = []
        if os.path.exists(self._skills_dir):
            for filename in os.listdir(self._skills_dir):
                if filename.endswith(".py") and not filename.startswith("_"):
                    filepath = os.path.join(self._skills_dir, filename)
                    skills.append({
                        "name": filename[:-3],
                        "path": filepath,
                        "size": os.path.getsize(filepath),
                    })
        return skills

    def uninstall(self, name: str) -> dict:
        """卸载技能"""
        filepath = os.path.join(self._skills_dir, f"{name}.py")
        if os.path.exists(filepath):
            os.remove(filepath)
            self.manager.unregister(name)
            return {"success": True, "message": f"Uninstalled skill: {name}"}
        return {"success": False, "error": f"Skill not found: {name}"}


# 示例技能模板
EXAMPLE_SKILL_TEMPLATE = '''# -*- coding: utf-8 -*-
"""
{{SKILL_NAME}} - {{DESCRIPTION}}
"""

from skills.skill_manager import Skill, SkillResult


def handler(**kwargs):
    """
    技能执行函数

    Args:
        **kwargs: 技能参数

    Returns:
        SkillResult: 执行结果
    """
    # TODO: 实现你的技能逻辑

    # 示例：返回成功结果
    return SkillResult(
        success=True,
        output=f"Hello from {{SKILL_NAME}}!",
        metadata={"skill": "{{SKILL_NAME}}"}
    )


# 技能定义
SKILL = Skill(
    name="{{SKILL_NAME}}",
    description="{{DESCRIPTION}}",
    version="1.0.0",
    author="Your Name",
    category="custom",
    parameters={
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "输入参数"
            }
        },
        "required": ["input"]
    },
    handler=handler,
    tags=["custom"],
)
'''
