# -*- coding: utf-8 -*-
"""
技能管理器 - 管理所有可用技能

核心功能:
- 技能注册和发现
- 技能执行
- 技能持久化
"""

import os
import json
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from pathlib import Path


@dataclass
class Skill:
    """技能定义"""
    name: str                        # 技能名称（唯一标识）
    description: str                 # 技能描述
    version: str = "1.0.0"           # 版本号
    author: str = ""                 # 作者
    category: str = "general"        # 分类

    # 技能参数 schema（JSON Schema 格式）
    parameters: dict = field(default_factory=dict)

    # 执行函数
    handler: Optional[Callable] = None

    # 元数据
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "category": self.category,
            "parameters": self.parameters,
            "tags": self.tags,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Skill':
        """从字典创建"""
        return cls(
            name=data["name"],
            description=data["description"],
            version=data.get("version", "1.0.0"),
            author=data.get("author", ""),
            category=data.get("category", "general"),
            parameters=data.get("parameters", {}),
            tags=data.get("tags", []),
            enabled=data.get("enabled", True),
        )


@dataclass
class SkillResult:
    """技能执行结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class SkillManager:
    """
    技能管理器

    管理所有技能的注册、发现和执行。
    """

    def __init__(self, skills_dir: str = None):
        """
        Args:
            skills_dir: 技能目录路径（用于加载自定义技能）
        """
        self._skills: dict[str, Skill] = {}
        self._skills_dir = skills_dir or os.path.join(os.path.dirname(__file__), "user_skills")

        # 确保技能目录存在
        os.makedirs(self._skills_dir, exist_ok=True)

    def register(self, skill: Skill):
        """注册技能"""
        self._skills[skill.name] = skill

    def unregister(self, name: str):
        """注销技能"""
        self._skills.pop(name, None)

    def get(self, name: str) -> Optional[Skill]:
        """获取技能"""
        return self._skills.get(name)

    def get_all(self) -> list[Skill]:
        """获取所有技能"""
        return list(self._skills.values())

    def get_enabled(self) -> list[Skill]:
        """获取所有启用的技能"""
        return [s for s in self._skills.values() if s.enabled]

    def get_by_category(self, category: str) -> list[Skill]:
        """获取指定分类的技能"""
        return [s for s in self._skills.values() if s.category == category]

    def execute(self, name: str, **kwargs) -> SkillResult:
        """
        执行技能

        Args:
            name: 技能名称
            **kwargs: 技能参数

        Returns:
            SkillResult: 执行结果
        """
        skill = self.get(name)
        if not skill:
            return SkillResult(success=False, error=f"Skill '{name}' not found")

        if not skill.enabled:
            return SkillResult(success=False, error=f"Skill '{name}' is disabled")

        if not skill.handler:
            return SkillResult(success=False, error=f"Skill '{name}' has no handler")

        try:
            result = skill.handler(**kwargs)
            if isinstance(result, SkillResult):
                return result
            return SkillResult(success=True, output=result)
        except Exception as e:
            return SkillResult(success=False, error=str(e))

    def load_user_skills(self):
        """从用户技能目录加载技能"""
        if not os.path.exists(self._skills_dir):
            return

        for filename in os.listdir(self._skills_dir):
            if filename.endswith(".py") and not filename.startswith("_"):
                skill_path = os.path.join(self._skills_dir, filename)
                try:
                    self._load_skill_file(skill_path)
                except Exception as e:
                    print(f"Failed to load skill {filename}: {e}")

    def _load_skill_file(self, filepath: str):
        """加载单个技能文件"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("skill_module", filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 查找并注册技能
        if hasattr(module, "SKILL"):
            skill = module.SKILL
            if isinstance(skill, Skill):
                self.register(skill)
        elif hasattr(module, "register"):
            module.register(self)

    def save_skill(self, skill: Skill, code: str):
        """保存技能到文件"""
        skill_file = os.path.join(self._skills_dir, f"{skill.name}.py")

        with open(skill_file, "w", encoding="utf-8") as f:
            f.write(f'''# -*- coding: utf-8 -*-
"""
{skill.description}
Version: {skill.version}
Author: {skill.author}
"""

from skills.skill_manager import Skill, SkillResult


def handler(**kwargs):
    """技能执行函数"""
    # TODO: 实现技能逻辑
    return SkillResult(success=True, output="Not implemented yet")


# 技能定义
SKILL = Skill(
    name="{skill.name}",
    description="{skill.description}",
    version="{skill.version}",
    author="{skill.author}",
    category="{skill.category}",
    parameters={json.dumps(skill.parameters, indent=4)},
    handler=handler,
)
''')

    def get_langchain_definitions(self) -> list[dict]:
        """获取所有启用技能的 LangChain 工具定义"""
        definitions = []
        for skill in self.get_enabled():
            if skill.handler:
                definitions.append({
                    "name": f"skill_{skill.name}",
                    "description": skill.description,
                    "parameters": skill.parameters,
                })
        return definitions

    def get_statistics(self) -> dict:
        """获取技能统计"""
        return {
            "total": len(self._skills),
            "enabled": len(self.get_enabled()),
            "categories": list(set(s.category for s in self._skills.values())),
        }


# 全局单例
_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """获取全局技能管理器"""
    global _manager
    if _manager is None:
        _manager = SkillManager()
    return _manager
