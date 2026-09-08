# -*- coding: utf-8 -*-
"""测试替身：假 LLM、假 DataLoader。

这些替身只实现 `processor.Processor` 真正调用到的接口（已核对 processor.py）：
    dataloader.fetch_llm_info(llm_id)            -> (model_name, endpoint, api_key)
    dataloader.select_interaction(conversation_id) -> 行元组列表
    dataloader.insert_interaction(row, conversation_id) -> interaction_id
    llm.bind_tools(TOOL_DEFINITIONS)             -> 绑定后的 llm
    llm.invoke(messages)                         -> 带 .content / .tool_calls 的响应
"""

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

# interaction 表列顺序（与 utils.py 的 colname_map 保持一致）
INTERACTION_COLUMNS = [
    "ID", "conversationID", "promptID", "requestText", "contextText",
    "requestTime", "typeMessage", "responseText", "responseTime",
    "workflow", "executionLog",
]


def make_interaction_row(ID="i1", conversationID="c1", promptID="p1",  # noqa: N803
                         requestText="hi", contextText="", requestTime="06 05 2026 10:00:00",
                         typeMessage="input", responseText="", responseTime="",
                         workflow="empty", executionLog=""):
    """按 interaction 列顺序造一行，便于测试历史重建"""
    return (ID, conversationID, promptID, requestText, contextText, requestTime,
            typeMessage, responseText, responseTime, workflow, executionLog)


class FakeHttpResponse:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    @classmethod
    def with_tool(cls, name, args=None, call_id="call_1"):
        return cls(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


class FakeHttpClient:
    """processor._close_http_client / _http_client_closed 会读 is_closed"""

    def __init__(self):
        self.is_closed = False

    def close(self):
        self.is_closed = True


class FakeToolCallingLLM:
    """按脚本依次返回响应，并记录每次 invoke 收到的 messages

    script 元素可以是 FakeHttpResponse，也可以是普通字符串（视为最终文本）。
    """

    def __init__(self, script=None, bind_error=None):
        self.script = list(script or [])
        self.bind_error = bind_error
        self.calls = []          # 每次 invoke 的 messages 快照
        self.bound_tools = None
        self.invoke_count = 0
        self._http_client = FakeHttpClient()

    def bind_tools(self, tools, **kwargs):
        if self.bind_error is not None:
            raise self.bind_error
        self.bound_tools = tools
        return self

    def invoke(self, messages, **kwargs):
        self.calls.append(list(messages))
        self.invoke_count += 1
        if not self.script:
            raise AssertionError(
                "FakeToolCallingLLM 脚本已耗尽（第 %d 次 invoke）" % self.invoke_count)
        item = self.script.pop(0)
        if isinstance(item, str):
            return FakeHttpResponse(content=item)
        return item

    # ── 断言辅助 ──
    def contents_of(self, call_index):
        """第 call_index 次 invoke 时收到的消息 (类型名, content) 列表"""
        return [(type(m).__name__, getattr(m, "content", None)) for m in self.calls[call_index]]

    def find_message(self, predicate, call_index=None):
        """在收到的所有 messages 中查找满足 predicate 的消息"""
        calls = self.calls if call_index is None else [self.calls[call_index]]
        for messages in calls:
            for msg in messages:
                if predicate(msg):
                    return msg
        return None


class FakeDataloader:
    """内存版 DataLoader，只实现 processor 用到的三个方法"""

    def __init__(self, llm_info=("glm-4", "https://open.bigmodel.cn/api/paas/v4/", "sk-test"),
                 history=None):
        self.llm_info = llm_info
        self.history_rows = list(history or [])
        self.fetch_llm_info_calls = []
        self.select_interaction_calls = []
        self.inserted = []
        self._seq = 0

    def fetch_llm_info(self, llm_id):
        self.fetch_llm_info_calls.append(llm_id)
        return self.llm_info

    def select_interaction(self, conversation_id, columns=None):
        self.select_interaction_calls.append(conversation_id)
        return list(self.history_rows)

    def insert_interaction(self, interaction_row, conversation_id):
        self._seq += 1
        interaction_id = "%s%d" % (conversation_id, self._seq)
        self.inserted.append({
            "id": interaction_id,
            "conversation_id": conversation_id,
            "row": interaction_row,
        })
        return interaction_id

    # ── 断言辅助 ──
    @property
    def last_row(self):
        return self.inserted[-1]["row"] if self.inserted else None


def interaction_dict(row):
    """把 interaction 行元组转成字典，便于断言"""
    return dict(zip(INTERACTION_COLUMNS, row))


def tool_result_ok(payload=None):
    return {"executed": True, "result": payload if payload is not None else {}}


def tool_result_error(message):
    return {"executed": False, "error": message}
