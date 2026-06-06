# QGIS Agent

> 🗺️ 将大语言模型 (LLM) 嵌入 QGIS 的智能助手插件 —— 用自然语言操控 QGIS 完成地理空间任务。

[![QGIS](https://img.shields.io/badge/QGIS-3.0+-589632?logo=qgis&style=flat-square)](https://qgis.org/)
[![Python](https://img.shields.io/badge/Python-3.7+-3776AB?logo=python&style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

---

## 架构概览

```mermaid
graph TB
    subgraph UI["🖥️ QGIS 主线程 — GUI"]
        direction LR
        A["🧩 QGISAgent<br/><small>主控制器</small>"]
        B["🪟 DockWidget<br/><small>UI 面板</small>"]
        C["💬 Conversation<br/><small>会话管理</small>"]
    end

    subgraph WORKER["⚙️ 工作线程 — QThreadPool"]
        direction LR
        D["🔧 ToolAgentWorker<br/><small>异步执行</small>"]
        E["🧠 Processor<br/><small>Agent 循环</small>"]
    end

    subgraph TOOLS["🔩 QGIS 工具层 — 主线程调度"]
        direction LR
        F["📞 call_tool()<br/><small>线程桥</small>"]
        G["🧰 11 个 QGIS 工具"]
        H["🗺️ QGIS API<br/><small>QgsProject / iface / Processing</small>"]
    end

    subgraph EXT["☁️ 外部服务"]
        I["🤖 LLM API<br/><small>DeepSeek / OpenAI 兼容</small>"]
    end

    A -->|创建/管理| B
    A -->|创建/管理| C
    C -->|async_response| D
    D -->|在线程中运行| E
    E -->|API 调用| I
    E -->|跨线程调用工具| F
    F -->|QTimer 调度到主线程| G
    G -->|操作| H
    E -->|thinking 信号| B
    E -->|finished 信号| A
```

## 调用流程

```mermaid
sequenceDiagram
    autonumber
    actor User as 👤 用户
    participant UI as 🪟 DockWidget
    participant Agent as 🧩 QGISAgent
    participant Conv as 💬 Conversation
    participant Proc as 🧠 Processor
    participant Worker as 🔧 Worker
    participant Tool as 📞 call_tool
    participant QGIS as 🗺️ QGIS API

    rect rgb(227, 242, 253)
        Note over User,Agent: 📥 用户发起请求
        User->>UI: 输入消息 + 回车
        UI->>Agent: _on_new_message_send()
        Agent->>UI: 显示用户消息 HTML
        Agent->>Conv: update_user_prompt(message)
        Conv->>Proc: async_response(message)
        Proc->>Worker: 创建 Worker 投入线程池
    end

    rect rgb(255, 243, 224)
        Note over Worker,Proc: ⚙️ 工作线程执行
        Worker->>Proc: agent_chat(user_input, callbacks)
        Proc->>Proc: 📄 加载 MEMORY.md 长期记忆
        Proc->>Proc: 🗃️ 加载 SQLite 对话历史
        Proc->>Proc: 📦 组装 messages = [System + History + User]

        loop 🔄 最多 10 轮工具调用
            Proc->>Proc: llm.bind_tools().invoke(messages)
            Note over Proc: ⏳ 等待 LLM 返回...

            alt ✅ LLM 返回 tool_calls
                loop 每个 tool_call
                    Proc->>Tool: call_tool(name, args)
                    Note over Tool: 🔍 检测线程：非主线程
                    Tool-->>QGIS: QTimer.singleShot(0) 调度
                    Note over QGIS: ✅ 主线程执行
                    QGIS-->>Tool: 返回结果
                    Tool-->>Proc: 工具执行结果
                end
                Proc->>Proc: 追加 ToolMessage 到 messages
            else 🎯 LLM 返回最终回复
                Note over Proc: ✅ 退出循环
            end
        end

        Proc->>Proc: 💾 保存交互到 SQLite
    end

    rect rgb(227, 242, 253)
        Note over Worker,User: 📤 返回结果
        Worker->>Agent: finished 信号
        Agent->>UI: updateConversation() 渲染回复
        UI->>User: 显示 AI 回复
    end
```

## 线程模型

```mermaid
flowchart LR
    subgraph MT["🖥️ QGIS 主线程 (GUI Thread)"]
        direction TB
        MT_UI["🪟 DockWidget UI<br/><small>对话渲染 / 输入处理</small>"]
        MT_AGENT["🧩 QGISAgent<br/><small>信号处理 / 状态管理</small>"]
        MT_TOOLS["🗺️ QGIS API 操作<br/><small>图层 / 渲染 / Processing</small>"]
        MT_CANVAS["🖼️ Map Canvas<br/><small>地图渲染</small>"]
    end

    subgraph WT["⚙️ QThreadPool 工作线程"]
        direction TB
        WT_LLM["🤖 LLM API 调用<br/><small>可能耗时数秒~数十秒</small>"]
        WT_HISTORY["🗃️ SQLite 读写<br/><small>对话历史持久化</small>"]
        WT_LOOP["🔄 Agent 工具调用循环<br/><small>最多 10 轮</small>"]
    end

    WT_LOOP -->|"⏱️ QTimer.singleShot(0)"| MT_TOOLS
    WT_LOOP -->|"📡 thinking / tool_status 信号"| MT_UI
    WT_LLM --> WT_LOOP
```

> **核心设计原则**：
> - LLM API 调用在工作线程中执行，**不阻塞 QGIS 主线程 UI**
> - 所有 QGIS API 操作通过 `QTimer.singleShot(0)` 调度回主线程执行，**保证线程安全**
> - 工作线程通过信号/槽同步等待主线程执行结果，超时 60 秒

## 数据流

```mermaid
flowchart TD
    INPUT["👤 用户自然语言输入"]

    subgraph CONTEXT["📦 上下文组装"]
        direction LR
        MEMORY["📄 MEMORY.md<br/><small>长期记忆文件</small>"]
        HISTORY["🗃️ SQLite 对话历史<br/><small>最近 20 条</small>"]
        SYSTEM["📋 AGENT_SYSTEM_PROMPT<br/><small>系统提示词</small>"]
    end

    subgraph LLM["🧠 LLM 推理"]
        direction LR
        BIND["🔗 llm.bind_tools<br/><small>绑定 11 个工具</small>"]
        INFER["⚡ llm.invoke<br/><small>推理 + 工具选择</small>"]
    end

    subgraph TOOLS["🔩 工具执行 (主线程调度)"]
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
    end

    OUTPUT["🤖 AI 回复文本"]

    INPUT --> CONTEXT
    CONTEXT -->|"messages 列表"| LLM
    LLM -->|"tool_calls"| TOOLS
    TOOLS -->|"ToolMessage 结果"| LLM
    LLM -->|"最终回复"| OUTPUT
```

## 功能特性

- 💬 **自然语言交互** — 在 QGIS 内直接对话，无需编写代码
- 🧰 **11 个内置工具** — 图层管理、数据处理、地图渲染、项目保存等
- 🧠 **多 LLM 支持** — DeepSeek、OpenAI、智谱 GLM、Gemini、小米 MiMo 等
- 🧵 **线程安全** — LLM 调用不阻塞 UI，QGIS API 操作安全调度
- 💾 **对话持久化** — SQLite 存储完整对话历史，支持检索与恢复
- 📝 **长期记忆** — 跨对话记忆，AI 记住你的偏好和工作习惯
- 🎨 **标注支持** — 一键设置图层标注样式

## 支持的 LLM

> 任何兼容 OpenAI API 格式的服务均可接入。

## 安装

### 方式一：QGIS 插件管理器安装 (推荐)

1. 下载 `qgis_agent.zip`
2. QGIS → 插件 → 管理和安装插件 → 从 ZIP 安装
3. 选择 `qgis_agent.zip` 安装

### 方式二：手动安装

```bash
# 复制到 QGIS 插件目录
cp -r qgis_agent/ ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/

# 安装依赖
pip install -r requirements.txt
```

## 配置

1. 在 QGIS 中打开插件面板
2. 切换到「模型配置」标签页
3. 添加 LLM 配置（名称、API 端点、API Key）
4. 或通过环境变量配置：
   - `DEEPSEEK_API_KEY` — DeepSeek API Key
   - `OPENAI_API_KEY` — OpenAI API Key
   - 其他提供商同理

## 使用方法

1. 点击工具栏 **QGIS Agent** 图标或菜单 **插件 → QGIS Agent**
2. 在底部输入框输入自然语言指令，例如：
   - `添加图层 D:/data/roads.shp`
   - `查看当前项目有哪些图层`
   - `对建筑图层按高度字段分级设色`
   - `将地图渲染导出为 PNG`
3. AI 会自动选择并调用合适的 QGIS 工具完成任务

## 内置工具

| 工具 | 功能 |
|------|------|
| `get_qgis_info` | 获取 QGIS 项目信息、图层列表 |
| `add_vector_layer` | 添加矢量图层 |
| `add_raster_layer` | 添加栅格图层 |
| `get_layer_features` | 查询图层要素属性 |
| `remove_layer` | 移除图层 |
| `zoom_to_layer` | 缩放到图层范围 |
| `execute_processing` | 执行 Processing 算法 |
| `execute_pyqgis` | 执行任意 PyQGIS 代码 |
| `set_layer_labeling` | 设置图层标注 |
| `render_map` | 渲染地图截图 |
| `save_project` / `load_project` | 保存/加载项目 |

## 项目结构

```
qgis_agent/
├── __init__.py                  # 插件入口
├── qgis_agent.py                # 主控制器
├── processor.py                 # LLM Agent 核心
├── qgis_tools.py                # QGIS 工具集 + 线程桥
├── conversation.py              # 对话会话管理
├── response_worker.py           # 多线程 Worker
├── dataloader.py                # SQLite 数据层
├── llm_providers.py             # LLM 提供商工厂
├── utils.py                     # 工具函数
├── config.py                    # 全局配置
├── package_manager.py           # 依赖管理
├── resources/prompt.json        # 提示词模板
├── tests/                       # 单元测试
├── icon.png                     # 插件图标
├── metadata.txt                 # QGIS 插件元数据
├── requirements.txt             # Python 依赖
├── README.md                    # 项目说明
├── CHANGELOG.md                 # 更新日志
└── LICENSE                      # 开源许可
```

## 开发

```bash
# 运行测试
python -m pytest tests/ -v

# 生成图标
python generate_icon.py
```

## 许可

[MIT](LICENSE)
