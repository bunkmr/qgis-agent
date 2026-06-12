# Skills 系统

QGIS Agent 的技能插件系统，支持扩展功能。

## 内置技能

| 技能 | 描述 | 分类 |
|------|------|------|
| `web_search` | 网络搜索（DuckDuckGo/Google/Bing） | search |
| `gis_data_search` | GIS 数据源搜索 | search |
| `format_results` | 格式化搜索结果 | utility |

## 安装技能

### 方法 1: 从 URL 安装

```python
from skills.installer import SkillInstaller

installer = SkillInstaller()

# 从 Python 文件安装
result = installer.install_from_url("https://example.com/my_skill.py")

# 从 ZIP 包安装
result = installer.install_from_url("https://example.com/my_skill.zip")

# 从 GitHub 仓库安装
result = installer.install_from_github("https://github.com/user/repo")
```

### 方法 2: 从本地文件安装

```python
result = installer.install_from_file("/path/to/my_skill.py")
```

### 方法 3: 手动安装

将 `.py` 文件复制到 `skills/user_skills/` 目录。

## 编写自定义技能

### 技能文件结构

```python
# -*- coding: utf-8 -*-
"""
我的自定义技能
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
    # 你的逻辑
    return SkillResult(
        success=True,
        output="结果"
    )


# 技能定义
SKILL = Skill(
    name="my_skill",
    description="技能描述",
    version="1.0.0",
    author="Your Name",
    category="custom",
    parameters={
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "参数描述"
            }
        },
        "required": ["param1"]
    },
    handler=handler,
    tags=["custom"],
)
```

### 参数 Schema

使用 JSON Schema 格式定义参数：

```python
parameters={
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索查询"
        },
        "limit": {
            "type": "integer",
            "description": "结果数量",
            "default": 10
        }
    },
    "required": ["query"]
}
```

### 返回值

使用 `SkillResult` 返回结果：

```python
# 成功
return SkillResult(
    success=True,
    output={"key": "value"},  # 可以是任何类型
    metadata={"extra": "info"}
)

# 失败
return SkillResult(
    success=False,
    error="错误信息"
)
```

## 使用技能

### 在 Agent Loop 中使用

```python
from skills import get_skill_manager

manager = get_skill_manager()

# 执行技能
result = manager.execute("web_search", query="QGIS tutorial")
print(result.output)
```

### 作为 LangChain 工具使用

```python
definitions = manager.get_langchain_definitions()
# 返回 LangChain 格式的工具定义
```

## 管理技能

```python
# 列出所有技能
skills = manager.get_all()

# 列出已安装的用户技能
installed = installer.list_installed()

# 卸载技能
installer.uninstall("my_skill")
```

## 示例技能

查看 `user_skills/example_weather.py` 了解如何编写技能。

## 技能市场（计划）

未来将支持从在线市场浏览和安装社区贡献的技能。

## 注意事项

1. 技能代码在 QGIS 主线程中执行，避免耗时操作
2. 网络请求需要设置超时
3. 敏感信息（API Key）应通过环境变量配置
4. 技能应处理异常并返回清晰的错误信息
