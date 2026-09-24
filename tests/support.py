# -*- coding: utf-8 -*-
"""测试基础设施：路径引导 + 无 QGIS / 无网络 / 无 langchain 环境下的模块替身。

约定：本文件提供的都是**最小可用替身**，只实现被测代码真正用到的接口，
不做全量模拟。真实 QGIS / langchain 存在时一律优先使用真实模块，
保证同一套用例在开发者真机和 CI 裸环境都能跑。
"""

import importlib
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)          # .../qgis_agent
PARENT_DIR = os.path.dirname(PROJECT_ROOT)         # 需加入 sys.path 才能 import qgis_agent
PACKAGE_NAME = os.path.basename(PROJECT_ROOT)

_STUB_MARK = "_is_qgis_agent_test_stub"


def ensure_project_path():
    """把项目父目录（必要时还有项目目录本身）加入 sys.path。

    ⚠️ 顺序很关键：**父目录必须排在项目目录之前**。
    项目目录里有 `qgis_agent.py`（插件入口文件），若项目目录优先，
    `import qgis_agent` 会命中这个**文件**而不是同名的**包目录**，
    于是包内相对导入（`from .utils import ...`）全部失败，报
    `attempted relative import with no known parent package`。
    工具类模块（config / utils 等）仍可从项目目录按顶层名导入。
    """
    for path in (PARENT_DIR, PROJECT_ROOT):
        while path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, PARENT_DIR)


def has_module(name):
    """真实环境中能否 import 该模块（不含已注入的 stub）"""
    mod = sys.modules.get(name)
    if mod is not None and getattr(mod, _STUB_MARK, False):
        return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def import_mod(name):
    """优先以包内模块方式导入（qgis_agent.utils），失败则退化为顶层模块导入"""
    ensure_project_path()
    if PACKAGE_NAME == "qgis_agent":
        try:
            return importlib.import_module("qgis_agent." + name)
        except ImportError:
            pass
    return importlib.import_module(name)


# ────────────────────────────────────────────────────────────
# Qt / QObject 最小替身
# ────────────────────────────────────────────────────────────

class Signal:
    """pyqtSignal 的替身：记录每次 emit 的参数，可选转发给 connect 的槽"""

    def __init__(self, *arg_types):
        self.arg_types = arg_types
        self.emitted = []
        self.slots = []
        # 由 _SignalDescriptor 在首次访问时填入「信号属于哪个 QObject」，
        # 供 QObject.sender() 在槽函数里反查发送者（对齐真实 Qt 语义）。
        self.owner = None

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot=None):
        self.slots.clear()

    def emit(self, *args):
        self.emitted.append(args)
        if self.owner is not None:
            QObject._sender_stack.append(self.owner)
        try:
            for slot in list(self.slots):
                slot(*args)
        finally:
            if self.owner is not None:
                QObject._sender_stack.pop()

    @property
    def count(self):
        return len(self.emitted)

    def last(self):
        return self.emitted[-1] if self.emitted else None


class _SignalDescriptor:
    """让 pyqtSignal(...) 能像真实 Qt 一样作为类属性使用，且每个实例独享一份 Signal"""

    def __init__(self, *arg_types):
        self.arg_types = arg_types
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        signal = obj.__dict__.get(self.name)
        if signal is None:
            signal = Signal(*self.arg_types)
            signal.owner = obj
            setattr(obj, self.name, signal)
        return signal


class QObject:
    #: emit 期间的发送者栈，供 QObject.sender() 使用（对齐 Qt 的 sender() 语义）
    _sender_stack = []

    def __init__(self, *args, **kwargs):
        self._children = []

    def sender(self):
        """返回当前正在执行的槽所对应的信号发送者（不在槽内时返回 None）。"""
        return self._sender_stack[-1] if self._sender_stack else None

    def deleteLater(self):
        pass

    def blockSignals(self, flag):  # noqa: N802 - 保持 Qt 命名
        return False


class QRunnable:
    def run(self):
        raise NotImplementedError


class QThreadPool:
    def __init__(self, *args, **kwargs):
        self.tasks = []
        self.expiry_timeout = None

    def setExpiryTimeout(self, ms):  # noqa: N802
        self.expiry_timeout = ms

    def start(self, runnable):
        self.tasks.append(runnable)

    def clear(self):
        self.tasks.clear()

    def waitForDone(self, ms=-1):  # noqa: N802
        return True


class _AutoModule(types.ModuleType):
    """未知属性按需返回 MagicMock，避免为用不到的 QGIS 类逐个写替身"""

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        value = MagicMock(name="%s.%s" % (self.__name__, name))
        setattr(self, name, value)
        return value


# ────────────────────────────────────────────────────────────
# qgis 替身
# ────────────────────────────────────────────────────────────

_QGIS_SUBMODULES = (
    "qgis.core",
    "qgis.gui",
    "qgis.utils",
    "qgis.analysis",
    "qgis.PyQt",
    "qgis.PyQt.QtCore",
    "qgis.PyQt.QtGui",
    "qgis.PyQt.QtWidgets",
)


class _FakeQgis:
    """qgis.core.Qgis 的最小替身。QGIS_VERSION 必须是真实字符串，
    否则 utils.get_qgis_version() 会拿到 MagicMock 并拼出空串。"""

    QGIS_VERSION = "3.40.5-Bratislava"
    QGIS_RELEASE_NAME = "Bratislava"

    class MessageLevel:
        Info = 0
        Warning = 1
        Critical = 2


def install_qgis_stub():
    """注入 qgis.* 替身。返回 True 表示注入成功，False 表示环境里已有真实 QGIS"""
    ensure_project_path()
    existing = sys.modules.get("qgis")
    if existing is not None:
        return bool(getattr(existing, _STUB_MARK, False))
    if has_module("qgis"):
        return False

    qgis = _AutoModule("qgis")
    setattr(qgis, _STUB_MARK, True)
    sys.modules["qgis"] = qgis

    for dotted in _QGIS_SUBMODULES:
        mod = _AutoModule(dotted)
        setattr(mod, _STUB_MARK, True)
        sys.modules[dotted] = mod

    qtcore = sys.modules["qgis.PyQt.QtCore"]
    qtcore.QObject = QObject
    qtcore.pyqtSignal = _SignalDescriptor
    qtcore.QRunnable = QRunnable
    qtcore.QThreadPool = QThreadPool
    sys.modules["qgis.core"].Qgis = _FakeQgis

    # 组装包层级，保证 `from qgis.PyQt.QtCore import QObject` 走 import 系统时不会落空
    qgis.core = sys.modules["qgis.core"]
    qgis.gui = sys.modules["qgis.gui"]
    qgis.utils = sys.modules["qgis.utils"]
    qgis.analysis = sys.modules["qgis.analysis"]
    qgis.PyQt = sys.modules["qgis.PyQt"]
    pyqt = qgis.PyQt
    pyqt.QtCore = qtcore
    pyqt.QtGui = sys.modules["qgis.PyQt.QtGui"]
    pyqt.QtWidgets = sys.modules["qgis.PyQt.QtWidgets"]
    return True


# ────────────────────────────────────────────────────────────
# langchain_core 替身（只实现消息数据类与 StrOutputParser）
# ────────────────────────────────────────────────────────────

class BaseMessage:
    """对应 langchain_core.messages.BaseMessage 的最小数据载体"""

    def __init__(self, content="", **kwargs):
        self.content = content
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return "%s(%r)" % (type(self).__name__, self.content)

    def __eq__(self, other):
        return type(self) is type(other) and self.content == getattr(other, "content", None)

    def __hash__(self):
        return hash((type(self).__name__, self.content))


class SystemMessage(BaseMessage):
    pass


class HumanMessage(BaseMessage):
    pass


class AIMessage(BaseMessage):
    def __init__(self, content="", **kwargs):
        kwargs.setdefault("tool_calls", [])
        super().__init__(content, **kwargs)


class ToolMessage(BaseMessage):
    def __init__(self, content="", tool_call_id=None, **kwargs):
        super().__init__(content, **kwargs)
        self.tool_call_id = tool_call_id


class StrOutputParser:
    """只实现 parse / invoke，够 processor 构造使用"""

    def parse(self, text):
        return getattr(text, "content", text)

    def invoke(self, text, **kwargs):
        return self.parse(text)


class _ChatModelStub:
    """langchain_openai.ChatOpenAI / langchain_deepseek.ChatDeepSeek 的最小替身。

    只保证「能被构造、能被 bind_tools / invoke 调用」，返回空串；
    不模拟任何真实推理行为。"""

    def __init__(self, *args, **kwargs):
        self._kwargs = kwargs
        self._tools = None

    def bind_tools(self, tools, **kwargs):
        self._tools = tools
        return self

    def with_config(self, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        return ""


def install_langchain_stub():
    """注入 langchain_core 替身。返回 True 表示注入成功"""
    ensure_project_path()
    if "langchain_core" in sys.modules:
        return bool(getattr(sys.modules["langchain_core"], _STUB_MARK, False))
    if has_module("langchain_core"):
        return False

    lc = types.ModuleType("langchain_core")
    setattr(lc, _STUB_MARK, True)
    messages = types.ModuleType("langchain_core.messages")
    setattr(messages, _STUB_MARK, True)
    parsers = types.ModuleType("langchain_core.output_parsers")
    setattr(parsers, _STUB_MARK, True)

    for cls in (BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage):
        setattr(messages, cls.__name__, cls)
    parsers.StrOutputParser = StrOutputParser

    lc.messages = messages
    lc.output_parsers = parsers
    sys.modules["langchain_core"] = lc
    sys.modules["langchain_core.messages"] = messages
    sys.modules["langchain_core.output_parsers"] = parsers

    # 同时桩住 processor / llm_providers 模块级依赖的模型包
    _openai = types.ModuleType("langchain_openai")
    setattr(_openai, _STUB_MARK, True)
    _openai.ChatOpenAI = _ChatModelStub
    sys.modules["langchain_openai"] = _openai

    _deepseek = types.ModuleType("langchain_deepseek")
    setattr(_deepseek, _STUB_MARK, True)
    _deepseek.ChatDeepSeek = _ChatModelStub
    sys.modules["langchain_deepseek"] = _deepseek
    return True


# ────────────────────────────────────────────────────────────
# 通用模块替身
# ────────────────────────────────────────────────────────────

class _Raising:
    """构造即抛异常的类，用来模拟"可选依赖缺失"时的降级分支"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("测试替身：该组件在本用例中不可用")


def install_module_stub(dotted_name, **attrs):
    """把任意模块替换成替身；返回被替换的模块对象"""
    mod = types.ModuleType(dotted_name)
    setattr(mod, _STUB_MARK, True)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[dotted_name] = mod
    parent_name, _, child = dotted_name.rpartition(".")
    if parent_name:
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, mod)
    return mod


def install_httpx_stub():
    """注入 httpx 替身（裸环境没有该库，而 llm_providers 在**模块级**继承了它）。

    必须是「真类」而不是 MagicMock：llm_providers 里有
        class _CurlTransport(httpx.HTTPTransport): ...
    以 MagicMock 为基类会在类创建时就抛错，导致整个包导不进来。

    返回 True 表示注入成功，False 表示环境里已有真实 httpx。
    """
    if has_module("httpx"):
        return False

    class _Transport:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def handle_request(self, request):  # pragma: no cover - 测试不发真请求
            raise RuntimeError("测试替身：httpx 在本用例中不可用")

    class _Client(_Transport):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.is_closed = False

        def close(self):
            self.is_closed = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    mod = install_module_stub(
        "httpx",
        HTTPTransport=_Transport,
        AsyncHTTPTransport=_Transport,
        Client=_Client,
        AsyncClient=_Client,
        Headers=dict,
        Proxy=dict,
        Response=lambda *a, **kw: None,
        HTTPError=RuntimeError,
        TransportError=RuntimeError,
        TimeoutException=RuntimeError,
    )
    return mod is not None


def install_runtime_stubs():
    """一次装齐导入被测运行时模块所需的全部替身（顺序无关，幂等）。"""
    install_qgis_stub()
    install_langchain_stub()
    install_httpx_stub()


def temp_home():
    """返回一个可直接赋给 os.environ['HOME'] 的临时目录路径（调用方负责创建）"""
    import tempfile
    return tempfile.mkdtemp(prefix="qgis_agent_home_")
