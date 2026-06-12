# QGIS Agent 测试指南

## 安装状态

✅ 插件已安装到: `C:\Users\zhaos\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\qgis_agent\`

## 测试步骤

### 1. 启动 QGIS

双击桌面 QGIS 图标或从开始菜单启动 QGIS。

### 2. 启用插件

1. 在菜单栏中选择 **插件** → **管理和安装插件...**
2. 在左侧选择 **已安装** 标签页
3. 在列表中找到 **QGIS Agent**
4. 勾选 **启用** 复选框
5. 关闭插件管理器

### 3. 打开 Agent 面板

插件启用后，右侧应该出现 QGIS Agent 面板。如果没有：
- 选择菜单 **插件** → **QGIS Agent** → **打开 QGIS Agent**

### 4. 配置 LLM 模型

首次使用需要配置 LLM 模型：

1. 点击面板底部的 **模型配置** 标签页
2. 点击 **添加模型** 按钮
3. 选择参考模板（如 DeepSeek）
4. 输入 API Key
5. 点击 **添加**

### 5. 测试基本功能

#### 测试 1: 查询项目信息
在对话框中输入:
```
当前项目有哪些图层？
```
期望: Agent 调用 `get_qgis_info` 工具并返回图层列表

#### 测试 2: 添加矢量图层
在对话框中输入:
```
添加一个 Shapefile 图层: D:\项目文件\01_原始数据\test.shp
```
期望: Agent 调用 `add_vector_layer` 工具

#### 测试 3: 执行 Processing 算法
在对话框中输入:
```
对图层做缓冲区分析，距离 100 米
```
期望: Agent 调用 `execute_processing` 工具

#### 测试 4: 执行 PyQGIS 代码
在对话框中输入:
```
获取当前项目的坐标系信息
```
期望: Agent 可能调用 `execute_pyqgis` 工具

### 6. 测试 Agent Loop 功能

#### 测试工具调用循环
输入一个需要多步骤的任务:
```
查看所有图层的属性表结构
```
期望: Agent 会先调用 `get_qgis_info` 获取图层列表，然后对每个图层调用 `get_layer_features`

#### 测试 RAG 检索
输入:
```
如何创建一个缓冲区？
```
期望: Agent 可能调用 `search_pyqgis_api` 检索相关 API 文档

#### 测试记忆功能
输入:
```
记住：我喜欢使用 CGCS2000 坐标系
```
期望: Agent 调用 `save_memory` 保存记忆

### 7. 查看测试结果

在 QGIS Python 控制台中查看日志:
1. 选择菜单 **插件** → **Python 控制台**
2. 查看是否有错误信息

### 8. 常见问题

#### 问题 1: 插件未显示
- 检查 Python 路径是否正确
- 重启 QGIS

#### 问题 2: 缺少依赖
- 在 Python 控制台中运行:
```python
import langchain
import langchain_core
import langchain_openai
```
- 如果报错，需要安装依赖:
```bash
pip install langchain langchain_core langchain_openai langchain_deepseek
```

#### 问题 3: API Key 错误
- 确保在模型配置中正确输入了 API Key
- 检查网络连接

## 测试新架构

新的 Agent Loop 架构已集成到插件中，但默认使用原始的 `Processor` 类。

要测试新的 `AgentLoopProcessor`，需要修改 `qgis_agent.py`:

```python
# 在 _init_plugin 方法中，找到:
from .processor import Processor as _ProcessorClass

# 替换为:
try:
    from .agent_loop.processor import AgentLoopProcessor as _ProcessorClass
except ImportError:
    from .processor import Processor as _ProcessorClass
```

## 反馈

测试过程中遇到任何问题，请记录:
1. 错误信息
2. 复现步骤
3. 期望行为
4. 实际行为
