import json
import traceback as tb

from qgis.PyQt.QtCore import QThreadPool, pyqtSignal, QObject

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from .llm_providers import get_llm_instance
from .utils import get_current_timestamp, pack
from .response_worker import ReflectStreamWorker, ToolAgentWorker
from .qgis_tools import TOOL_DEFINITIONS, call_tool

# ── RAG 模块 ──
from .rag import DocStore, APIDocRetriever, Cookbook

# ── Query Tuning 模块 ──
from .query_tuning import QueryTuner, DataOverview

# Agent 系统提示词
AGENT_SYSTEM_PROMPT = """你是一个 QGIS 地理信息系统智能助手，运行在 QGIS 桌面版内部。

## 你的能力
你可以通过调用工具直接操作 QGIS，包括：
- 查看当前项目状态（图层列表、坐标系等）
- 添加/移除矢量图层和栅格图层
- 查看图层属性表和要素数据
- 缩放到指定图层
- 设置图层标注（Labeling）
- 执行 QGIS Processing 处理算法（缓冲区、裁剪、相交、字段计算等）
- 直接执行 PyQGIS 代码完成复杂操作
- 保存/加载项目文件
- 渲染地图为图片
- **检索 PyQGIS API 文档**（search_pyqgis_api）—— 在写代码前查询准确的 API 签名

## 工作方式
1. 收到用户请求后，先调用 get_qgis_info 了解当前 QGIS 项目状态
2. 在执行 execute_pyqgis 或 execute_processing 之前，**强烈建议先调用 search_pyqgis_api 检索相关 API 文档**，确保参数准确
3. 根据需要调用其他工具执行操作
4. 每次工具调用后，根据返回结果决定下一步
5. 最终向用户汇报操作结果

## 重要规则
- 始终用中文回复用户
- 操作文件时使用绝对路径
- 执行操作前确认图层存在
- 如果工具返回错误，分析原因并尝试修复
- 当用户问"有哪些图层"、"当前项目状态"等查询类问题时，直接调用 get_qgis_info
- 当用户要求添加数据时，先检查文件路径是否存在
- 对于复杂的多步骤任务，逐步执行并汇报进度

## 长期记忆
你拥有长期记忆能力。通过 save_memory 工具可以保存重要信息（用户偏好、常用路径、项目配置、重要结论等），通过 load_memory 工具可以读取之前的记忆。
**重要规则**：
- 当用户告诉你重要偏好、常用设置、项目关键信息时，主动调用 save_memory 保存
- 当用户的问题可能涉及之前保存的信息时，先调用 load_memory 查看记忆
- 在每次对话开始时，记忆内容已自动注入到下方，可以直接使用

## QGIS 环境信息
你正在 QGIS 中运行，可以直接操作 iface（QGIS界面）、QgsProject（当前项目）等对象。
Processing 算法 ID 格式为 "provider:algorithm"，如 "native:buffer"、"gdal:contour"。

### execute_pyqgis 可用类型（已预导入，无需 import）
以下类型已在 execute_pyqgis 环境中预先导入，生成代码时可直接使用：
QgsPoint, QgsPointXY, QgsGeometry, QgsFeature, QgsField, QgsFields,
QgsWkbTypes, QgsCoordinateTransform, QgsFeatureRequest, QgsDistanceArea, QgsUnitTypes,
QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem, QgsProject, Qgis, iface,
QColor, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer,
QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer, QgsSymbol,
QgsRendererCategory, QgsRendererRange,
QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat

### 标注（Labeling）操作规则 — 极其重要！
- **严禁通过 execute_pyqgis 代码方式设置标注！** QGIS 各版本标注 API 差异巨大，代码方式极易失败
- **必须使用 set_layer_labeling 工具**来启用/禁用/修改图层标注
- set_layer_labeling 工具内部已处理所有版本兼容问题
- 如果用户说"显示标签"、"加标注"、"显示名称"、"标注XX字段"等，直接调用 set_layer_labeling 工具

### 图层样式/渲染操作规则
- 如需修改图层颜色、符号样式，优先使用 execute_pyqgis，环境中已预导入 QgsFillSymbol 等渲染类
- QColor 已预导入，直接用 QColor("#RRGGBB") 创建颜色

### PyQGIS 类型兼容性注意事项
- QgsPoint(x, y) 是 3D 点（含 z），QgsPointXY(x, y) 是 2D 点
- 遍历要素几何顶点时：vertex = feature.geometry().get().vertexAt(i)，返回 QgsPoint
- 构建几何时：QgsGeometry.fromPointXY(QgsPointXY(x, y)) 或 QgsGeometry.fromPolylineXY([QgsPointXY(...)])
- 要素几何访问：feature.geometry().asPoint() 返回 QgsPointXY，feature.geometry().get() 返回 QgsAbstractGeometry
- 创建新要素时用 QgsFeature(layer.fields()) 初始化，再用 feature.setGeometry() 和 feature.setAttributes()

### 常见 API 名称陷阱（QGIS 3.x 实际 API）
- 几何验证：geom.isGeosValid() 而非 geom.isValid()
- 几何简化：geom.simplify(tolerance) 而非 geom.simplifyGeometry()
- 获取图层要素数：layer.featureCount() 而非 layer.feature_count()
- 图层字段列表：layer.fields() 返回 QgsFields，遍历用 for field in layer.fields()
- 坐标变换：QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance()) 需要三个参数
- 获取地图画布：iface.mapCanvas() 返回 QgsMapCanvas
"""


class Processor(QObject):
    response_ready = pyqtSignal(str, str, str, str)
    reflection_ready = pyqtSignal(str, str, str, str)
    thinking = pyqtSignal(str)  # 实时流式思考内容
    tool_status = pyqtSignal(str)  # 工具执行状态提示
    error_signal = pyqtSignal(str)

    def __init__(self, llm_id, conversation_id, dataloader, temperature=0.0):
        from langchain_core.output_parsers import StrOutputParser

        super().__init__()
        self.latest_interaction_id = None
        self.llm_id = llm_id
        self.conversation_id = conversation_id
        self.dataloader = dataloader
        self.temperature = temperature
        model_name, endpoint, api_key = dataloader.fetch_llm_info(llm_id)
        self.model_name = model_name
        self.provider = llm_id.split("::", 1)[0]
        self.llm = get_llm_instance(self.provider, model_name, api_key, endpoint, temperature=temperature)
        self.streaming_llm = get_llm_instance(self.provider, model_name, api_key, endpoint, temperature=temperature)
        self.output_parser = StrOutputParser()
        self.threadpool = QThreadPool()
        self.max_tool_rounds = 10  # 最大工具调用轮次，防止死循环
        self._cancelled = False  # 中断标志
        self._code_confirm_callback = None  # 代码执行确认回调

        # ── RAG 组件 ──
        self.doc_store = DocStore()
        self.retriever = APIDocRetriever(self.doc_store)
        self.cookbook = Cookbook(self.doc_store)

        # ── Query Tuning 组件 ──
        self.query_tuner = QueryTuner(self.llm)
        self.data_overview = DataOverview()

    def cancel(self):
        """设置中断标志，后台线程会在下一轮循环前检查"""
        self._cancelled = True
        # 清空线程池中等待的任务
        self.threadpool.clear()

    # ── Agent 模式：带工具调用的智能对话 ──

    def agent_chat(self, user_input: str, thinking_callback=None, tool_status_callback=None) -> tuple:
        """
        Agent 对话：LLM 可以调用 QGIS 工具完成用户请求。
        支持多轮工具调用（观察→操作→反馈循环）。
        集成 RAG API 检索 + Cookbook 自我进化。
        返回 (最终回复文本, workflow_tag)
        """
        import json
        from langchain_core.messages import ToolMessage

        request_time = get_current_timestamp()

        # ── Query Tuning: 优化用户查询 ──
        try:
            data_overview_text = self.data_overview.get_data_overview()
            tuned_query = self.query_tuner.tune_query(user_input, data_overview_text)
            if thinking_callback:
                thinking_callback(f"[Query Tuning] 优化查询: {tuned_query[:100]}...\n")
        except Exception as e:
            # Query tuning失败不影响主流程
            tuned_query = user_input

        # ── 加载长期记忆 ──
        system_prompt = AGENT_SYSTEM_PROMPT
        memory_content = ""
        try:
            import os
            from qgis.core import QgsApplication
            memory_path = os.path.join(
                QgsApplication.qgisSettingsDirPath(),
                "python", "plugins", "qgis_agent", "MEMORY.md"
            )
            if os.path.exists(memory_path):
                with open(memory_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                if raw:
                    # 截断过长记忆
                    if len(raw) > 4000:
                        raw = raw[:4000] + "\n\n...(记忆过长已截断)"
                    memory_content = raw
        except Exception:
            pass

        if memory_content:
            system_prompt += f"\n\n## 长期记忆内容（来自 MEMORY.md）\n以下是之前保存的重要信息，请优先参考：\n\n{memory_content}"

        # ── Cookbook 检索：查找相似历史案例 ──
        cookbook_context = ""
        try:
            cookbook_results = self.cookbook.search_for_task(user_input, top_k=2)
            if cookbook_results:
                cookbook_context = self.cookbook.format_as_context(cookbook_results)
        except Exception:
            pass

        if cookbook_context:
            system_prompt += f"\n\n{cookbook_context}"

        # ── 加载对话历史上下文 ──
        messages = [SystemMessage(content=system_prompt)]
        history_limit = 20  # 最多加载最近 20 条历史消息（10 轮对话）

        try:
            history_rows = self.dataloader.select_interaction(self.conversation_id)
            if history_rows:
                # 取最近的 N 条，避免 token 溢出
                recent_rows = history_rows[-history_limit:]
                for row in recent_rows:
                    interaction = pack(row, "interaction")
                    if interaction.get("typeMessage") == "input":
                        messages.append(HumanMessage(content=interaction.get("requestText", "")))
                    elif interaction.get("typeMessage") == "return":
                        messages.append(AIMessage(content=interaction.get("responseText", "")))
        except Exception:
            pass  # 历史加载失败不影响本次对话

        # 添加当前用户输入
        messages.append(HumanMessage(content=user_input))

        all_tool_calls_log = []
        final_response = ""
        workflow = "empty"

        for round_idx in range(self.max_tool_rounds):
            # 检查中断标志
            if self._cancelled:
                self._cancelled = False
                final_response = "⏹ 用户中断了操作。"
                workflow = "empty"
                break

            # 绑定工具到 LLM（如果不支持 tool calling 则回退到普通对话）
            try:
                llm_with_tools = self.llm.bind_tools(TOOL_DEFINITIONS)
            except (AttributeError, TypeError, NotImplementedError, Exception):
                # 模型不支持 function calling，直接普通对话
                if thinking_callback:
                    thinking_callback(f"[思考中...]\n")
                response = self.llm.invoke(messages)
                final_response = (response.content if hasattr(response, 'content') and response.content
                                  else str(response))
                if thinking_callback:
                    thinking_callback(final_response)
                break

            if thinking_callback:
                thinking_callback(f"[思考中...]\n")

            # 非流式调用（带 tool_choice="auto"）
            response = llm_with_tools.invoke(messages)

            # 检查是否有工具调用
            tool_calls = getattr(response, 'tool_calls', None) or []

            if not tool_calls:
                # 没有工具调用，LLM 给出了最终回复
                final_response = (response.content if hasattr(response, 'content') and response.content
                                  else str(response))
                if thinking_callback:
                    thinking_callback(final_response)
                break

            # 处理工具调用
            messages.append(response)

            for tool_call in tool_calls:
                tool_name = tool_call.get('name', '')
                tool_args = tool_call.get('args', {})
                tool_id = tool_call.get('id', '')

                status_msg = f"🔧 调用工具: {tool_name}..."
                if thinking_callback:
                    thinking_callback(f"\n{status_msg}\n")
                if tool_status_callback:
                    tool_status_callback(status_msg)

                # ── RAG 检索增强：对危险工具，先查 API 文档 ──
                if tool_name in ("execute_pyqgis", "execute_processing"):
                    try:
                        api_docs = self.retriever.search_for_tool_call(tool_name, tool_args)
                        if api_docs:
                            doc_context = self.retriever.format_as_context(api_docs)
                            if doc_context and thinking_callback:
                                thinking_callback(f"📚 RAG 检索到 {len(api_docs)} 条相关 API 文档\n")
                    except Exception:
                        pass  # RAG 检索失败不阻塞流程

                # 执行工具
                try:
                    result = call_tool(tool_name, tool_args)
                    result_str = json.dumps(result, ensure_ascii=False, indent=2)
                    all_tool_calls_log.append({
                        "tool": tool_name,
                        "args": tool_args,
                        "result": result,
                    })
                    workflow = "withTool"
                except Exception as e:
                    result_str = json.dumps({"error": str(e), "traceback": tb.format_exc()}, ensure_ascii=False)

                # 截断过长的结果
                if len(result_str) > 4000:
                    result_str = result_str[:4000] + "\n...(结果已截断)"

                if thinking_callback:
                    thinking_callback(f"📋 结果:\n{result_str[:500]}\n")

                # 添加工具消息到对话
                messages.append(ToolMessage(content=result_str, tool_call_id=tool_id))

            # 本轮结束，继续下一轮
            if thinking_callback:
                thinking_callback("\n---\n")

        else:
            # 达到最大轮次，强制要求 LLM 总结
            messages.append(HumanMessage(content="请基于以上工具执行结果，用中文总结完成情况。"))
            try:
                final_resp = self.llm.invoke(messages)
                final_response = (final_resp.content if hasattr(final_resp, 'content') and final_resp.content
                                  else str(final_resp))
            except Exception:
                final_response = "已达到最大工具调用轮次，操作已完成但无法生成总结。"
            if thinking_callback:
                thinking_callback(final_response)

        response_time = get_current_timestamp()
        prompt_id = f"{self.llm_id}::0::agent"

        # ── Cookbook 自动归档 ──
        try:
            self.cookbook.archive_from_agent_result(
                user_input=user_input,
                tool_calls_log=all_tool_calls_log,
                final_response=final_response,
                success=True,
            )
        except Exception:
            pass  # 归档失败不影响主流程

        # 保存交互记录
        tool_log = json.dumps(all_tool_calls_log, ensure_ascii=False) if all_tool_calls_log else ""
        interaction_row = [self.conversation_id, prompt_id, user_input, "", request_time, "return", final_response, response_time, workflow, tool_log]
        interaction_id = self.dataloader.insert_interaction(interaction_row, self.conversation_id)
        self.latest_interaction_id = interaction_id

        return final_response, workflow

    # ── 保留旧的简单对话方法（兼容性） ──

    def general_chat(self, user_input: str) -> str:
        request_time = get_current_timestamp()
        prompt_row = {
            "template": "你是一个QGIS地理信息系统助手。请用中文回答：{input}",
            "ID": "generalChat"
        }
        human_message = HumanMessage(content=prompt_row["template"].format(input=user_input))
        result = self.llm.invoke([human_message])
        response = self.output_parser.invoke(result)
        response_time = get_current_timestamp()

        prompt_id = f"{self.llm_id}::0::generalChat"
        interaction_row = [self.conversation_id, prompt_id, user_input, "", request_time, "return", response, response_time, "empty", ""]
        self.dataloader.insert_interaction(interaction_row, self.conversation_id)
        return response

    def code_producer(self, user_input: str):
        request_time = get_current_timestamp()
        prompt_row = {
            "template": "你是一个PyQGIS代码生成专家。根据以下用户需求生成PyQGIS Python代码，代码放在```python代码块中：{input}",
            "ID": "codeProducer"
        }
        human_message = HumanMessage(content=prompt_row["template"].format(input=user_input))
        result = self.llm.invoke([human_message])
        response = self.output_parser.invoke(result)
        response_time = get_current_timestamp()

        prompt_id = f"{self.llm_id}::0::codeProducer"
        interaction_row = [self.conversation_id, prompt_id, user_input, "", request_time, "return", response, response_time, "withCode", ""]
        interaction_id = self.dataloader.insert_interaction(interaction_row, self.conversation_id)
        self.latest_interaction_id = interaction_id
        return response, "withCode"

    # ── 入口方法 ──

    def response(self, user_input, response_type):
        """同步响应（阻塞，仅用于兼容）"""
        return self.agent_chat(user_input)

    def response_stream(self, user_input, response_type, thinking_callback, tool_status_callback=None):
        """流式 Agent 对话"""
        return self.agent_chat(user_input, thinking_callback, tool_status_callback)

    # ── 异步入口 ──

    def async_response(self, user_input, response_type):
        self._cancelled = False  # 重置中断标志
        worker = ToolAgentWorker(self, user_input)
        worker.signals.thinking.connect(self.thinking.emit)
        # tool_status 直接透传，不经过 processor 中转（避免跨线程信号链问题）
        worker.signals.tool_status.connect(self.tool_status.emit)
        worker.signals.finished.connect(
            lambda resp, workflow: self.response_ready.emit(user_input, response_type, resp, workflow)
        )
        worker.signals.error.connect(self.error_signal.emit)
        self.threadpool.start(worker)

    def async_reflect(self, log_message, executed_code, response_type="code"):
        worker = ReflectStreamWorker(self, executed_code, log_message, response_type)
        worker.signals.thinking.connect(self.thinking.emit)
        worker.signals.finished.connect(
            lambda resp, workflow: self.reflection_ready.emit(log_message, response_type, resp, workflow)
        )
        worker.signals.error.connect(self.error_signal.emit)
        self.threadpool.start(worker)

    def reflect(self, log_message, executed_code, response_type="code"):
        try:
            request_time = get_current_timestamp()
            latest_row = self.dataloader.select_latest_interaction(self.conversation_id, self.latest_interaction_id)
            latest_interaction = pack(latest_row, "interaction")
            user_input = latest_interaction["requestText"]
            ai_response = latest_interaction["responseText"]

            prompt = f"""
            你生成的PyQGIS代码执行时出错。请分析错误并修复代码。

            原始需求: {user_input}
            生成的代码: {ai_response}
            实际执行的代码: {executed_code}
            错误信息: {log_message}

            请提供修正后的代码，放在```python代码块中。
            """
            human_message = HumanMessage(content=prompt)
            result = self.llm.invoke([human_message])
            response = self.output_parser.invoke(result)
            response_time = get_current_timestamp()

            prompt_id = f"{self.llm_id}::0::codeProducer"
            interaction_row = [self.conversation_id, prompt_id, user_input, prompt, request_time, "return", response, response_time, "withCode", ""]
            interaction_id = self.dataloader.insert_interaction(interaction_row, self.conversation_id)
            self.latest_interaction_id = interaction_id
            return response, "withCode"
        except Exception as e:
            return f"修正失败: {str(e)}", "empty"
