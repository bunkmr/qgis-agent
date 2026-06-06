.. QGIS Agent documentation master file

QGIS Agent 用户手册
========================

QGIS Agent 是一个将大语言模型 (LLM) 嵌入 QGIS 的智能助手插件，
用自然语言操控 QGIS，无需编写代码。

.. toctree::
   :maxdepth: 2
   :caption: 目录

   introduction
   installation
   usage
   tools
   models
   faq

快速开始
--------

1. **安装插件**: QGIS → 插件 → 管理并安装插件 → 从 ZIP 安装
2. **配置模型**: 在「模型配置」标签页添加 LLM（如 DeepSeek、OpenAI）
3. **新建对话**: 点击「+ 新建对话」创建新会话
4. **开始提问**: 用自然语言描述需求，Agent 自动调用工具完成

支持的功能
----------

- 🧠 **多模型支持**: DeepSeek、OpenAI、GLM、Gemini、MiMo 等
- 🛠 **11 个内置 QGIS 工具**: 图层管理、空间分析、地图渲染
- 💬 **对话持久化**: SQLite 存储，支持长期记忆
- ⚡ **线程安全**: 基于 QThreadPool，不阻塞 UI
- 🌐 **代码执行确认**: 执行 PyQGIS 代码前弹窗确认
- 🌡️ **Temperature 控制**: 可调节 LLM 创造力/精确度
