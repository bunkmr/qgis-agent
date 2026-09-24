# -*- coding: utf-8 -*-
"""会话收尾信号 / processor 生命周期回归守卫。

对应用户报障（本轮）：
  1. 「我发送的文本在执行完成后看不到了」
  2. `TypeError: 'method' object is not connected`
     （conversation.py 的 `self.processor.response_ready.disconnect(...)`）

第 2 条的真因不是「没加 try 那么简单」：QGISAgent 在用户切换模型 / 温度时会
**整体替换 processor**（qgis_agent.py 里 `self.live_conversation.processor =
self._Processor(...)`），而旧 worker 的信号连接仍留在**旧对象**上。旧 worker
收尾时触发旧对象的信号，回调里 `self.processor` 却已是新对象 —— 拿新对象去
disconnect 一个从未连接过的槽，就是用户看到的那个 TypeError；更糟的是它还会
把新对象上刚建立的连接误断开，导致新一轮的回复永远渲染不出来。

第 1 条的真因在 dock 层（`txHistory.append` 的内容没同步进 `_chat_html` 缓冲，
会被 `_set_chat_html` 的整体重写抹掉）。dock 层需要真实 Qt，由真机验收脚本
覆盖（见技能 `qgis-plugin-headless-verify`）；本文件覆盖会话侧契约。

⚠️ 测试方法学要点：PyQt 会把**槽函数里的异常交给 sys.excepthook 打印**，
emit 调用处不会抛（用户在 QGIS 控制台看到一串 traceback，而程序不崩、界面
僵在「发送中」）。所以「没报错」不能靠 try/except 判断 —— 必须拦 excepthook，
否则断言是假绿。
"""
import sys
import unittest

try:  # 既支持以包方式导入，也支持 unittest discover 顶层导入
    from . import support
except ImportError:
    import support

support.install_qgis_stub()
support.install_langchain_stub()

# conversation → processor → llm_providers 在**模块级**继承了 httpx.HTTPTransport，
# 所以裸环境（无 httpx）下必须先临时注入替身才能导入。
# 导入完成后立刻归还现场：否则这个替身会泄漏给同进程的其它测试
# （实测会把 test_endpoint_diagnostics 的 HTTP 端到端用例打挂）。
_saved_httpx = sys.modules.pop("httpx", None)
support.install_httpx_stub()
try:
    _conversation = support.import_mod("conversation")
finally:
    sys.modules.pop("httpx", None)
    if _saved_httpx is not None:
        sys.modules["httpx"] = _saved_httpx

Conversation = _conversation.Conversation
QObject = _conversation.QObject


class FakeProcessor(QObject):
    """processor 的最小替身：只保留会话侧真正依赖的信号与入口。"""

    response_ready = _conversation.pyqtSignal(str, str, str, str)
    error_signal = _conversation.pyqtSignal(str)
    reflection_ready = _conversation.pyqtSignal(str, str, str, str)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.shutdown_called = False

    def async_response(self, message, response_type):
        self.calls.append((message, response_type))

    def async_reflect(self, *args, **kwargs):
        self.calls.append(("reflect",))

    def cancel(self):
        self.calls.append(("cancel",))

    def shutdown(self):
        self.shutdown_called = True


def new_conversation():
    """绕开重量级 __init__（要 dataloader / 真实 Processor），只装配被测状态。"""
    conv = Conversation.__new__(Conversation)
    QObject.__init__(conv)
    conv.processor = FakeProcessor()
    conv.llm_finished = True
    conv._response_handled = True
    conv.modified = ""
    conv.meta_info = {}
    conv._pending_request = None
    conv._pending_response_type = None
    return conv


class SlotErrorCatcher(unittest.TestCase):
    """基类：提供「捕获槽内被吞掉的异常」的能力。"""

    def setUp(self):
        self._slot_errors = []
        self._orig_excepthook = sys.excepthook

        def _hook(exc_type, exc_value, exc_tb):
            self._slot_errors.append(
                "%s: %s" % (getattr(exc_type, "__name__", exc_type), exc_value)
            )

        sys.excepthook = _hook

    def tearDown(self):
        sys.excepthook = self._orig_excepthook

    def fire(self, emit_callable, *args):
        """触发信号；返回本次触发的槽内异常列表（true 表示有东西炸了）。"""
        mark = len(self._slot_errors)
        try:
            emit_callable(*args)
        except Exception as exc:  # noqa: BLE001 - 裸环境下 stub 的 emit 会直接传播
            self._slot_errors.append(
                "直接抛出 %s: %s" % (type(exc).__name__, exc)
            )
        return self._slot_errors[mark:]

    def assertNoSlotError(self, errs, msg=""):
        self.assertEqual(list(errs), [], msg or "槽函数内出现异常")


class TestResponseSignalContract(SlotErrorCatcher):
    """一轮请求的信号连接 / 收尾 / 断开契约。"""

    def test_single_round_emits_once(self):
        conv = new_conversation()
        got = []
        conv.llm_response.connect(lambda r, w, m: got.append(r))
        conv._update_llm_response("hello", "chat")

        errs = self.fire(conv.processor.response_ready.emit, "hello", "chat", "回答", "wf")
        self.assertNoSlotError(errs)
        self.assertEqual(got, ["回答"])

    def test_error_then_response_does_not_raise(self):
        """★ 用户现场：工作线程先 emit error 再 emit finished。

        旧代码在第二个回调里 disconnect 已断开的槽 →
        TypeError: 'method' object is not connected，后面的 emit 全不执行。
        """
        conv = new_conversation()
        got = []
        conv.llm_response.connect(lambda r, w, m: got.append("RESP:" + r))
        conv.llm_interrupted.connect(lambda e: got.append("ERR:" + e))
        conv._update_llm_response("hello", "chat")

        errs = self.fire(conv.processor.error_signal.emit, "HTTP 500 · boom")
        errs += self.fire(conv.processor.response_ready.emit,
                          "hello", "chat", "迟到的回答", "wf")

        self.assertNoSlotError(errs)
        self.assertEqual(got, ["ERR:HTTP 500 · boom"], "一轮只应收尾一次")
        self.assertTrue(conv._response_handled)
        self.assertTrue(conv.llm_finished, "收尾后必须复位，否则界面卡在发送中")

    def test_response_then_error_does_not_raise(self):
        """反序双到同样只能收尾一次。"""
        conv = new_conversation()
        got = []
        conv.llm_response.connect(lambda r, w, m: got.append("RESP:" + r))
        conv.llm_interrupted.connect(lambda e: got.append("ERR:" + e))
        conv._update_llm_response("hello", "chat")

        errs = self.fire(conv.processor.response_ready.emit, "hello", "chat", "回答", "wf")
        errs += self.fire(conv.processor.error_signal.emit, "迟到的错误")

        self.assertNoSlotError(errs)
        self.assertEqual(got, ["RESP:回答"])

    def test_repeated_update_does_not_accumulate_connections(self):
        """连续两次 _update_llm_response 不得把同一个槽连两遍。"""
        conv = new_conversation()
        conv._update_llm_response("第一条", "chat")
        conv._update_llm_response("第二条", "chat")
        self.assertEqual(conv.processor.response_ready.count, 0,
                         "count 记录的是 emit 次数，不应被影响")
        self.assertEqual(len(conv.processor.response_ready.slots), 1,
                         "同一个槽只应连接一次")

        got = []
        conv.llm_response.connect(lambda r, w, m: got.append(r))
        errs = self.fire(conv.processor.response_ready.emit, "第二条", "chat", "回答", "wf")
        self.assertNoSlotError(errs)
        self.assertEqual(len(got), 1)

    def test_detach_is_idempotent(self):
        conv = new_conversation()
        conv._update_llm_response("hello", "chat")
        conv._detach_processor_signals()
        conv._detach_processor_signals()  # 再断一次不得抛

    def test_reflection_signal_is_guarded(self):
        conv = new_conversation()
        got = []
        conv.llm_reflection.connect(lambda r, w, m: got.append(r))
        conv.update_reflection("日志", "code()", "code")

        errs = self.fire(conv.processor.reflection_ready.emit, "日志", "code", "反思结果", "wf")
        errs += self.fire(conv.processor.reflection_ready.emit, "日志", "code", "重复的反思", "wf")
        self.assertNoSlotError(errs)
        self.assertEqual(got, ["反思结果"])


class TestProcessorReplacement(SlotErrorCatcher):
    """processor 被整体替换（切换模型 / 温度）时的隔离契约。"""

    def test_stale_processor_callback_is_ignored(self):
        """★ 用户现场根因：旧 processor 的迟到回调不得报错、不得渲染过期结果、
        更不得把新 processor 上的连接误断开。"""
        conv = new_conversation()
        got = []
        conv.llm_response.connect(lambda r, w, m: got.append("RESP:" + r))
        conv.llm_interrupted.connect(lambda e: got.append("ERR:" + e))

        conv._update_llm_response("用模型A", "chat")
        stale = conv.processor

        # 切换模型：会话先释放旧连接，QGISAgent 再整体换掉 processor
        conv.release_processor()
        self.assertTrue(conv._response_handled, "release 后本轮必须作废")
        self.assertTrue(conv.llm_finished)
        conv.processor = FakeProcessor()
        conv._update_llm_response("用模型B", "chat")

        errs = self.fire(stale.response_ready.emit,
                         "用模型A", "chat", "旧模型的回答", "wf")
        self.assertNoSlotError(errs)
        self.assertEqual(got, [], "过期结果不能被当成本轮回复渲染")
        self.assertEqual(len(conv.processor.response_ready.slots), 1,
                         "新 processor 上的连接不能被误断开")

        errs = self.fire(conv.processor.response_ready.emit,
                         "用模型B", "chat", "新模型的回答", "wf")
        self.assertNoSlotError(errs)
        self.assertEqual(got, ["RESP:新模型的回答"])

    def test_stale_error_signal_is_ignored(self):
        conv = new_conversation()
        got = []
        conv.llm_interrupted.connect(lambda e: got.append("ERR:" + e))
        conv._update_llm_response("用模型A", "chat")
        stale = conv.processor
        conv.release_processor()
        conv.processor = FakeProcessor()
        conv._update_llm_response("用模型B", "chat")

        errs = self.fire(stale.error_signal.emit, "旧模型的错误")
        self.assertNoSlotError(errs)
        self.assertEqual(got, [])

    def test_replacement_without_release_is_still_safe(self):
        """即使调用方忘了先 release（直接赋值），旧回调也不能炸。"""
        conv = new_conversation()
        conv._update_llm_response("A", "chat")
        stale = conv.processor
        conv.processor = FakeProcessor()  # 粗暴替换，不通知会话

        errs = self.fire(stale.response_ready.emit, "A", "chat", "旧的", "wf")
        self.assertNoSlotError(errs)

    def test_signal_sender_processor_resolution(self):
        """_signal_sender_processor 在槽内取发送者，槽外回退到当前 processor。"""
        conv = new_conversation()
        seen = []

        def probe(*_args):
            seen.append(conv._signal_sender_processor())

        conv.processor.response_ready.connect(probe)
        conv.processor.response_ready.emit("x", "chat", "y", "z")
        self.assertIs(seen[0], conv.processor, "槽内应解析为信号发送者")
        self.assertIs(conv._signal_sender_processor(), conv.processor,
                      "槽外应回退为当前 processor")


class TestStopSemantics(SlotErrorCatcher):
    """停止按钮：stop() 只发中断请求，收尾由回调负责。"""

    def test_stop_does_not_reset_llm_finished(self):
        """stop() 若抢先置 llm_finished=True，会同时造成两个真事故：
        界面放开第二次发送（信号连接叠加）与「停止中…」中间态失效。"""
        conv = new_conversation()
        conv._update_llm_response("原始请求", "chat")
        self.assertFalse(conv.llm_finished)

        conv.stop()
        self.assertFalse(conv.llm_finished, "停止是异步的，状态要等收尾回调复位")
        self.assertIn(("cancel",), conv.processor.calls)

    def test_stop_then_worker_finishes_resets_state(self):
        conv = new_conversation()
        got = []
        conv.llm_interrupted.connect(lambda e: got.append(e))
        conv._update_llm_response("原始请求", "chat")
        conv.stop()

        errs = self.fire(conv.processor.error_signal.emit, "已取消")
        self.assertNoSlotError(errs)
        self.assertTrue(conv.llm_finished)
        self.assertEqual(got, ["已取消"], "停止只应产生一次中断事件")

    def test_can_send_again_after_stop_finishes(self):
        conv = new_conversation()
        got = []
        conv.llm_response.connect(lambda r, w, m: got.append(r))
        conv.llm_interrupted.connect(lambda e: got.append("ERR:" + e))

        conv._update_llm_response("原始请求", "chat")
        conv.stop()
        self.fire(conv.processor.error_signal.emit, "已取消")

        conv._update_llm_response("新消息", "chat")
        self.assertFalse(conv.llm_finished)
        self.assertEqual(len(conv.processor.response_ready.slots), 1,
                         "停止收尾后重新发送不得让槽连接叠加")
        errs = self.fire(conv.processor.response_ready.emit, "新消息", "chat", "新回答", "wf")
        self.assertNoSlotError(errs)
        self.assertEqual(got, ["ERR:已取消", "新回答"])


if __name__ == "__main__":
    unittest.main()
