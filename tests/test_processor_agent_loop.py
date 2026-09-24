# -*- coding: utf-8 -*-
"""processor.Processor.agent_chat 测试（项目最核心的循环）

在裸环境下用替身把 processor 拉起来：
    qgis.*           -> support.install_qgis_stub()
    langchain_core   -> support.install_langchain_stub()
    .qgis_tools      -> 真实 TOOL_DEFINITIONS + 假的 call_tool（不碰 QGIS 运行时）
    .rag/.query_tuning/.response_worker/.llm_providers -> 替身
LLM 与 DataLoader 用 tests/fakes.py 的假实现，全程无网络。
"""

import importlib
import os
import shutil
import sys
import tempfile
import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401
import fakes

PROCESSOR_MODULE = "qgis_agent.processor"
_MISSING = object()


class ProcessorHarness:
    """替身环境管理器：进入时安装替身并导入 processor，退出时完整还原"""

    def __init__(self, llm, tool_results=None):
        self.llm = llm
        self.tool_results = tool_results or {}
        self.tool_calls = []
        self._saved_modules = {}
        self._saved_attrs = {}

    def _stub(self, name, **attrs):
        self._saved_modules[name] = sys.modules.get(name)
        parent_name, _, child = name.rpartition(".")
        if parent_name:
            parent = sys.modules.get(parent_name)
            if parent is not None:
                self._saved_attrs[(parent, child)] = getattr(parent, child, _MISSING)
        support.install_module_stub(name, **attrs)

    def _call_tool(self, name, args):
        self.tool_calls.append({"tool": name, "args": dict(args or {})})
        value = self.tool_results.get(name)
        if callable(value):
            return value(args)
        if value is None:
            return {"executed": True, "ok": True}
        return value

    def load(self):
        support.install_qgis_stub()
        support.install_langchain_stub()

        real_tools = importlib.import_module("qgis_agent.qgis_tools")
        self._stub(
            "qgis_agent.qgis_tools",
            TOOL_DEFINITIONS=list(real_tools.TOOL_DEFINITIONS),
            TOOL_MAP=dict(getattr(real_tools, "TOOL_MAP", {}) or {}),
            call_tool=self._call_tool,
        )
        self._stub("qgis_agent.rag",
                   DocStore=support._Raising,
                   APIDocRetriever=support._Raising,
                   Cookbook=support._Raising)
        self._stub("qgis_agent.query_tuning",
                   QueryTuner=support._Raising,
                   DataOverview=support._Raising)
        self._stub("qgis_agent.response_worker",
                   ReflectStreamWorker=object,
                   ToolAgentWorker=object)
        self._stub("qgis_agent.llm_providers",
                   get_llm_instance=lambda *args, **kwargs: self.llm,
                   # processor 用 (effective, reason) 解包：替身必须同签名，
                   # 否则 `from .llm_providers import ...` 会直接 ImportError。
                   resolve_browser_tls=lambda requested=False: (bool(requested), ""))

        sys.modules.pop(PROCESSOR_MODULE, None)
        return importlib.import_module(PROCESSOR_MODULE)

    def close(self):
        for (parent, child), value in self._saved_attrs.items():
            if value is _MISSING:
                try:
                    delattr(parent, child)
                except AttributeError:
                    pass
            else:
                setattr(parent, child, value)
        for name, value in self._saved_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        sys.modules.pop(PROCESSOR_MODULE, None)


class ProcessorTestCase(unittest.TestCase):
    LLM_ID = "GLM::glm-4"
    CONVERSATION_ID = "conv1"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qgis_agent_proc_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.history_path = os.path.join(self.tmp, "debug_history.json")

    def load_processor(self, llm, tool_results=None):
        harness = ProcessorHarness(llm, tool_results)
        self.addCleanup(harness.close)
        module = harness.load()
        module.Processor._debug_history_path = staticmethod(lambda: self.history_path)
        return harness, module

    def new_processor(self, llm, tool_results=None, dataloader=None, **kwargs):
        harness, module = self.load_processor(llm, tool_results)
        loader = dataloader if dataloader is not None else fakes.FakeDataloader()
        processor = module.Processor(self.LLM_ID, self.CONVERSATION_ID, loader, **kwargs)
        return harness, module, processor, loader


class TestConstruction(ProcessorTestCase):
    def test_provider_and_model_parsed_from_llm_id(self):
        llm = fakes.FakeToolCallingLLM(["ok"])
        _h, _m, proc, loader = self.new_processor(llm)
        self.assertEqual(proc.provider, "GLM")
        self.assertEqual(proc.model_name, "glm-4")
        self.assertEqual(loader.fetch_llm_info_calls, [self.LLM_ID])

    def test_optional_components_degrade_to_none(self):
        """RAG / QueryTuning 不可用时不得阻塞构造"""
        llm = fakes.FakeToolCallingLLM(["ok"])
        _h, _m, proc, _loader = self.new_processor(llm)
        self.assertIsNone(proc.doc_store)
        self.assertIsNone(proc.retriever)
        self.assertIsNone(proc.cookbook)
        self.assertIsNone(proc.query_tuner)
        self.assertIsNone(proc.data_overview)

    def test_debugger_is_real_smart_debugger(self):
        llm = fakes.FakeToolCallingLLM(["ok"])
        _h, _m, proc, _loader = self.new_processor(llm)
        self.assertIsNotNone(proc.debugger)
        self.assertTrue(hasattr(proc.debugger, "analyze_error"))

    def test_real_tool_definitions_are_bound_to_llm(self):
        """绑定给模型的必须是 qgis_tools 里真实的工具清单，而不是替身造的空表"""
        llm = fakes.FakeToolCallingLLM(["ok"])
        _h, _m, proc, _l = self.new_processor(llm)
        proc.agent_chat("x")

        self.assertIsNotNone(llm.bound_tools)
        names = {tool["name"] for tool in llm.bound_tools}
        self.assertIn("get_qgis_info", names)
        self.assertIn("execute_pyqgis", names)
        self.assertGreaterEqual(len(names), 10)


class TestSingleToolRound(ProcessorTestCase):
    def test_tool_call_then_final_answer(self):
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}, "call_1"),
            "共有 3 个图层",
        ])
        harness, module, proc, loader = self.new_processor(
            llm, {"get_qgis_info": {"layers": [{"name": "roads"}]}})

        text, workflow = proc.agent_chat("有哪些图层")

        self.assertEqual(text, "共有 3 个图层")
        self.assertEqual(workflow, "withTool")
        self.assertEqual([c["tool"] for c in harness.tool_calls], ["get_qgis_info"])
        self.assertEqual(llm.invoke_count, 2)

    def test_tool_result_is_wrapped_in_untrusted_fence(self):
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}, "call_1"),
            "done",
        ])
        _h, module, proc, _l = self.new_processor(llm, {"get_qgis_info": {"layers": []}})
        proc.agent_chat("有哪些图层")

        tool_msg = llm.find_message(lambda m: type(m).__name__ == "ToolMessage")
        self.assertIsNotNone(tool_msg)
        self.assertIn(module.UNTRUSTED_BEGIN, tool_msg.content)
        self.assertIn(module.UNTRUSTED_END, tool_msg.content)
        self.assertEqual(tool_msg.tool_call_id, "call_1")

    def test_interaction_is_persisted(self):
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}, "call_1"),
            "done",
        ])
        _h, _m, proc, loader = self.new_processor(llm, {"get_qgis_info": {}})
        proc.agent_chat("我的问题")

        self.assertEqual(len(loader.inserted), 1)
        record = loader.inserted[0]
        self.assertEqual(record["conversation_id"], self.CONVERSATION_ID)
        row = record["row"]
        self.assertEqual(row[2], "我的问题")     # requestText
        self.assertEqual(row[5], "return")       # typeMessage
        self.assertEqual(row[6], "done")         # responseText
        self.assertEqual(row[8], "withTool")     # workflow
        self.assertIn("get_qgis_info", row[9])   # executionLog(工具日志)
        self.assertEqual(proc.latest_interaction_id, record["id"])

    def test_multiple_tool_calls_in_one_round(self):
        first = fakes.FakeHttpResponse(
            content="",
            tool_calls=[
                {"name": "get_qgis_info", "args": {}, "id": "c1"},
                {"name": "get_layer_features", "args": {"layer_id": "l1"}, "id": "c2"},
            ])
        llm = fakes.FakeToolCallingLLM([first, "两个工具都跑完了"])
        harness, _m, proc, _l = self.new_processor(llm)

        text, workflow = proc.agent_chat("查图层和要素")

        self.assertEqual(text, "两个工具都跑完了")
        self.assertEqual([c["tool"] for c in harness.tool_calls],
                         ["get_qgis_info", "get_layer_features"])
        tool_messages = [m for m in llm.calls[1] if type(m).__name__ == "ToolMessage"]
        self.assertEqual(len(tool_messages), 2)

    def test_huge_tool_result_is_truncated(self):
        huge = {"blob": "x" * 10000}
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("get_layer_features", {"layer_id": "l1"}),
            "done",
        ])
        _h, _m, proc, _l = self.new_processor(llm, {"get_layer_features": huge})
        proc.agent_chat("导出属性表")

        tool_msg = llm.find_message(lambda m: type(m).__name__ == "ToolMessage")
        self.assertIn("...(结果已截断)", tool_msg.content)
        self.assertLess(len(tool_msg.content), 5000)


class TestHistoryReconstruction(ProcessorTestCase):
    def test_history_rows_become_messages(self):
        history = [
            fakes.make_interaction_row(ID="conv10", conversationID=self.CONVERSATION_ID,
                                       typeMessage="input", requestText="第一个问题"),
            fakes.make_interaction_row(ID="conv11", conversationID=self.CONVERSATION_ID,
                                       typeMessage="return", requestText="第二个问题",
                                       responseText="第二个回答"),
        ]
        llm = fakes.FakeToolCallingLLM(["直接回答"])
        _h, module, proc, loader = self.new_processor(llm, dataloader=fakes.FakeDataloader(history=history))

        proc.agent_chat("当前问题")

        self.assertEqual(loader.select_interaction_calls, [self.CONVERSATION_ID])
        self.assertEqual(llm.contents_of(0), [
            ("SystemMessage", module.AGENT_SYSTEM_PROMPT),
            ("HumanMessage", "第一个问题"),
            ("HumanMessage", "第二个问题"),
            ("AIMessage", "第二个回答"),
            ("HumanMessage", "当前问题"),
        ])

    def test_history_failure_does_not_break_chat(self):
        class BrokenLoader(fakes.FakeDataloader):
            def select_interaction(self, conversation_id, columns=None):
                raise RuntimeError("数据库坏了")

        llm = fakes.FakeToolCallingLLM(["仍然可以回答"])
        _h, module, proc, _loader = self.new_processor(llm, dataloader=BrokenLoader())
        text, _wf = proc.agent_chat("问题")
        self.assertEqual(text, "仍然可以回答")
        self.assertEqual(llm.contents_of(0), [
            ("SystemMessage", module.AGENT_SYSTEM_PROMPT),
            ("HumanMessage", "问题"),
        ])


class TestSystemMessageInvariant(ProcessorTestCase):
    """system 消息不变式：**有且仅有一条，且必须位于首位**。

    实测踩坑（2026-09-24 用户报障）：旧实现把 Query Tuning 的改写结果作为
    **第二条** SystemMessage 追加在系统提示词之后，Qwen3 系的 chat template
    直接拒绝：

        Error: Jinja Exception: System message must be at the beginning.

    llama.cpp 把它包成 HTTP 500，用户侧只看到「模型服务内部错误」，而同一个
    模型在别的客户端（不带 system）一切正常 —— 这类问题极难自查。所以这里把
    不变式锁死：改写结果只能**并入**第一条系统消息，不能另起一条。
    """

    TUNED = "把「画个图」改写得更具体"

    class _Tuner:
        def tune_query(self, user_input, overview):
            return TestSystemMessageInvariant.TUNED

    class _Overview:
        def get_data_overview(self):
            return "图层 3 个"

    def _processor_with_tuning(self, llm):
        _h, module, proc, _l = self.new_processor(llm)
        proc.query_tuner = self._Tuner()
        proc.data_overview = self._Overview()
        return module, proc

    def test_tuned_query_merges_into_the_single_system_message(self):
        llm = fakes.FakeToolCallingLLM(["直接回答"])
        module, proc = self._processor_with_tuning(llm)

        proc.agent_chat("画个图")

        sent = llm.contents_of(0)
        roles = [role for role, _content in sent]
        self.assertEqual(
            roles.count("SystemMessage"), 1,
            "system 消息必须只有一条，否则 Qwen3 系模板会直接报 500：%s" % roles)
        self.assertEqual(roles[0], "SystemMessage",
                         "system 消息必须在首位：%s" % roles)

        system_text = sent[0][1]
        self.assertIn("用户意图改写", system_text,
                      "改写结果不能因为合并而丢失")
        self.assertIn(self.TUNED, system_text)
        self.assertIn(module.AGENT_SYSTEM_PROMPT[:20], system_text,
                      "原始系统提示词不能被改写结果顶掉")

    def test_no_system_message_after_first_when_history_exists(self):
        """带历史对话时同样只能有一条 system，且仍在首位。"""
        history = [
            fakes.make_interaction_row(ID="c1", conversationID=self.CONVERSATION_ID,
                                       typeMessage="input", requestText="之前的问题"),
            fakes.make_interaction_row(ID="c2", conversationID=self.CONVERSATION_ID,
                                       typeMessage="return", requestText="之前的问题",
                                       responseText="之前的回答"),
        ]
        llm = fakes.FakeToolCallingLLM(["直接回答"])
        _h, _m, proc, _l = self.new_processor(
            llm, dataloader=fakes.FakeDataloader(history=history))
        proc.query_tuner = self._Tuner()
        proc.data_overview = self._Overview()

        proc.agent_chat("继续")

        roles = [role for role, _c in llm.contents_of(0)]
        self.assertEqual(roles.count("SystemMessage"), 1, "roles=%s" % roles)
        self.assertEqual(roles[0], "SystemMessage", "roles=%s" % roles)


class TestToolErrorDiagnosis(ProcessorTestCase):
    def _error_result(self, message):
        return lambda args: {"executed": False, "error": message}

    def test_error_triggers_smart_debugger_diagnosis(self):
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("execute_pyqgis", {"code": "import geopandas"}),
            "修好了",
        ])
        _h, _m, proc, _l = self.new_processor(llm, {
            "execute_pyqgis": self._error_result("ModuleNotFoundError: No module named 'geopandas'")
        })
        proc.agent_chat("跑个脚本")

        diagnosis = llm.find_message(
            lambda m: "SmartDebugger 诊断结论" in getattr(m, "content", ""))
        self.assertIsNotNone(diagnosis)
        self.assertIn("import_errors", diagnosis.content)
        self.assertIn("出错工具: execute_pyqgis", diagnosis.content)
        self.assertTrue(any("🩺" in (args[0] if args else "")
                            for args in proc.execution_log.emitted))

    def test_repeated_failures_abort_with_diagnosis(self):
        """连续失败超过 DEBUG_MAX_RETRIES 后放弃重试，把诊断结论交给用户"""
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("execute_pyqgis", {"code": "x"})
        ] * 4)
        _h, module, proc, _l = self.new_processor(llm, {
            "execute_pyqgis": self._error_result("ModuleNotFoundError: No module named 'geopandas'")
        })

        text, workflow = proc.agent_chat("跑个脚本")

        self.assertIn("已放弃自动重试", text)
        self.assertIn("import_errors", text)
        self.assertEqual(workflow, "withTool")
        self.assertEqual(llm.invoke_count, module.DEBUG_MAX_RETRIES + 1)

    def test_tool_exception_is_converted_to_error_result(self):
        def boom(args):
            raise RuntimeError("工具内部炸了")

        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("execute_processing", {"algorithm": "native:buffer"}),
            "换个算法",
        ])
        _h, _m, proc, _l = self.new_processor(llm, {"execute_processing": boom})
        text, _wf = proc.agent_chat("做个缓冲区")

        self.assertEqual(text, "换个算法")
        self.assertTrue(any("❌" in (args[0] if args else "")
                            for args in proc.execution_log.emitted))


class TestLoopGuards(ProcessorTestCase):
    def test_max_rounds_forces_summary(self):
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}),
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}),
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}),
            "这是总结",
        ])
        _h, _m, proc, _l = self.new_processor(llm, {"get_qgis_info": {}})
        proc.max_tool_rounds = 3

        text, workflow = proc.agent_chat("一直查")

        self.assertEqual(text, "这是总结")
        self.assertEqual(llm.invoke_count, 4)  # 3 轮工具 + 1 次强制总结
        last_human = [m for m in llm.calls[-1] if type(m).__name__ == "HumanMessage"][-1]
        self.assertIn("总结", last_human.content)

    def test_cancelled_before_first_round(self):
        llm = fakes.FakeToolCallingLLM(["不会用到"])
        _h, _m, proc, _l = self.new_processor(llm)
        proc._cancelled = True

        text, workflow = proc.agent_chat("问题")

        self.assertEqual(text, "⏹ 用户中断了操作。")
        self.assertEqual(workflow, "empty")
        self.assertEqual(llm.invoke_count, 0)

    def test_unsupported_tool_calling_falls_back_to_plain_chat(self):
        llm = fakes.FakeToolCallingLLM(["纯文本回答"], bind_error=NotImplementedError("no tools"))
        _h, _m, proc, _l = self.new_processor(llm)

        text, workflow = proc.agent_chat("问题")

        self.assertEqual(text, "纯文本回答")
        self.assertEqual(workflow, "empty")
        self.assertEqual(llm.invoke_count, 1)

    def test_reuse_after_close_is_refused(self):
        llm = fakes.FakeToolCallingLLM(["不会用到"])
        _h, _m, proc, _l = self.new_processor(llm)
        proc._needs_recreate = True
        llm._http_client.close()

        text, workflow = proc.agent_chat("问题")

        self.assertIn("已中断", text)
        self.assertEqual(workflow, "empty")
        self.assertEqual(llm.invoke_count, 0)

    def test_cancel_closes_http_client_and_marks_recreate(self):
        llm = fakes.FakeToolCallingLLM(["x"])
        _h, _m, proc, _l = self.new_processor(llm)
        proc.cancel()
        self.assertTrue(llm._http_client.is_closed)
        self.assertTrue(proc._needs_recreate)
        self.assertTrue(proc._http_client_closed())

    def test_shutdown_closes_http_client(self):
        llm = fakes.FakeToolCallingLLM(["x"])
        _h, _m, proc, _l = self.new_processor(llm)
        proc.shutdown()
        self.assertTrue(proc._cancelled)
        self.assertTrue(llm._http_client.is_closed)
        self.assertTrue(proc._needs_recreate)

    def test_thinking_callback_receives_stream(self):
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("get_qgis_info", {}),
            "完成",
        ])
        _h, _m, proc, _l = self.new_processor(llm, {"get_qgis_info": {}})
        chunks = []
        proc.agent_chat("问题", thinking_callback=chunks.append)

        joined = "".join(chunks)
        self.assertIn("调用工具", joined)
        self.assertIn("完成", joined)


class TestRagBranchDegrades(ProcessorTestCase):
    def test_dangerous_tool_without_retriever_does_not_crash(self):
        """retriever 为 None 时，RAG 分支的异常必须被吞掉，不能中断对话"""
        llm = fakes.FakeToolCallingLLM([
            fakes.FakeHttpResponse.with_tool("execute_processing", {"algorithm": "native:buffer"}),
            "缓冲区已生成",
        ])
        _h, _m, proc, _l = self.new_processor(llm, {"execute_processing": {"executed": True}})
        text, workflow = proc.agent_chat("做个缓冲区")
        self.assertEqual(text, "缓冲区已生成")
        self.assertEqual(workflow, "withTool")

    def test_cookbook_archive_failure_is_swallowed(self):
        llm = fakes.FakeToolCallingLLM(["完成"])
        _h, _m, proc, _l = self.new_processor(llm)
        text, _wf = proc.agent_chat("问题")
        self.assertEqual(text, "完成")


if __name__ == "__main__":
    unittest.main()
