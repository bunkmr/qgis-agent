# QGIS Agent 双 RAG 系统说明

## 📊 当前状态

### 系统概览

QGIS Agent 现在拥有**两个互补的 RAG 系统**：

| 系统 | 目录 | 检索内容 | 工具数量 |
|------|------|----------|----------|
| **原有 RAG** | `rag/` | PyQGIS API 文档 | 380+ |
| **工具文档 RAG** | `tool_docs/` | Processing 算法工具 | 679 |

### 是否冲突？

**不冲突！它们是互补的**：

```
用户请求："对道路图层做100米缓冲区分析"

原有 RAG 检索：
├── QgsVectorLayer 的使用方法
├── QgsGeometry 的缓冲区方法
├── processing.run() 的调用方式
└── 方法签名和参数说明

工具文档 RAG 检索：
├── native:buffer 算法的参数说明
├── INPUT, DISTANCE, SEGMENTS 等参数
├── 完整的代码示例
└── 算法的详细描述
```

---

## 🔍 两个系统的详细对比

### 原有 RAG 系统 (`rag/`)

**目的**：帮助 LLM 理解 PyQGIS API 的使用方法

**检索内容**：
- QgsVectorLayer (17 methods)
- QgsGeometry (16 methods)
- QgsProject (11 methods)
- QgsCoordinateReferenceSystem (9 methods)
- QgsFeature (9 methods)
- QgsMapCanvas (7 methods)
- QgsApplication (2 methods)
- Processing 算法 (100+)
- 手动补充文档 (11)
- **总计**: 380+ 条文档

**文件结构**：
```
rag/
├── doc_store.py          # SQLite FTS5 文档存储
├── retriever.py          # API 文档检索器
├── cookbook.py            # Cookbook 自我进化
├── doc_generator.py      # API 文档生成器
└── official_doc_scraper.py  # 官方 API 文档
```

**使用场景**：
```python
# 当 LLM 需要编写 PyQGIS 代码时
from rag import DocStore, APIDocRetriever

retriever = APIDocRetriever(doc_store)
docs = retriever.search_for_tool_call("execute_pyqgis", {"code": "..."})
```

---

### 工具文档 RAG 系统 (`tool_docs/`)

**目的**：帮助 LLM 理解 Processing 算法的使用方法

**检索内容**：
- 679 个 QGIS Processing 算法
- 每个算法包含：
  - tool_id (如 `native:buffer`)
  - tool_name (如 `Buffer`)
  - brief_description (简要描述)
  - parameters (参数说明)
  - code_example (代码示例)

**文件结构**：
```
tool_docs/
├── native_buffer.toml
├── native_clip.toml
├── native_extractbyattribute.toml
├── ... (679 个 TOML 文件)

tool_docs_index.json    # JSON 索引文件 (快速检索)
```

**使用场景**：
```python
# 当 LLM 需要调用 Processing 算法时
from tool_doc_manager import ToolDocManager

manager = ToolDocManager()
results = manager.search_tools("缓冲区分析", top_k=3)
```

---

## 🎯 如何协同工作

### 场景 1：用户请求缓冲区分析

```python
# 用户输入
user_query = "对道路图层做100米缓冲区分析"

# 1. Query Tuning 优化查询
tuned_query = query_tuner.tune_query(user_query, data_overview)
# 输出: "Perform buffer analysis on road layer with 100m distance"

# 2. 工具文档 RAG 检索
tool_results = tool_doc_manager.search_tools("buffer", top_k=3)
# 返回: native:buffer 的完整文档

# 3. 原有 RAG 检索
api_docs = retriever.search_for_tool_call("execute_pyqgis", {"code": "processing.run('native:buffer', ...)"})
# 返回: QgsVectorLayer, processing.run() 等 API 文档

# 4. 注入 LLM 上下文
system_prompt += f"\n\nTool Documentation:\n{tool_context}"
system_prompt += f"\n\nAPI Documentation:\n{api_context}"

# 5. LLM 生成代码
code = llm.invoke(messages)
```

### 场景 2：用户请求复杂的 PyQGIS 操作

```python
# 用户输入
user_query = "根据属性字段对图层进行分级渲染"

# 1. 工具文档 RAG 检索
tool_results = tool_doc_manager.search_tools("graduated renderer", top_k=3)
# 返回: 相关工具文档

# 2. 原有 RAG 检索
api_docs = retriever.search_for_tool_call("execute_pyqgis", {"code": "QgsGraduatedSymbolRenderer..."})
# 返回: QgsGraduatedSymbolRenderer 的详细 API 文档

# 3. LLM 生成 PyQGIS 代码
code = llm.invoke(messages)
```

---

## 📁 文件结构

```
qgis_agent/
├── rag/                          # 原有 RAG 系统
│   ├── doc_store.py              # SQLite FTS5 存储
│   ├── retriever.py              # API 文档检索
│   ├── cookbook.py                # Cookbook 自我进化
│   └── ...
│
├── tool_docs/                    # 工具文档 RAG 系统
│   ├── native_buffer.toml        # 679 个 TOML 文件
│   ├── native_clip.toml
│   └── ...
│
├── tool_docs_index.json          # JSON 索引 (快速检索)
├── tool_doc_manager.py           # 工具文档管理器
├── import_tool_docs.py           # 导入脚本
│
└── processor.py                  # 主处理器 (集成两个 RAG)
```

---

## 🚀 性能对比

| 指标 | 原有 RAG | 工具文档 RAG |
|------|----------|--------------|
| 文档数量 | 380+ | 679 |
| 检索方式 | SQLite FTS5 | JSON 索引 |
| 检索速度 | < 50ms | < 10ms |
| 内存占用 | 中等 | 低 |
| 维护成本 | 高 (需要构建索引) | 低 (直接加载) |

---

## 🔧 集成到 processor.py

```python
from .rag import DocStore, APIDocRetriever, Cookbook
from .tool_doc_manager import ToolDocManager

class Processor(QObject):
    def __init__(self, ...):
        # 原有 RAG 组件
        self.doc_store = DocStore()
        self.retriever = APIDocRetriever(self.doc_store)
        self.cookbook = Cookbook(self.doc_store)

        # 新增工具文档组件
        self.tool_doc_manager = ToolDocManager()

    def agent_chat(self, user_input, ...):
        # 1. 工具文档 RAG 检索
        tool_results = self.tool_doc_manager.search_tools(user_input, top_k=3)

        # 2. 原有 RAG 检索 (在执行代码时)
        if tool_name in ("execute_pyqgis", "execute_processing"):
            api_docs = self.retriever.search_for_tool_call(tool_name, tool_args)
```

---

## 📝 总结

| 问题 | 答案 |
|------|------|
| 现在集成了615个工具了吗？ | ✅ 是的，集成了679个工具文档 |
| 原来的RAG方式检索API还有吗？ | ✅ 是的，仍然存在 |
| 它们冲突吗？ | ❌ 不冲突，它们是互补的 |

**两个系统的分工**：
- **原有 RAG**: 检索 PyQGIS API 文档 (类、方法、参数)
- **工具文档 RAG**: 检索 Processing 算法文档 (工具、参数、示例)

**协同工作**：当 LLM 需要编写代码时，两个系统同时提供上下文，帮助生成更准确的代码。
