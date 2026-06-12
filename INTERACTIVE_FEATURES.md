# QGIS Agent 交互式功能说明

## 🎯 新增功能概览

| 功能 | 文件 | 说明 |
|------|------|------|
| **工作流录制** | `workflow_recorder.py` | 记录对话中的工具调用序列，保存为可重用工作流 |
| **工作流执行** | `workflow_executor.py` | 在新工程中直接执行保存的工作流 |
| **主动提问** | `clarification_manager.py` | 识别模糊请求，主动向用户澄清 |

---

## 🔄 功能1：工作流固化

### 问题场景

```
用户：对道路图层做100米缓冲区分析
Agent：执行缓冲区分析...
用户：保存结果到 D:/output/buffer.shp
Agent：保存成功！

# 下次用户又想做类似的分析：
用户：对河流图层做200米缓冲区分析
Agent：需要重新理解任务，重新执行...
```

### 解决方案

将对话中的工具调用序列保存为**工作流模板**，下次可以直接调用：

```python
from workflow_recorder import WorkflowRecorder

# 录制工作流
recorder = WorkflowRecorder()
workflow = recorder.record_from_conversation(
    tool_calls=[
        {"tool": "native:buffer", "args": {"INPUT": "${input_layer}", "DISTANCE": "${distance}"}},
        {"tool": "native:savefeatures", "args": {"INPUT": "OUTPUT", "OUTPUT": "${output_path}"}}
    ],
    workflow_name="缓冲区分析工作流",
    description="对图层执行缓冲区分析并保存结果"
)

# 下次直接执行
from workflow_executor import WorkflowExecutor

executor = WorkflowExecutor()
results = executor.execute_workflow(
    template=workflow,
    parameters={
        "input_layer": "D:/data/rivers.shp",
        "distance": 200,
        "output_path": "D:/output/rivers_buffer.shp"
    }
)
```

### 使用示例

#### 1. 录制工作流

```python
# 在对话中录制
user_input = "对道路图层做100米缓冲区分析，然后保存到 D:/output/roads_buffer.shp"

# Agent 执行后，自动录制
workflow = recorder.record_from_conversation(
    tool_calls=conversation.tool_calls,
    workflow_name="道路缓冲区分析"
)
```

#### 2. 搜索工作流

```python
# 搜索相关工作流
workflows = recorder.search_workflows("缓冲区", top_k=3)
# 返回: ["道路缓冲区分析", "河流缓冲区分析", ...]
```

#### 3. 执行工作流

```python
# 在新工程中执行
executor = WorkflowExecutor()
results = executor.execute_workflow(
    template=workflow,
    parameters={"input_layer": "D:/data/new_roads.shp"}
)
```

#### 4. 生成工作流代码

```python
# 生成可独立运行的 Python 代码
code = executor.generate_workflow_code(workflow)
# 保存为 .py 文件，可以在任何 QGIS 环境中运行
```

---

## ❓ 功能2：主动提问

### 问题场景

```
用户：分析一下这个数据
Agent：好的，我来分析...（但不知道要分析什么）
```

### 解决方案

Agent 识别模糊请求，主动向用户澄清：

```python
from clarification_manager import ClarificationManager

manager = ClarificationManager(llm)

# 分析用户请求
analysis = manager.analyze_request("分析一下这个数据")

if not analysis["is_complete"]:
    # 生成澄清问题
    response = manager.get_clarification_response("分析一下这个数据")
    # 输出: "请具体说明要分析什么内容，例如：统计面积、长度、数量等"
```

### 使用示例

#### 1. 识别模糊请求

```python
# 用户输入
user_input = "分析一下"

# 分析请求
analysis = manager.analyze_request(user_input)
# analysis = {
#   "is_complete": False,
#   "ambiguities": ["vague_analysis"],
#   "clarification_questions": ["请具体说明要分析什么内容"],
#   "missing_parameters": ["analysis_type"]
# }
```

#### 2. 生成澄清问题

```python
# 使用 LLM 生成自然的澄清问题
response = manager.get_clarification_response(user_input)
# 输出: "您想分析什么内容呢？比如：
# - 统计图层的面积、长度、数量
# - 分析空间分布特征
# - 检查数据质量
# 请具体说明一下。"
```

#### 3. 在 Agent 中集成

```python
# 在 processor.py 中集成
def agent_chat(self, user_input, ...):
    # 1. 检查是否需要澄清
    clarification = clarification_manager.get_clarification_response(user_input)
    if clarification:
        return clarification, "clarification"

    # 2. 继续正常处理
    # ...
```

---

## 🎯 完整使用场景

### 场景1：录制并复用工作流

```
# 第一次对话
用户：对道路图层做100米缓冲区分析
Agent：执行缓冲区分析...
用户：保存结果
Agent：保存成功！

# 录制工作流
workflow = recorder.record_from_conversation(
    tool_calls=conversation.tool_calls,
    workflow_name="道路缓冲区分析"
)

# 第二次对话（新工程）
用户：使用之前的工作流分析河流图层
Agent：找到工作流"道路缓冲区分析"，参数替换为河流图层...
Agent：执行完成！
```

### 场景2：主动澄清模糊请求

```
用户：分析一下
Agent：请具体说明要分析什么内容：
1. 统计图层的面积、长度、数量
2. 分析空间分布特征
3. 检查数据质量
4. 其他

用户：统计面积
Agent：好的，请提供要统计的图层名称
```

### 场景3：生成可独立运行的代码

```python
# 生成工作流代码
code = executor.generate_workflow_code(workflow)

# 保存为 .py 文件
with open("buffer_analysis.py", "w") as f:
    f.write(code)

# 在 QGIS 中运行
exec(open("buffer_analysis.py").read())
```

---

## 📁 文件结构

```
qgis_agent/
├── workflow_recorder.py      # 工作流录制器
├── workflow_executor.py      # 工作流执行器
├── clarification_manager.py  # 主动提问管理器
├── workflows/                # 保存的工作流模板
│   ├── buffer_analysis.json
│   └── clip_analysis.json
└── INTERACTIVE_FEATURES.md   # 本文档
```

---

## 🚀 集成到 processor.py

```python
from .workflow_recorder import WorkflowRecorder
from .workflow_executor import WorkflowExecutor
from .clarification_manager import ClarificationManager

class Processor(QObject):
    def __init__(self, ...):
        # ... 现有初始化 ...

        # 新增组件
        self.workflow_recorder = WorkflowRecorder()
        self.workflow_executor = WorkflowExecutor()
        self.clarification_manager = ClarificationManager(self.llm)

    def agent_chat(self, user_input, ...):
        # 1. 检查是否需要澄清
        clarification = self.clarification_manager.get_clarification_response(
            user_input, context={"loaded_layers": self._get_loaded_layers()}
        )
        if clarification:
            return clarification, "clarification"

        # 2. 执行对话
        final_response, workflow = self._execute_conversation(user_input, ...)

        # 3. 录制工作流（如果有多次工具调用）
        if len(conversation.tool_calls) > 1:
            self.workflow_recorder.record_from_conversation(
                tool_calls=conversation.tool_calls,
                workflow_name=f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

        return final_response, workflow
```

---

## 📊 功能对比

| 特性 | 原有 | 新增 |
|------|------|------|
| 工具调用 | 每次重新理解 | 可复用工作流 |
| 用户交互 | 被动响应 | 主动澄清 |
| 代码生成 | 一次性 | 可保存为模板 |
| 多工程支持 | ❌ | ✅ |

---

## 💡 最佳实践

1. **录制有意义的工作流**
   - 多步骤操作
   - 常用分析流程
   - 可参数化的任务

2. **合理设置参数**
   - 使用 `${parameter}` 标记可替换参数
   - 提供默认值
   - 说明参数含义

3. **主动澄清时机**
   - 缺少关键参数时
   - 请求模糊时
   - 有多种实现方式时

4. **工作流命名**
   - 使用描述性名称
   - 添加标签分类
   - 记录用途说明

---

## 🔮 后续扩展

- [ ] 工作流市场：分享和下载工作流
- [ ] 工作流版本管理
- [ ] 工作流可视化编辑器
- [ ] 工作流性能分析
- [ ] 工作流依赖管理
