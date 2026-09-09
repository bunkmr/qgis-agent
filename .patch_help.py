# -*- coding: utf-8 -*-
"""一次性补丁：HELP.html 去 mermaid + 状态标注 + 工具表补全。用完即删。"""
import io
import os
import sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HELP.html")
src = io.open(PATH, encoding="utf-8").read()

REPLACEMENTS = []

# 1) .mermaid 样式块 -> .arch-ascii + .status-todo
REPLACEMENTS.append((
    """.mermaid {
    background-color: #f9f9f9;
    padding: 20px;
    border-radius: 10px;
    margin: 20px 0;
    text-align: center;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
}""",
    """.arch-ascii {
    background-color: #2d2d2d;
    color: #f8f8f2;
    padding: 20px;
    border-radius: 10px;
    margin: 20px 0;
    overflow-x: auto;
    font-family: "Consolas", "Monaco", monospace;
    font-size: 0.9em;
    line-height: 1.5;
    box-shadow: 0 4px 15px rgba(0,0,0,0.2);
}
.status-todo {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    background: #c0392b;
    color: #fff;
    font-size: 0.8em;
    font-weight: bold;
    margin-left: 6px;
}""",
))

# 2) 系统架构总图
REPLACEMENTS.append((
    """<div class="mermaid">
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
        E["🧠 Processor<br/><small>Agent 循环 + RAG + Cookbook</small>"]
    end

    subgraph TOOLS["🔩 QGIS 工具层 — 主线程调度"]
        direction LR
        F["📞 call_tool()<br/><small>线程桥</small>"]
        G["🧰 15+ QGIS 工具"]
        H["🗺️ QGIS API<br/><small>QgsProject / iface / Processing</small>"]
    end

    subgraph RAG["📚 RAG 引擎 (本地)"]
        direction LR
        J["📖 DocStore<br/><small>SQLite FTS5</small>"]
        K["🔍 Retriever<br/><small>关键词搜索</small>"]
        L["🧰 ToolDocs<br/><small>679 Processing 工具</small>"]
    end

    subgraph EXT["☁️ 外部服务"]
        I["🤖 LLM API<br/><small>DeepSeek / OpenAI 兼容</small>"]
    end

    A -->|创建/管理| B
    A -->|创建/管理| C
    C -->|async_response| D
    D -->|在线程中运行| E
    E -->|API 调用| I
    E -->|检索文档| K
    K -->|查询| J
    E -->|检索工具| L
    E -->|跨线程调用工具| F
    F -->|QTimer 调度到主线程| G
    G -->|操作| H
</div>""",
    """<pre class="arch-ascii">🖥️ QGIS 主线程（GUI）
┌──────────────────────────────────────────────────────────────┐
│  🧩 QGISAgent（主控制器）  ──▶  🪟 DockWidget（UI 面板）        │
│                             └─▶  💬 Conversation（会话管理）   │
└───────────────────────────────┬──────────────────────────────┘
                                │ async_response
                                ▼
⚙️ 工作线程（QThreadPool）
┌──────────────────────────────────────────────────────────────┐
│  🔧 ToolAgentWorker（异步执行）──▶  🧠 Processor（Agent 循环） │
└───────┬───────────────┬──────────────────────┬───────────────┘
        │               │                      │
        │ 检索文档      │ 检索工具             │ API 调用
        ▼               ▼                      ▼
📚 RAG 引擎（本地）                    ☁️ 外部服务
┌──────────────────────────────┐      ┌────────────────────────┐
│ 📖 DocStore（SQLite FTS5）   │      │ 🤖 LLM API             │
│ 🔍 Retriever（关键词搜索）    │      │    DeepSeek / OpenAI   │
│ 🧰 ToolDocs（679 个算法文档） │      │    兼容端点             │
└──────────────────────────────┘      └────────────────────────┘
        │ 跨线程调用工具
        ▼
🔩 QGIS 工具层（主线程调度）
┌──────────────────────────────────────────────────────────────┐
│  📞 call_tool()（线程桥）──QTimer──▶  🧰 19 个 QGIS 工具       │
│                                     └▶ 🗺️ QGIS API           │
│                                        QgsProject / iface /   │
│                                        Processing             │
└──────────────────────────────────────────────────────────────┘</pre>""",
))

# 3) RAG 检索流程
REPLACEMENTS.append((
    """<div class="mermaid">
graph LR
    A[用户请求] --> B[Query Tuning]
    B --> C[RAG 检索]
    C --> D[PyQGIS API]
    C --> E[Processing 工具]
    D --> F[LLM 生成代码]
    E --> F
    F --> G[执行分析]
    G --> H[返回结果]
</div>""",
    """<pre class="arch-ascii">用户请求 ──▶ Query Tuning ──▶ RAG 检索 ──┬──▶ PyQGIS API 文档 ──┐
                                        └──▶ Processing 工具文档 ┘
                                                     │
                                                     ▼
                                              LLM 生成代码
                                                     │
                                   执行分析 ◀────────┘
                                        │
                                        ▼
                                     返回结果</pre>""",
))

# 4) 工作流固化
REPLACEMENTS.append((
    """<div class="mermaid">
graph TB
    A[第一次对话] --> B[执行工具链]
    B --> C[录制工作流]
    C --> D[保存为模板]
    D --> E[第二次对话]
    E --> F[加载工作流]
    F --> G[参数替换]
    G --> H[直接执行]
    H --> I[返回结果]
</div>""",
    """<pre class="arch-ascii">第一次对话 ─▶ 执行工具链 ─▶ 录制工作流 ─▶ 保存为模板
                                              │
第二次对话 ◀─────────────────────────────────┘
     │
     ▼
加载工作流 ─▶ 参数替换 ─▶ 直接执行 ─▶ 返回结果</pre>""",
))

# 5) 工具文档检索
REPLACEMENTS.append((
    """<div class="mermaid">
graph LR
    A[用户请求] --> B{工具检索}
    B --> C[native:buffer]
    B --> D[native:clip]
    B --> E[native:intersection]
    B --> F[native:dissolve]
    B --> G[... 679 个工具]
    C --> H[参数说明]
    D --> H
    E --> H
    F --> H
    G --> H
    H --> I[代码示例]
    I --> J[LLM 生成代码]
    J --> K[执行分析]
</div>""",
    """<pre class="arch-ascii">用户请求 ──▶ 工具检索 ──┬──▶ native:buffer
                      ├──▶ native:clip
                      ├──▶ native:intersection
                      ├──▶ native:dissolve
                      └──▶ … 共 679 个算法文档
                                   │
                                   ▼
                              参数说明 ──▶ 代码示例 ──▶ LLM 生成代码 ──▶ 执行分析</pre>""",
))

# 6) 示例1 缓冲区工作流
REPLACEMENTS.append((
    """<div class="mermaid">
graph LR
    A[加载道路图层] --> B[执行缓冲区分析]
    B --> C[保存结果]
    C --> D[加载结果图层]
</div>""",
    """<pre class="arch-ascii">加载道路图层 ──▶ 执行缓冲区分析 ──▶ 保存结果 ──▶ 加载结果图层</pre>""",
))

# 7) 示例2 空间叠加
REPLACEMENTS.append((
    """<div class="mermaid">
graph LR
    A[加载土地利用图层] --> B[加载行政区划图层]
    B --> C[执行相交分析]
    C --> D[统计各区域面积]
    D --> E[导出结果]
</div>""",
    """<pre class="arch-ascii">加载土地利用图层 ──▶ 加载行政区划图层 ──▶ 执行相交分析 ──▶ 统计各区域面积 ──▶ 导出结果</pre>""",
))

# 8) 核心亮点：标注未接入功能
REPLACEMENTS.append((
    """<li><strong>📚 RAG API 文档检索</strong> - 380+ PyQGIS API 文档，自动查询准确参数</li>
<li><strong>🧰 679 个 Processing 工具</strong> - 完整的工具文档和代码示例</li>
<li><strong>🐛 SmartDebugger</strong> - 智能调试系统，自动分析错误并提供修复建议</li>
<li><strong>🔄 工作流固化</strong> - 将对话中的工具链保存为可重用工作流</li>
<li><strong>❓ 主动提问</strong> - 识别模糊请求，主动向用户澄清</li>
<li><strong>📊 Task Graph</strong> - 任务流程图可视化</li>
<li><strong>🎯 Query Tuning</strong> - 用户查询优化</li>""",
    """<li><strong>📚 RAG API 文档检索</strong> - 检索本地 PyQGIS API 文档，自动查询准确参数</li>
<li><strong>🧰 679 个 Processing 算法/工具文档</strong> - tool_docs/ 下每个算法一份 TOML 参考</li>
<li><strong>🐛 SmartDebugger</strong> - 工具失败自动诊断并把诊断结论回灌 LLM 改写重试</li>
<li><strong>🛡️ 安全护栏</strong> - PyQGIS 代码 AST 静态扫描、危险操作确认、不可信数据净化</li>
<li><strong>🎯 Query Tuning</strong> - 用户查询优化（已接入）</li>
<li><strong>🔄 工作流固化</strong> - 将对话中的工具链保存为可重用工作流 <span class="status-todo">规划中 · 当前不可用</span></li>
<li><strong>❓ 主动提问</strong> - 识别模糊请求，主动向用户澄清 <span class="status-todo">规划中 · 当前不可用</span></li>
<li><strong>📊 Task Graph</strong> - 任务流程图可视化 <span class="status-todo">规划中 · 当前不可用</span></li>""",
))

# 9) 顶部状态说明
REPLACEMENTS.append((
    """<h1>🗺️ QGIS Agent</h1>
<p class="version">版本 2.1.3 | 将大语言模型嵌入QGIS的智能助手</p>
<p style="text-align: center; color: #666;">用自然语言操控 QGIS，无需编写代码</p>""",
    """<h1>🗺️ QGIS Agent</h1>
<p class="version">版本 2.1.3（实际版本以 QGIS 插件管理器显示为准）| 将大语言模型嵌入QGIS的智能助手</p>
<p style="text-align: center; color: #666;">用自然语言操控 QGIS，无需编写代码</p>

<div class="warning">
<strong>⚠️ 功能状态说明（请先阅读）</strong>
<p>本文档是<strong>产品路线图 + 使用手册</strong>，其中部分能力仍处于规划阶段、尚未接入对话链路。带
<span class="status-todo">规划中 · 当前不可用</span> 标记的功能<strong>在当前版本中无法使用</strong>，包括：</p>
<ul>
<li><strong>技能系统</strong>（<code>skills/</code>）—— 代码中存在实现，但生产链路零引用</li>
<li><strong>工作流固化 / 录制回放</strong>（<code>workflow_recorder.py</code>、<code>workflow_executor.py</code>）—— 未接线</li>
<li><strong>主动提问</strong>（<code>clarification_manager.py</code>）—— 未接线</li>
<li><strong>任务图 Task Graph</strong>（<code>task_graph.py</code>）—— 未接线</li>
</ul>
<p>其余未标注的能力均为当前版本实际可用功能。宁可少宣传，也不希望您装了发现用不了。</p>
</div>""",
))

# 10) 内置工具表：补齐 19 个
REPLACEMENTS.append((
    """<tr><td><code>save_memory</code></td><td>保存长期记忆</td><td>🧠 记忆</td><td>记住用户偏好</td></tr>
<tr><td><code>load_memory</code></td><td>加载长期记忆</td><td>🧠 记忆</td><td>读取之前保存的信息</td></tr>
</table>""",
    """<tr><td><code>save_memory</code></td><td>保存长期记忆</td><td>🧠 记忆</td><td>记住用户偏好</td></tr>
<tr><td><code>load_memory</code></td><td>加载长期记忆</td><td>🧠 记忆</td><td>读取之前保存的信息</td></tr>
<tr><td><code>get_algorithm_parameters</code></td><td>查询 Processing 算法参数</td><td>📚 RAG</td><td>查 native:buffer 需要哪些参数</td></tr>
<tr><td><code>get_layer_profile</code></td><td>生成图层数据概览</td><td>📊 查询</td><td>看看这个图层有哪些字段</td></tr>
<tr><td><code>set_layer_renderer</code></td><td>设置图层渲染样式</td><td>🎨 渲染</td><td>按高度字段分级设色</td></tr>
<tr><td><code>reproject_layer</code></td><td>图层投影转换</td><td>⚙️ 分析</td><td>把这个图层转到 EPSG:3857</td></tr>
</table>

<p style="color:#888; font-size:0.9em;">共 <strong>19</strong> 个内置工具，清单与代码中的
<code>qgis_tools.TOOL_DEFINITIONS</code> 保持一致。</p>""",
))

# 11) 工作流章节标题标注
REPLACEMENTS.append((
    """<h3>🔄 工作流固化</h3>
<p>将对话中的工具调用序列保存为可重用工作流：</p>""",
    """<h3>🔄 工作流固化 <span class="status-todo">规划中 · 当前不可用</span></h3>
<p><strong>当前版本无法使用。</strong>设计目标：将对话中的工具调用序列保存为可重用工作流
（<code>workflow_recorder.py</code> / <code>workflow_executor.py</code> 代码已存在，但尚未接入对话链路）：</p>""",
))

REPLACEMENTS.append((
    """<h3>❓ 主动提问</h3>
<p>识别模糊或不完整的请求，主动向用户澄清：</p>""",
    """<h3>❓ 主动提问 <span class="status-todo">规划中 · 当前不可用</span></h3>
<p><strong>当前版本无法使用。</strong>设计目标：识别模糊或不完整的请求，主动向用户澄清
（<code>clarification_manager.py</code> 尚未接入对话链路）：</p>""",
))

REPLACEMENTS.append((
    """<h2>📊 工作流示例</h2>""",
    """<h2>📊 工作流示例 <span class="status-todo">以下为「工作流固化」规划示意</span></h2>""",
))

# 12) 更新日志：补 v2.2.0
REPLACEMENTS.append((
    """<h2>📄 更新日志</h2>

<h3>v2.1.3 (2026-09-02)</h3>""",
    """<h2>📄 更新日志</h2>

<h3>v2.2.0（开发中）</h3>
<ul>
<li>🐛 SmartDebugger 接线：工具失败自动诊断并回灌 LLM 改写重试，不再一错就停</li>
<li>🛡️ 安全：PyQGIS 代码 AST 静态扫描（模块白名单 + 危险调用黑名单）</li>
<li>🛡️ 安全：危险操作确认扩展到 <code>remove_layer</code> / <code>load_project</code> / <code>save_project</code>，覆盖文件写入时确认</li>
<li>🛡️ 安全：不可信数据净化 —— 图层名、属性值不再原样回喂 LLM，并对结果截断</li>
<li>💬 体验：错误分级提示（中文可操作文案），不再只抛原始 traceback</li>
<li>🐛 修复「点停止后该对话永久报废」</li>
</ul>

<h3>v2.1.3 (2026-09-02)</h3>""",
))

missing = []
for old, new in REPLACEMENTS:
    if old not in src:
        missing.append(old.split("\n")[0][:60])
    else:
        src = src.replace(old, new, 1)

if missing:
    print("未匹配到（需人工检查）：")
    for m in missing:
        print("  -", m)
    sys.exit(1)

io.open(PATH, "w", encoding="utf-8").write(src)
print("HELP.html 补丁完成，共替换 %d 处" % len(REPLACEMENTS))
print("残留 mermaid 引用:", src.count("mermaid"))
