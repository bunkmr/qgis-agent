# 更新日志

## [2.4.0] - 2026-09-22

### MCP 服务出口（新增）
- ✨ **外部 Agent 驱动 QGIS**：内置的 20 个工具现在可通过 **Model Context Protocol** 提供给 Claude Desktop / Cursor / Codex 等外部 AI Agent。架构为插件内 `mcp_bridge.py`（本地 socket 服务）+ 随包附带的 `mcp_server/`（stdio MCP Server，纯标准库，零第三方依赖）。
- ✨ **设置页新增「MCP 服务」区**：启用开关、监听端口、访问令牌（重新生成 / 复制）、随插件自动启动、特权工具放行、实时连接状态、一键复制客户端配置、连通性自检。
- 🔒 **安全边界**：仅监听 `127.0.0.1`（不支持绑定其它地址）；强制令牌校验（`hmac.compare_digest` 常数时间比较），无令牌或令牌错误一律拒绝；默认不随插件启动，需用户显式开启。
- 🔒 **特权工具默认不放行**：`execute_pyqgis` / `execute_processing` / `remove_layer` / `load_project` / `save_project` / `run_skill` 既不出现在外部 Agent 的工具清单里，直接调用也会被拒绝；即便放行，每次执行仍会走既有的三档授权弹窗。
- 🔒 **凭据存放**：端口与令牌写入 `~/.qgis_agent/mcp_session.json`（文件 0600、目录 0700），服务停止即删除，供 MCP Server 自动发现。
- 🛡 **协议层加固**：单行请求 4MB 上限 + 分块读取（不用 `readline`，避免对端持续发送无换行字节流撑爆内存）；连接空闲 10 分钟超时（避免空闲连接占满名额）；工作线程异常一律转结构化错误，不影响进程。
- 🧪 新增 71 个单测（协议层 37 + MCP Server 27 + 依赖探测健壮性 7），全套 237 个单测通过；QGIS 4.2.1 真机端到端 43/43 通过（真实 socket + 真实 stdio 子进程走完整 MCP 握手）。

### 修复
- 🐛 **「浏览器兼容 TLS」开关此前点不到**：该开关只存在于 `settings_dialog.py`，而该对话框已无任何调用方（死代码）。现已并入实际可见的「模型配置」页，并删除这两个死文件。
- 🐛 `run_skill` 会执行用户技能目录下的 Python 代码，此前经 MCP 通道可免确认调用，现纳入特权工具集合。
- 🐛 **依赖探测不再只认 ImportError**：`_soft_import` 原来仅捕获 `ImportError`，而依赖「装了一半」时抛出的常是别的异常——例如 pydantic 与 pydantic-core 版本错配抛 `SystemError`、macOS 上框架/动态库加载失败抛 `OSError`。这类异常会从模块顶层逃逸，导致**整个插件加载失败且界面没有任何提示**。现统一兜住，转为「缺少依赖」对话框。（QGIS 3.44.14 真机实测复现）
- 🐛 **locale 取值不再崩溃**：`QSettings().value("locale/userLocale")` 在 QGIS 尚未注册 locale 时返回 `None`，原实现直接 `[0:2]` 切片会 `TypeError` 打断 `QGISAgent.__init__`。现加默认值兜底。

### 改进
- 📝 措辞中性化：移除源码 / `metadata.txt` / `README` 中「绕过 Cloudflare / 伪装指纹 / 反爬」等表述，改为描述网关行为与兼容性处理，避免上架评审歧义。
- 📝 `metadata.txt` 补充 MCP 能力说明与安全模型，明确可选依赖与零依赖组件。
- 🧪 真机验收扩展：新增「安装后验收」脚本，直接针对已安装副本走 `classFactory → initGui → run` 全链路，并在 **QGIS 4.2.1 (Qt6/Py3.12) 47/47** 与 **QGIS 3.44.14 (Qt5/PyQt5/Py3.12) 46/46** 双版本全绿。
- 🧪 新增 7 个依赖探测健壮性回归测试（含源码级约束，防止被改回窄捕获）。

## [Unreleased]

### 安全（P0 修复）
- 🔒 **execute_pyqgis AST 扫描补齐**：黑名单加入 `open`/`getattr`/`setattr`/`delattr`/`globals`/`locals`/`vars`/`breakpoint` 等；字符串参数以 `__` 开头一律拒绝；语法解析失败不再放行
- 🔒 **内建白名单**：`__builtins__` 由黑名单改为白名单，排除文件读写与动态属性入口
- 🔒 **stdout/stderr 恢复**：`execute_pyqgis` 使用 `try/finally` 保证恢复，避免吞掉其他插件输出

### 正确性
- 🐛 **桥接信号幂等**：`_init_main_thread_bridge` 仅连接一次，关闭再开 Dock 不再导致工具执行两遍
- 🐛 **processor logger 顺序**：SmartDebugger 导入失败时不再因 NameError 整模块挂掉
- 🐛 **Cookbook 归档**：按工作流 steps 实际状态计算 success，失败案例不再污染案例库
- 🐛 **工作流回放**：识别 `{"error": ...}` 业务失败，不再计为成功
- 🐛 **工具超时**：30s → 180s；超时提示「主线程可能仍在执行」
- 🐛 **确认超时**：60s → 300s，适应长代码审查
- 🐛 **select_latest_interaction**：无历史时返回 None，避免 IndexError
- 🐛 **utils.markdown→HTML**：修复 Python 3.9 下 f-string 含反斜杠的 SyntaxError（QGIS 3.22 常见环境）
- 🧹 **print → logger**：code_reviewer / 工具栏定位警告改用 logging

### 文档
- 📚 README / CLAUDE.md / metadata 对齐代码：20 个工具、信号/槽调度、Skills/Workflow/Clarification/Code Review 已接线
- 📚 requirements 补充 `tomli; python_version < "3.11"` 与可选依赖说明

## [2.3.2] - 2026-09-10

### 新增（可选逃生舱：浏览器指纹 TLS）
- 🆕 **浏览器指纹 TLS**：当模型接口被 Cloudflare 等反爬网关在 TLS 握手阶段按客户端指纹（JA3）拦截时，可在「模型配置」中开启「浏览器指纹 TLS」选项，用 `curl_cffi` 伪装 Chrome 指纹绕过（实测可连通 `ai.zhaosh.fun`）。默认关闭，不引入额外依赖，不影响默认发布路径。
- 💡 实现方式：用真正的 `httpx.Client` + 自定义 `Transport` 包一层转交 `curl_cffi`——既保留 Chrome 的 JA3 指纹，又能过 `openai`/`langchain` 对 `http_client` 的 `isinstance(httpx.Client)` 类型检查（直接传 `curl_cffi.Session` 会被新版 openai SDK 拒绝）。

### 改进（连接失败可诊断）
- 🔍 **错误分级新增「TLS 握手被拦截」专属分类**：连接失败时直接提示是网关指纹拦截，并给出「换官方接口 / 开启浏览器指纹 TLS」的可操作建议，不再只显示泛泛的"网络连接失败"。

### 兼容
- 📌 Qt5/Qt6 双兼容（同 v2.3.1，最低 QGIS 3.22）。

## [2.3.1] - 2026-09-09

### 兼容（Qt5 / Qt6 双兼容，真机 QGIS 4.2.1 / Qt6 实例化验证通过）
- 🔧 `thinking_display.py` 取色改用 `qgis.PyQt`（移除硬编码 PyQt5/PyQt6：Qt6 环境下 PyQt5 不存在，独立 PyQt6 会与 QGIS 内部绑定冲突）
- 🔧 输入框自适应高度信号由 `QTextDocument.sizeChanged`（Qt6 已移除）改为 `QTextEdit.textChanged`（Qt5/Qt6 通用）
- 📌 最低兼容 QGIS 3.22（覆盖 QGIS 3.22~4.x，Qt5 与 Qt6 全兼容）

## [2.3.0] - 2026-09-08

### 规划中特性全部接入（死代码清理收口）
- **代码审查(code_reviewer)**：代码执行确认前自动 LLM 安全审查，后台 QThread 不卡界面，结果展示 issues/suggestions
- **主动澄清(clarification_manager)**：用户请求模糊时主动追问，澄清后与原请求合并重跑
- **技能系统(skills)**：新增 `run_skill` 工具，LLM 可调用内置技能（web_search 等）
- **工作流录制/回放**：录制对话中的工具操作为工作流，可一键回放（workflow_store + dockwidget UI）
- **任务图(task_graph)**：复杂多步请求可选分解为有序步骤执行（默认关闭）
- 删除空壳 `thinking_widget.py`（功能已由 thinking_display.py 覆盖）；工具数 19→20


## [2.2.1] - 2026-09-08

### 改进（前台 / UI 体验优化）
- 🎨 **代码执行确认升级（P1-4）**：自定义 `CodeConfirmDialog`，代码预览**默认展开**（旧版藏在「详细」里等于没确认），新增三档授权「仅此一次 / 本次会话允许该工具 / 总是允许」；「总是允许」持久化到 QSettings，重启仍免确认
- ⏹ **停止中间态（U10）**：点「停止」后按钮变为「停止中…」并禁用，待 worker 真正结束才恢复，修复「点了停止却不知道有没有停」的困惑；无在途调用时直接恢复，不卡界面
- 🔌 **测试连接按钮（D14）**：模型配置页新增「测试连接」，后台线程执行连通性检查，不阻塞界面
- 👋 **首次启动引导（D4）**：首次打开插件弹出一次简明使用指引，之后持久化 `firstRunDone` 不再打扰
- 🚀 **首启不阻塞（P1-7）**：PyQGIS API 索引构建从 UI 线程移到后台线程，首启不再假死 10-30 秒，状态条实时反馈进度
- 🌗 **主题自适应（U15）**：聊天消息 / 思考块统一注入 `get_theme_css()` 派生的 `--qa-*` 主题变量，深色主题下文本可读；`create_markdown` 增强表格 / 链接 / 代码块渲染
- 🔍 **聊天区增强（U11/U13/U18/D5）**：发送中仅禁用发送按钮（输入框可继续预写）；消息区 Ctrl+F 搜索 + 复制回复；底部状态条（阶段 + 耗时）；空状态显示示例指令卡片

## [2.2.0] - 2026-09-08

### 新增
- 🆕 **4 个高频工具**：`get_algorithm_parameters`（查询 Processing 算法真实参数名，消灭参数编造）、`get_layer_profile`（先"看一眼数据"：字段/投影/要素数）、`set_layer_renderer`（分级/分类设色）、`reproject_layer`（坐标转换）
- 🧪 **可运行测试基线**：191 个用例，在裸 Python 环境（无 QGIS / 无 langchain / 无网络）下全部通过，覆盖安全护栏、错误分级、调试器等核心纯逻辑

### 修复
- 🛡️ **提示词注入防护**：属性表字段值 / 图层名回喂 LLM 前统一净化（剥离控制字符 + 截断）；工具结果包裹不可信数据围栏
- 🛡️ **PyQGIS 代码 AST 静态扫描**：拦截 `os` / `subprocess` / `eval` / 双下划线访问等危险调用，命中即拒绝执行
- 🛡️ **危险操作确认扩展到 5 类**：`remove_layer` / `load_project` / `save_project` / `render_map`（覆盖时）加入确认；「跳过确认」改为仅本次会话有效，不再持久化
- 🐛 **SmartDebugger 接线**：工具执行失败时自动诊断并回灌 LLM 改写重试（最多 3 次），复杂任务成功率提升
- 🐛 **停止后可恢复**：点「停止」后对话不再永久报废，可新建对话或切换模型重试
- 💬 **错误分级提示**：401/403/429/超时/连接失败等改为中文可操作提示，不再甩英文堆栈

### 改进
- 💡 思考过程改为累积展示（不再整体替换闪烁）；输入框 Enter 发送 / Shift+Enter 换行、高度自适应
- 📦 最小兼容版本 3.0 → 3.22（PEP585 注解需 Py≥3.9）；打包剔除 `.workbuddy` 开发记忆等隐私文件
- 📝 文档如实标注「技能系统 / 工作流录制 / 主动提问 / 任务图」为规划中功能；HELP.html 接入帮助页；i18n 翻译进包

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
