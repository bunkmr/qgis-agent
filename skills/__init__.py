# -*- coding: utf-8 -*-
"""
Skills 系统 - 可扩展的技能插件架构

支持:
- 内置技能（网络搜索、数据分析等）
- 自定义技能（用户/社区贡献）
- 技能安装和管理
"""

from .skill_manager import SkillManager, Skill, SkillResult
from .builtins import register_builtin_skills

__all__ = ["SkillManager", "Skill", "SkillResult", "register_builtin_skills"]
