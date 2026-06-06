# QGIS Agent 项目上下文

> QGIS 桌面插件，将 LLM Agent 嵌入 QGIS，支持通过自然语言调用 15 个 QGIS 工具完成地理空间操作。集成 RAG API 文档检索和 Cookbook 自我进化机制。

---

## 项目架构总览

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#4a9eff', 'primaryTextColor': '#fff', 'primaryBorderColor': '#2b7cd3', 'lineColor': '#5a6a7a', 'secondaryColor': '#ffa726', 'tertiaryColor': '#e8f4fd'}}}%%
graph TB
    subgraph UI["🖥️ QGIS 主线程"]
        direction LR
        A["🧩 QGISAgent<br/><small>qgis_agent.py</small>"]
        B["🪟 DockWidget<br/><small>qgis_agent_dockwidget.py</small>"]
        C["💬 Conversation<br/><small>conversation.py</small>"]
    end

    subgraph WORKER["⚙️ 工作线程 (QThreadPool)"]
        direction LR
        D["🔧 ToolAgentWorker<br/><small>response_worker.py</small>"]
        E["🧠 Processor.agent_chat()<br/><small>processor.py</small>"]
    end

    subgraph TOOLS["🔩 主线程调度"]
        direction LR
        F["📞 call_tool()<br/><small>qgis_tools.py</small>"]
        G["🧰 15 QGIS 工具函数"]
        H["🗺️ QGIS API<br/><small>QgsProject / iface / Processing</small>"]
    end

    subgraph EXT["☁️ 外部"]
        I["🤖 LLM API<br/><small>DeepSeek / OpenAI 兼容</small>"]
    end

    A -->|创建/管理| B
    A -->|创建/管理| C
    C -->|async_response()| D
    D -->|在线程中运行| E
    E -->|LLM API 调用| I
    E -->|跨线程调用工具| F
    F -->|QTimer.singleShot 调度到主线程| G
    G -->|操作| H
    E -->|thinking 信号| B
    E -->|finished 信号| A

    style UI fill:#e3f2fd,stroke:#1976d2,stroke-width:2px,color:#1565c0
    style WORKER fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100
    style TOOLS fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#2e7d32
    style EXT fill:#fce4ec,stroke:#c62828,stroke-width:2px,color:#b71c1c
    style A fill:#1976d2,stroke:#0d47a1,color:#fff
    style E fill:#ff6b6b,stroke:#c62828,color:#fff
    style F fill:#ffa726,stroke:#e65100,color:#fff
    style G fill:#66bb6a,stroke:#2e7d32,color:#fff
```

---

## 调用链路详解

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'actorTextColor': '#1a1a2e', 'actorBkg': '#e3f2fd', 'actorBorder': '#1976d2', 'actorLineColor': '#1976d2', 'signalColor': '#455a64', 'signalTextColor': '#1a1a2e', 'labelBoxBkgColor': '#fff3e0', 'labelBoxBorderColor': '#f57c00', 'labelTextColor': '#e65100', 'loopTextColor': '#1565c0', 'noteBkgColor': '#e8f5e9', 'noteBorderColor': '#388e3c', 'noteTextColor': '#2e7d32'}}}%%
sequenceDiagram
    autonumber
    actor User as 👤 用户
    participant D as 🪟 DockWidget
    participant A as 🧩 QGISAgent
    participant C as 💬 Conversation
    participant P as 🧠 Processor
    participant W as 🔧 ToolAgentWorker
    participant T as 📞 call_tool
    participant Q as 🗺️ QGIS API

    User->>D: 输入消息 + 回车
    D->>A: _on_new_message_send()
    A->>D: 显示用户消息 HTML
    A->>C: update_user_prompt(message)
    C->>P: async_response(message)
    P->>W: 创建 Worker 投入线程池

    rect rgb(255, 243, 224)
        Note over W: ⚙️ 工作线程开始
    end

    W->>P: agent_chat(user_input, callbacks)

    P->>P: 📄 加载 MEMORY.md 长期记忆
    P->>P: 🗃️ 加载 SQLite 对话历史
    P->>P: 📦 组装 messages = [System + History + User]

    loop 🔄 最多 10 轮工具调用
        P->>P: llm.bind_tools(TOOL_DEFINITIONS).invoke(messages)
        Note over P: ⏳ 等待 LLM 返回...

        alt ✅ LLM 返回 tool_calls
            loop 每个 tool_call
                P->>T: call_tool(name, args)
                Note over T: 🔍 检测线程：非主线程
                T-->>Q: QTimer.singleShot(0) 调度
                Note over Q: ✅ 主线程执行
                Q-->>T: 返回结果
                T-->>P: 工具执行结果
            end
            P->>P: 追加 ToolMessage 到 messages
        else 🎯 LLM 返回最终回复
            Note over P: ✅ 退出循环
        end
    end

    P->>P: 💾 保存交互到 SQLite
    W->>A: finished 信号

    rect rgb(227, 242, 253)
        Note over W: ⚙️ 工作线程结束
    end

    A->>D: updateConversation() 渲染回复
    D->>User: 显示 AI 回复
```

---

## 线程模型

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'fontSize': '14px'}}}%%
flowchart LR
    subgraph MT["🖥️ QGIS 主线程 (GUI Thread)"]
        direction TB
        MT_UI["🪟 DockWidget UI"]
        MT_AGENT["🧩 QGISAgent 信号处理"]
        MT_TOOLS["🗺️ QGIS API 操作"]
        MT_CANVAS["🖼️ Map Canvas 渲染"]
    end

    subgraph WT["⚙️ QThreadPool 工作线程"]
        direction TB
        WT_LLM["🤖 LLM API 调用<br/><small>可能耗时数秒~数十秒</small>"]
        WT_HISTORY["🗃️ SQLite 读写"]
        WT_LOOP["🔄 Agent 工具调用循环"]
    end

    WT_LOOP -->|"⏱️ QTimer.singleShot(0)"| MT_TOOLS
    WT_LOOP -->|"📡 thinking / tool_status 信号"| MT_UI
    WT_LLM --> WT_LOOP

    style MT fill:#e3f2fd,stroke:#1976d2,stroke-width:2px,color:#1565c0
    style WT fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100
    style MT_TOOLS fill:#66bb6a,stroke:#2e7d32,color:#fff
    style WT_LOOP fill:#ffa726,stroke:#e65100,color:#fff
    style WT_LLM fill:#ef5350,stroke:#c62828,color:#fff
```

**关键设计原则**：
- LLM API 调用在工作线程中执行，**不阻塞 QGIS 主线程 UI**
- 所有 QGIS API 操作通过 `QTimer.singleShot(0)` 调度回主线程执行，**保证线程安全**
- 工作线程用 `QEventLoop.processEvents()` 同步等待主线程执行结果
- 超时时间：60 秒

---

## 数据流向

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#e3f2fd', 'primaryBorderColor': '#1976d2', 'lineColor': '#5a6a7a'}}}%%
flowchart TD
    INPUT["👤 用户自然语言输入"]
    
    subgraph CONTEXT["📦 上下文组装 (agent_chat)"]
        direction LR
        MEMORY["📄 MEMORY.md<br/><small>长期记忆文件</small>"]
        HISTORY["🗃️ SQLite interactions 表<br/><small>对话历史 (最近20条)</small>"]
        SYSTEM["📋 AGENT_SYSTEM_PROMPT<br/><small>系统提示词</small>"]
    end

    subgraph LLM["🧠 LLM 推理"]
        direction LR
        BIND["🔗 llm.bind_tools<br/><small>TOOL_DEFINITIONS</small>"]
        INFER["⚡ llm.invoke<br/><small>messages</small>"]
    end

    subgraph TOOLS["🔩 工具执行 (主线程)"]
        T1["📊 get_qgis_info"]
        T2["📂 add_vector_layer"]
        T3["🗾 add_raster_layer"]
        T4["🔍 get_layer_features"]
        T5["🗑️ remove_layer"]
        T6["🔎 zoom_to_layer"]
        T7["⚙️ execute_processing"]
        T8["🐍 execute_pyqgis"]
        T9["🏷️ set_layer_labeling"]
        T10["📸 render_map"]
        T11["💾 save/load_project"]
        TM1["🧠 save_memory"]
        TM2["📖 load_memory"]
        TR1["📚 search_pyqgis_api"]
    end

    OUTPUT["🤖 AI 回复文本"]

    INPUT --> CONTEXT
    CONTEXT -->|"messages 列表"| LLM
    LLM -->|"tool_calls"| TOOLS
    TOOLS -->|"ToolMessage 结果"| LLM
    LLM -->|"最终回复"| OUTPUT

    style CONTEXT fill:#e3f2fd,stroke:#1976d2,stroke-width:2px,color:#1565c0
    style LLM fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100
    style TOOLS fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#2e7d32
    style INPUT fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#6a1b9a
    style OUTPUT fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#6a1b9a
    style INFER fill:#ff6b6b,stroke:#c62828,color:#fff
    style BIND fill:#ffa726,stroke:#e65100,color:#fff
```

---

## 15 个 QGIS 工具

| # | 工具名 | 功能 | 关键 QGIS API |
|---|--------|------|---------------|
| 1 | `save_memory` | 保存长期记忆（追加到 MEMORY.md） | 文件 I/O + 去重 |
| 2 | `load_memory` | 读取长期记忆文件全部内容 | 文件 I/O |
| 3 | `search_pyqgis_api` | 检索 PyQGIS/GDAL/Processing API 文档 | `rag.retriever` → SQLite FTS5 |
| 4 | `get_qgis_info` | 获取版本、项目路径、CRS、图层列表 | `QgsProject.instance()`, `Qgis.QGIS_VERSION` |
| 5 | `get_layer_features` | 获取矢量图层属性表 + 几何 WKT | `layer.getFeatures()`, `feature.geometry().asWkt()` |
| 6 | `add_vector_layer` | 添加矢量图层 | `QgsVectorLayer()`, `QgsProject.addMapLayer()` |
| 7 | `add_raster_layer` | 添加栅格图层 | `QgsRasterLayer()`, `QgsProject.addMapLayer()` |
| 8 | `remove_layer` | 移除图层 | `QgsProject.removeMapLayer()` |
| 9 | `zoom_to_layer` | 缩放到图层范围 | `iface.setActiveLayer()`, `iface.zoomToActiveLayer()` |
| 10 | `execute_processing` | 执行 Processing 算法 | `processing.run()` |
| 11 | `execute_pyqgis` | 执行任意 PyQGIS 代码 | `exec()` + stdout/stderr 重定向 |
| 12 | `set_layer_labeling` | 设置矢量图层标注（字体/颜色/缓冲/位置） | `QgsPalLayerSettings` |
| 13 | `save_project` | 保存项目文件 | `QgsProject.write()` |
| 14 | `load_project` | 加载项目文件 | `QgsProject.read()`, `iface.mapCanvas().refresh()` |
| 15 | `render_map` | 渲染地图为 PNG | `QgsMapRendererParallelJob` |

---

## 长期记忆机制

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#e3f2fd', 'primaryBorderColor': '#1976d2', 'lineColor': '#5a6a7a'}}}%%
flowchart TD
    START["🔄 每次对话开始"]
    READ["📖 读取 MEMORY.md"]
    INJECT["💉 注入到 System Prompt 末尾"]
    LLM_VISIBLE["🧠 LLM 可见所有历史记忆"]

    subgraph TOOL_USE["🛠️ 对话中 LLM 使用记忆工具"]
        direction LR
        SAVE["💾 save_memory<br/><small>追加新记忆 + 自动去重</small>"]
        LOAD["📂 load_memory<br/><small>读取全部记忆内容</small>"]
    end

    FILE["📄 MEMORY.md<br/><small>位于插件目录下</small>"]

    START --> READ
    READ --> INJECT
    INJECT --> LLM_VISIBLE
    LLM_VISIBLE --> TOOL_USE
    SAVE --> FILE
    LOAD --> FILE

    style START fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#6a1b9a
    style TOOL_USE fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100
    style FILE fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#2e7d32
    style LLM_VISIBLE fill:#ff6b6b,stroke:#c62828,color:#fff
    style SAVE fill:#66bb6a,stroke:#2e7d32,color:#fff
    style LOAD fill:#ffa726,stroke:#e65100,color:#fff
```

**记忆文件路径**：`{QGIS Profile}/python/plugins/qgis_agent/MEMORY.md`
**去重逻辑**：如果内容已存在于文件中，跳过保存
**长度限制**：注入时截断到 4000 字符，`load_memory` 工具返回上限 8000 字符

---

## RAG API 文档检索

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#e3f2fd', 'primaryBorderColor': '#1976d2', 'lineColor': '#5a6a7a'}}}%%
flowchart TD
    START["🔍 search_pyqgis_api 被调用"]
    QUERY["📝 用户查询关键词"]
    KW["🔤 关键词提取 + 中英文翻译"]
    FTS["🔎 SQLite FTS5 全文搜索"]
    SCORE["📊 相关性排序 (Top-K)"]
    RESULT["📋 返回 API 签名 + 参数说明"]

    START --> QUERY
    QUERY --> KW
    KW --> FTS
    FTS --> SCORE
    SCORE --> RESULT
    RESULT -->|"注入上下文"| PYQGIS["🐍 execute_pyqgis / ⚙️ execute_processing"]

    style START fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#6a1b9a
    style FTS fill:#1976d2,stroke:#0d47a1,color:#fff
    style RESULT fill:#66bb6a,stroke:#2e7d32,color:#fff
    style PYQGIS fill:#ffa726,stroke:#e65100,color:#fff
```

**模块**：`rag/doc_store.py`（SQLite FTS5 索引） + `rag/retriever.py`（检索逻辑） + `rag/doc_generator.py`（文档生成）
- 首次运行自动构建 PyQGIS API 索引（从 QGIS 运行时反射）
- `execute_pyqgis` 和 `execute_processing` 执行前自动检索相关 API 文档
- 支持中英文关键词搜索

---

## Cookbook 自我进化

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#e3f2fd', 'primaryBorderColor': '#1976d2', 'lineColor': '#5a6a7a'}}}%%
flowchart LR
    SUCCESS["✅ 工具执行成功"]
    ARCHIVE["📦 归档到案例库"]
    SIMILAR["🔍 相似案例检索"]
    INJECT["💉 注入 Agent 上下文"]

    SUCCESS --> ARCHIVE
    SIMILAR --> INJECT
    INJECT --> LLM["🧠 LLM 推理"]

    ARCHIVE -.->|"下次对话"| SIMILAR

    style SUCCESS fill:#66bb6a,stroke:#2e7d32,color:#fff
    style ARCHIVE fill:#1976d2,stroke:#0d47a1,color:#fff
    style LLM fill:#ff6b6b,stroke:#c62828,color:#fff
```

**模块**：`rag/cookbook.py`
- 每次工具成功执行后自动归档案例（任务描述 + 工具调用 + 结果摘要）
- 新任务开始时检索相似历史案例，帮助 LLM 更快找到正确方案
- 质量评分机制：高分案例优先注入

---

## 对话历史存储

- **存储引擎**：SQLite（`QGIS_Agent.db`）
- **核心表**：`interactions`（每条消息一行）、`conversations`（对话元信息）、`llm_config`（LLM 配置）
- **加载策略**：`agent_chat` 开始时从数据库读取最近 20 条（10 轮对话），拼入 `messages` 列表
- **格式**：`typeMessage=input` → `HumanMessage`，`typeMessage=return` → `AIMessage`

---

## 模块依赖图

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#e3f2fd', 'primaryBorderColor': '#1976d2', 'lineColor': '#5a6a7a'}}}%%
graph LR
    MAIN["🧩 qgis_agent.py"]
    DOCK["🪟 dockwidget"]
    CONV["💬 conversation.py"]
    DL["🗃️ dataloader.py"]
    UTIL["🔧 utils.py"]
    CFG["⚙️ config.py"]

    PROC["🧠 processor.py"]
    LLP["🤖 llm_providers.py"]
    TOOLS["🔩 qgis_tools.py"]
    WORKER["🔧 response_worker.py"]

    RAG["📚 rag/"]
    DOCSTORE["rag/doc_store.py"]
    RETR["rag/retriever.py"]
    COOK["rag/cookbook.py"]

    QAPI["🗺️ QGIS Core/GUI/Widgets"]
    SQL["📦 sqlite3"]

    MAIN --> DOCK
    MAIN --> CONV
    MAIN --> DL
    MAIN --> UTIL
    MAIN --> CFG

    CONV --> PROC
    PROC --> LLP
    PROC --> TOOLS
    PROC --> WORKER
    PROC --> UTIL
    PROC --> RAG

    RAG --> DOCSTORE
    RAG --> RETR
    RAG --> COOK
    DOCSTORE --> SQL
    RETR --> DOCSTORE

    TOOLS --> QAPI
    TOOLS --> RETR
    WORKER --> PROC
    DL --> SQL

    style MAIN fill:#1976d2,stroke:#0d47a1,color:#fff
    style PROC fill:#ff6b6b,stroke:#c62828,color:#fff
    style TOOLS fill:#ffa726,stroke:#e65100,color:#fff
    style RAG fill:#9c27b0,stroke:#6a1b9a,color:#fff
    style QAPI fill:#66bb6a,stroke:#2e7d32,color:#fff
    style SQL fill:#7b1fa2,stroke:#4a148c,color:#fff
```

---

## 关键配置

| 配置项 | 来源 | 默认值 |
|--------|------|--------|
| LLM Provider | `llm_config` 表 / 设置对话框 | DeepSeek |
| LLM Model | `llm_config` 表 | deepseek-chat |
| API Endpoint | `llm_config` 表 / 环境变量 | `https://api.deepseek.com/v1` |
| API Key | 环境变量 `DEEPSEEK_API_KEY` | — |
| 最大工具轮次 | `processor.py` | 10 |
| 历史消息上限 | `processor.py` | 20 条 |
| 工具执行超时 | `qgis_tools.py` | 60 秒 |
| 记忆注入长度上限 | `processor.py` | 4000 字符 |

---

## 常见问题速查

| 问题 | 原因 | 修复位置 |
|------|------|----------|
| Map Canvas 停止渲染 | 工具在工作线程调用 QGIS API | `qgis_tools.py:call_tool()` 已修复为 QTimer 主线程调度 |
| LLM 遗忘上下文 | `agent_chat` 未加载历史 | `processor.py` 已修复：加载 SQLite 历史 + MEMORY.md |
| `set_font_color` NameError | 局部导入未存为实例属性 | `qgis_agent.py` 已修复为 `self._set_font_color` |
| 文本未左对齐 | HTML div 缺少 `text-align:left` | `qgis_agent_dockwidget.py` 已修复 |
| `cbSkipConfirm` NoneType 错误 | dockwidget 创建在信号连接之后 | `qgis_agent.py:_init_plugin()` 已修复：dockwidget 创建前置 |
| GitHub 文件内容乱码 | API push 时双重 base64 编码 | 使用 `/git/blobs` API + sha 构建 tree |
| `requirements.txt` 缺少 `langchain_core` | 依赖声明不完整 | 代码中 `required_modules` 含 `langchain_core`，但 `requirements.txt` 未声明 |
