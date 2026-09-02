# 更新日志

## [2.1.3] - 2026-09-02

### 新增
- 🆕 **QGIS 4（PyQt6）兼容**：PyQt5 / PyQt6 双兼容，插件可在 QGIS 3.x 与 4.x（含 macOS QGIS 4）中加载运行
- 🆕 **本地 / 自托管模型免密**：自定义 OpenAI 兼容端点的 API Key 可留空（Ollama / vLLM / llama.cpp 等）
- 🛡️ **Cloudflare 403 自动规避**：调用 LLM 时自动附加浏览器 `User-Agent`，绕过 Cloudflare Bot 防护对 Python 客户端的拦截

### 修复
- 🐛 修复「点击发送无反应」：无活动对话时自动创建对话；主线程构造异常与 LLM 调用超时被改为可见红字提示，不再被 Qt 静默吞掉
- 🐛 修复 PyQt6 枚举未限定（QHeaderView / QEvent / QPalette / QDialog / QMessageBox 等）导致的 DockWidget 崩溃
- 🐛 修复 pydantic-core 版本冲突（用户 site 遮蔽 QGIS 自带版本）导致插件无法加载
- 🐛 修复 `QPalette.Base` 等属性缺失报错

### 改进
- 💬 对话名称默认取首条消息前 20 字，不再强制弹窗要求命名
- 🧠 RAG 组件（DocStore / Retriever / Cookbook 等）构造失败时优雅降级为 None，不阻塞对话
- ⏱️ LLM 调用统一设置 `timeout=180, max_retries=1`，避免端点不通时 worker 永久挂起
- 📦 打包脚本改用显式包含白名单；ZIP 顶层为标准 `qgis_agent/` 目录

## [2.1.2] - 2026-06-19

### 修复
- 🐛 **修复 RAG 模块导入错误**: 解决 ZIP 安装后 `ModuleNotFoundError: No module named 'qgis_agent.rag'` 问题
- 🐛 **修复子插件误判**: 移除 `rag/`、`agent_loop/`、`skills/` 目录的 `__init__.py`，避免 QGIS 扫描器将其误判为子插件

### 改进
- 将子包的相对导入改为绝对导入（`from .rag` → `from qgis_agent.rag`）
- 清理不必要的依赖（移除 `requests`、`langchain`）
- 更新打包脚本，ZIP 包现在包含 `rag/`、`agent_loop/`、`skills/` 模块

## [1.2.0] - 2026-06-06

### 新增
- 📚 **RAG API 文档检索**: 本地 SQLite FTS5 全文搜索引擎，在执行 PyQGIS 代码前自动检索相关 API 签名和参数信息
- 🧬 **Cookbook 自我进化**: 成功任务自动归档为案例，执行前检索相似案例提供参考，越用越聪明
- 🔍 **search_pyqgis_api 工具**: LLM 可主动调用此工具查询 PyQGIS/GDAL/Processing API 文档
- 📖 **API 文档生成器**: 从 QGIS 运行时反射 + Processing 注册表 + 手动补充三个来源提取 API 文档

### 改进
- `Processor.__init__()` 集成 `DocStore`、`APIDocRetriever`、`Cookbook` 组件
- `agent_chat()` 在执行危险工具前自动触发 RAG 检索
- `agent_chat()` 开始前检索 Cookbook 相似案例并注入 system prompt
- `agent_chat()` 结束后自动归档成功案例到 Cookbook
- System prompt 增加 search_pyqgis_api 使用指导和 Cookbook 参考说明
- 工具数量从 14 增加到 15（新增 search_pyqgis_api）

### 新增文件
- `rag/` — RAG 模块目录
  - `rag/__init__.py` — 模块入口
  - `rag/doc_store.py` — SQLite FTS5 文档存储（API 文档 + Cookbook）
  - `rag/retriever.py` — API 文档检索器（关键词提取 + FTS5 搜索）
  - `rag/doc_generator.py` — 文档生成器（inspect 反射 + Processing 算法 + 手动补充）
  - `rag/cookbook.py` — Cookbook 自我进化（自动归档 + 检索 + 质量评分）
- `scripts/build_api_index.py` — 独立脚本：构建 API 文档索引
- `data/pyqgis_api.db` — API 文档 SQLite 数据库（自动生成）

## [1.1.0] - 2026-06-06

### 新增
- 🔒 **代码安全确认**: 执行 PyQGIS 代码和 Processing 算法前弹窗确认（借鉴 QGPT Agent）
- 🌡️ **Temperature 控制**: 底部滑块调节 LLM 输出创造性（0.0=精确, 1.0=创造）
- 🌐 **国际化支持**: 中文翻译文件 (`i18n/` 目录)
- 📚 **Sphinx 文档**: 完整中文帮助文档 (`help/` 目录)，包含安装指南、使用手册、工具参考、FAQ

### 改进
- `call_tool()` 新增危险工具确认机制，通过 `_MainThreadBridge` 的 `confirm_request` 信号在主线程弹窗
- `Processor.__init__()` 新增 `temperature` 参数和 `_code_confirm_callback`
- DockWidget 底部栏新增 Temperature 滑块 (`sliderTemperature`) 和值显示 (`lblTempValue`)
- 全局代码确认回调 `set_code_confirm_callback()` 在 `qgis_tools.py` 中注册

## [1.0.0] - 2026-06-06

### 新增
- 初始版本发布
- 11 个内置 QGIS 工具（图层管理、数据处理、地图渲染等）
- 多 LLM 支持（DeepSeek、OpenAI、智谱 GLM、Gemini、小米 MiMo）
- 对话持久化（SQLite 存储）
- 长期记忆机制（MEMORY.md）
- 线程安全架构（QThreadPool + 主线程调度桥）
- 三标签页停靠面板（对话 / 对话列表 / 模型配置）
- 依赖自动安装管理
- 图层标注设置工具
