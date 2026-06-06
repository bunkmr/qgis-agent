简介
========

QGIS Agent 是什么？
--------------------

QGIS Agent 是一个 QGIS 桌面插件，将大语言模型 (LLM) 的智能能力嵌入到
QGIS 地理信息系统环境中。用户可以用自然语言与 QGIS 交互，Agent 会自动
调用 QGIS 工具完成复杂的空间数据处理任务。

核心特性
--------

- **自然语言交互**: 无需编写 Python/PyQGIS 代码，用中文描述需求即可
- **Agent 自主决策**: LLM 自主选择调用哪个 QGIS 工具，支持多轮工具调用
- **流式思考展示**: 实时显示 LLM 推理过程，透明可控
- **代码安全确认**: 执行 PyQGIS 代码前弹窗确认，避免误操作
- **长期记忆**: 自动保存用户偏好、常用路径等跨对话信息
- **线程安全**: 所有 LLM 调用在后台线程执行，不阻塞 QGIS 界面

技术架构
--------

::

    用户输入 → Conversation → Processor (LangChain Agent)
                                  ├── LLM (DeepSeek/OpenAI/GLM...)
                                  ├── Tool Definitions (11个工具)
                                  └── MainThreadBridge (线程安全调度)
                                       ├── QGIS API (主线程)
                                       └── QWaitCondition (同步等待)

工作流程
--------

1. 用户输入自然语言请求
2. Processor 将请求发送给 LLM
3. LLM 分析需求，决定调用哪些工具
4. 工具通过 MainThreadBridge 在主线程中安全执行
5. 工具结果返回给 LLM，LLM 决定是否继续调用工具
6. 最终 LLM 生成回复展示给用户
