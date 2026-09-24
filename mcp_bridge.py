# -*- coding: utf-8 -*-
"""QGIS Agent MCP 桥接服务（插件内，供外部 Agent 通过 MCP 驱动 QGIS）。

角色定位（对照 GitHub 上主流 QGIS MCP 项目的双组件架构）：
- 本文件 = 宿主侧「socket 服务」，跑在 QGIS 进程里，只监听回环地址；
- `mcp_server/qgis_agent_mcp_server.py` = 外部「MCP Server」，实现 MCP 协议，
  由 Claude Desktop / Cursor 等客户端以 stdio 方式拉起，再通过本服务驱动 QGIS。

与其它项目的关键差异（也是本实现刻意保守的地方）：
1. **只监听 127.0.0.1**，不接受绑定到其它地址，避免 QGIS 被局域网内他人驱动；
2. **强制要求访问令牌**，没有令牌的请求一律拒绝，不做「默认无认证」；
3. **危险工具默认不暴露**（`allow_dangerous=False`），即便放开，
   执行时仍会走既有的三档授权弹窗，由用户在 QGIS 界面上确认；
4. **复用 `qgis_tools` 单一真源**：工具清单与执行入口都来自 `qgis_tools`，
   本模块不复制任何工具定义，避免两边漂移；
5. **默认不自动监听**，需用户在设置页显式开启。

线程模型：socket 收发在工作线程，真正的 QGIS 调用经 `qgis_tools.call_tool()`
自动调度回主线程（`_MainThreadBridge`），界面不会被长任务卡死。
"""

import json
import os
import re
import shutil
import socket
# 仅用于探测候选解释器：全部以列表传参、不经 shell，且 argv 来自白名单。
import subprocess  # nosec B404
import sys
import threading

from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal

from .mcp_protocol import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_LINE_BYTES,
    MCPProtocolHandler,
    clear_session_file,
    read_session_file,
    write_session_file,
)
import contextlib

# 并发连接上限：MCP 客户端通常只用 1 条，留出冗余即可，避免连接耗尽
MAX_CONNECTIONS = 4
# accept 循环的空闲等待（秒），用于在 stop() 时及时退出阻塞
CONN_TIMEOUT = 0.5
# 建立连接后等待首个请求的时限（秒）
ACK_TIMEOUT = 30.0
# 首请求之后允许的连接空闲时长（秒）。
# 不能设为不限时：4 条空闲连接就能永久占满名额，把合法客户端挡在门外。
IDLE_TIMEOUT = 600.0
# 单次 recv 的块大小
RECV_CHUNK = 65536

# 除 qgis_tools._DANGEROUS_TOOLS 之外，本桥接层额外视为「需显式放行」的工具：
# run_skill 会执行用户技能目录下的 .py（skills/skill_manager.load_user_skills），
# 等价于执行用户提供的代码，因此默认不对 MCP 外部 Agent 暴露。
# 注意：这只影响 MCP 这条通道，插件内置 Agent 的行为不变。
PRIVILEGED_EXTRA_TOOLS = ("run_skill",)

# 实在找不到任何可验证的 Python 时，配置里退化成这个裸命令名，由客户端从 PATH 解析。
# Windows 上解释器叫 python（或 py），没有 python3 这个约定名。
PYTHON_PATH_COMMAND = "python" if os.name == "nt" else "python3"

# 探测候选解释器时的单次超时（秒）。探测只做一次并缓存，正常机器上是几十毫秒。
PYTHON_PROBE_TIMEOUT = 5.0
# 最多「真的执行」几个候选，避免在异常机器上把 GUI 线程卡住。
# 注意上限计的是**实际发起探测的次数**，不是候选列表下标 ——
# 绝大多数候选（各种前缀下猜出来的路径）根本不存在，跳过它们是零成本的；
# 按下标截断会把 PATH 上的可用解释器一起截掉（实测踩过）。
PYTHON_PROBE_LIMIT = 8


def _default_token():
    """生成 32 字节随机十六进制令牌。"""
    return os.urandom(32).hex()


def _looks_like_python(path):
    """按文件名判断是不是 Python 解释器。

    只看文件名是刻意的：这是唯一在三个平台都成立、且不会误判的廉价判据
    （Windows ``python.exe`` / ``pythonw.exe``、Linux ``python3.12``、
    macOS 独立安装 ``python3`` 全部命中；而 ``QGIS``、``QGIS-final-4_2_1``
    这类 GUI 主程序一律不命中）。
    """
    # 不能用 os.path.basename：它只认当前平台的分隔符，在 POSIX 上传入
    # r"C:\OSGeo4W\bin\python.exe" 会原样返回整串（判不出 python）。
    base = re.split(r"[\\/]", str(path or ""))[-1].lower()
    return base.startswith("python")


def _python_candidates(server_script=None, python_executable=None):
    """按优先级列出候选解释器路径（去重保序）。

    优先级：调用方显式指定 > sys.executable（仅当它确实像 python）>
    QGIS 前缀下的各种布局 > sys.base_prefix / sys.prefix 下的布局 >
    PATH 上的 python3 / python。

    ⚠️ 为什么不能无条件信 sys.executable：macOS 上 QGIS 的 Python 是嵌在
    app 里的，GUI 进程里 ``sys.executable`` 就是
    ``/Applications/QGIS.app/Contents/MacOS/QGIS``（GUI 主程序）。把它写进
    客户端配置的 ``command``，等于让 MCP 客户端去「启动一个 QGIS 界面」当
    stdio 服务 —— 它既不读 stdin、也不讲 JSON-RPC。Windows 上
    ``sys.executable`` 正好就是 python.exe，所以这个坑只在 macOS 暴露。
    """
    candidates = []

    def _add(path):
        path = str(path or "")
        # ⚠️ 一律只收「名字像 python」的候选：下面的探测会真的把它执行起来，
        # 放进一个 GUI 程序等于在用户屏幕上弹一个窗口。
        if path and _looks_like_python(path) and path not in candidates:
            candidates.append(path)

    _add(python_executable)
    _add(getattr(sys, "executable", "") or "")

    version = "python%d.%d" % tuple(sys.version_info[:2])
    relative = ("bin/python3", "bin/" + version, "bin/python",
                version, "python3", "python.exe", "bin/python.exe")

    prefixes = []
    with contextlib.suppress(Exception):
        from qgis.core import QgsApplication
        prefixes.append(QgsApplication.prefixPath())
    prefixes.append(getattr(sys, "base_prefix", "") or "")
    prefixes.append(getattr(sys, "prefix", "") or "")
    for prefix in prefixes:
        if not prefix:
            continue
        for rel in relative:
            _add(os.path.join(prefix, rel))

    for name in ("python3", "python"):
        with contextlib.suppress(Exception):
            _add(shutil.which(name))

    return candidates, server_script


def _clean_env():
    """去掉会干扰解释器启动的 Python 环境变量，模拟 MCP 客户端的启动环境。"""
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE"):
        env.pop(key, None)
    return env


def _try_run(argv, env=None):
    """跑一次探测命令，退出码为 0 且没有崩在解释器初始化上才算通过。"""
    try:
        # 参数以列表传入、不使用 shell；argv 全部来自探测白名单，无注入面。
        proc = subprocess.run(  # nosec B603
            argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PYTHON_PROBE_TIMEOUT, env=env,
        )
    except Exception:  # noqa: BLE001 —— 超时 / 权限 / 不是可执行文件等一律判不合格
        return False
    return proc.returncode == 0


def _probe_environment(candidate, script=None):
    """探测候选解释器，返回它**在哪种环境下**可用；不可用返回 ``None``。

    判据刻意不是「文件存在」—— 存在也可能跑不起来。实测 macOS 上
    ``QGIS.app/Contents/MacOS/python3.12`` 是存在的，但它是给 app 内部用的
    framework 解释器，裸跑缺 PYTHONHOME，直接报
    ``Could not find platform independent libraries <prefix>`` 并退出。

    所以这里**真的执行一次**：有服务脚本就跑它的 ``--help``（退出码 0 才算
    通过，顺带证明它还能 import 到脚本所需的标准库），没有脚本就退化成
    ``python -c "import sys"``。

    为什么要试两种环境、且**先干净后继承**：
    - MCP 客户端拉起子进程时用的是**它自己**的环境，所以「干净环境下能跑」
      才是真正相关的判据 —— 先按这个判；
    - 但 QGIS 自带的解释器（Windows OSGeo4W、Linux 发行版包）往往要靠进程里
      已有的 PYTHONPATH / PYTHONHOME 才能初始化，所以干净环境失败时再按继承
      环境兜一次，避免漏掉本来可用的解释器。

    返回 ``"inherited"`` 时调用方要提醒用户：这个选择只在 QGIS 进程的环境下
    成立，客户端可能拉不起来。
    """
    # 名字闸门（第二道，见 _python_candidates 的说明）：探测会真的执行候选，
    # 绝不能对「看起来不像解释器」的可执行文件动手。
    if not _looks_like_python(candidate):
        return None
    if not os.path.exists(candidate):
        return None
    if not os.access(candidate, os.X_OK):
        return None
    argv = ([candidate, script, "--help"] if script
            else [candidate, "-c", "import sys"])
    if _try_run(argv, _clean_env()):
        return "clean"
    if _try_run(argv, None):
        return "inherited"
    return None


def _can_run(candidate, script=None):
    """这个候选**真的**能当解释器用吗？（任一环境能跑即算能跑）"""
    return _probe_environment(candidate, script) is not None


# 探测结果缓存：(server_script, python_executable) -> (path, note)
_PYTHON_CACHE = {}


def clear_python_cache():
    """清掉解释器探测缓存（换 QGIS 前缀、装了新 Python 后调用）。"""
    _PYTHON_CACHE.clear()


def resolve_python_executable(server_script=None, python_executable=None):
    """挑一个「真能跑起 MCP Server」的 Python 解释器，返回 ``(路径, 说明)``。

    ``说明`` 为空表示不需要特殊处理（``command`` 就是当前进程的解释器）；
    非空时是给用户看的一句话，解释为什么配置里的 ``command`` 不是 QGIS 的路径。

    保底策略：任何一步出意外，都退回 ``sys.executable``（即改动前的行为），
    绝不抛异常 —— 生成一段配置失败不该让整个设置页崩掉。
    """
    fallback = (getattr(sys, "executable", "") or "python3")
    try:
        key = (str(server_script or ""), str(python_executable or ""))
        if key in _PYTHON_CACHE:
            return _PYTHON_CACHE[key]

        candidates, script = _python_candidates(server_script, python_executable)
        have_script = bool(script) and os.path.exists(script)
        probe_target = script if have_script else None

        # 猜出来的路径大多不存在，先按「存在」筛一遍，让探测上限只花在真候选上
        existing = [c for c in candidates
                    if c and os.path.exists(c)][:PYTHON_PROBE_LIMIT]

        # ⚠️ 两趟探测，且**干净环境那趟必须优先**：客户端是在自己的环境里拉起
        # command 的，所以「干净环境下能跑」才等同于「配置能用」。只在 QGIS
        # 进程环境里能跑的解释器（靠继承的 PYTHONPATH/PYTHONHOME 活着）只能当
        # 兜底 —— 拿它当首选，用户会看到客户端一启动就退出。
        result = None
        env_used = None
        for wanted, label in (("clean", "clean"), ("inherited", "inherited")):
            for candidate in existing:
                if _probe_environment(candidate, probe_target) == wanted:
                    result = candidate
                    env_used = label
                    break
            if result is not None:
                break

        if result is None:
            # 一个都跑不起来。此时**不要**回吐 sys.executable —— 在 macOS GUI 上
            # 它是 QGIS 的 GUI 主程序，写进配置只会换来一个「客户端一启动就弹
            # QGIS 窗口、然后握手超时」。改成一个裸命令名交给客户端从 PATH 解析，
            # 至少在装了 Python 的机器上是可用方向。
            if _looks_like_python(fallback):
                result = fallback
                note = ("⚠ 未能确认 %s 能跑起 MCP Server（电脑上没找到其它可用的 "
                        "Python）。若客户端连不上，请手动把 command 改成 Python 的"
                        "绝对路径。" % fallback)
            else:
                result = PYTHON_PATH_COMMAND
                note = ("⚠ 未在本机找到可用的 Python 解释器，配置里先写成 %r，"
                        "由客户端从 PATH 里解析。若仍是连不上，请把它改成 Python 的"
                        "绝对路径。" % PYTHON_PATH_COMMAND)
        elif _looks_like_python(fallback) and os.path.normcase(result) == os.path.normcase(fallback):
            note = ""
        elif not _looks_like_python(fallback):
            note = ("ℹ 当前进程的可执行文件（%s）不是 Python 解释器，"
                    "配置里的 command 已自动改用 %s。" % (fallback, result))
        else:
            note = ("ℹ 当前解释器（%s）跑不起来 MCP Server，"
                    "配置里的 command 已自动改用 %s。" % (fallback, result))

        if env_used == "inherited":
            note = ("⚠ 本机没有「干净环境下就能启动」的 Python，已选用 %s —— "
                    "它需要 QGIS 进程的环境变量才能初始化，MCP 客户端可能拉不起来。"
                    "若客户端报「意外退出」，请手动把 command 换成"
                    "一个独立安装的 Python 绝对路径。" % result)

        _PYTHON_CACHE[key] = (result, note)
        return result, note
    except Exception:  # noqa: BLE001
        return fallback, ""


def _safe_qgis_info():
    """采集少量 QGIS 状态信息，失败时返回空字典。"""
    info = {}
    with contextlib.suppress(Exception):
        from qgis.core import Qgis, QgsProject
        info["qgis_version"] = Qgis.QGIS_VERSION
        project = QgsProject.instance()
        info["project_path"] = project.fileName() or ""
        info["layer_count"] = len(project.mapLayers())
    return info


class _BridgeServerThread(QThread):
    """TCP 接收线程：accept 循环 + 每连接一个工作线程。

    不使用 QtNetwork，改用标准库 socket —— 逻辑更直白，也便于在裸 Python 下复用。
    """

    def __init__(self, host, port, handler, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = int(port)
        self._handler = handler
        self._server = None
        self._stop_flag = threading.Event()
        self._ready = threading.Event()
        self.bind_error = None
        self.actual_port = None
        self._conn_count = 0
        self._conn_lock = threading.Lock()

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self._host, self._port))
            server.listen(8)
            server.settimeout(CONN_TIMEOUT)
        except OSError as exc:
            self.bind_error = exc
            with contextlib.suppress(OSError):
                server.close()
            self._ready.set()
            return

        self._server = server
        self.actual_port = server.getsockname()[1]
        self._ready.set()

        while not self._stop_flag.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._conn_lock:
                if self._conn_count >= MAX_CONNECTIONS:
                    with contextlib.suppress(OSError):
                        conn.close()
                    continue
                self._conn_count += 1
            worker = threading.Thread(
                target=self._serve_connection, args=(conn,), daemon=True
            )
            worker.start()

        with contextlib.suppress(OSError):
            server.close()

    def _release_connection(self):
        with self._conn_lock:
            self._conn_count = max(0, self._conn_count - 1)

    @staticmethod
    def _reject_oversized(conn):
        """单行超限时回一条错误并（由调用方）断开，避免连接被无声关闭。"""
        payload = {"ok": False, "code": "bad_request",
                   "error": "单行请求超过 %d 字节上限，连接已关闭。" % MAX_LINE_BYTES}
        with contextlib.suppress(OSError):
            conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    def _dispatch(self, raw_line):
        """交给协议层处理；任何异常都转成结构化错误，绝不让工作线程崩掉。"""
        try:
            return self._handler.handle_line(raw_line)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "code": "internal_error",
                 "error": "服务内部错误: %s" % (exc,)},
                ensure_ascii=False,
            )

    def _serve_connection(self, conn):
        """分块读取 + 自行按换行切分。

        刻意不用 socket.makefile().readline()：readline 在收到换行符之前会把
        整个流缓冲进内存，对端只要持续发送不含换行的字节就能把 QGIS 进程撑爆
        （协议层的长度校验发生在 readline 返回之后，拦不住）。
        """
        buffer = b""
        try:
            conn.settimeout(ACK_TIMEOUT)
            while not self._stop_flag.is_set():
                try:
                    chunk = conn.recv(RECV_CHUNK)
                except socket.timeout:
                    break
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk

                while True:
                    newline_at = buffer.find(b"\n")
                    if newline_at < 0:
                        break
                    line, buffer = buffer[:newline_at], buffer[newline_at + 1:]
                    if len(line) > MAX_LINE_BYTES:
                        self._reject_oversized(conn)
                        return
                    response = self._dispatch(line)
                    if response is None:
                        continue
                    try:
                        conn.sendall((response + "\n").encode("utf-8"))
                    except OSError:
                        return

                # 迟迟没有换行符却已超上限：不能再缓冲下去
                if len(buffer) > MAX_LINE_BYTES:
                    self._reject_oversized(conn)
                    return

                # 首条请求处理完后放宽超时，允许长连接空闲（但有上限）
                with contextlib.suppress(OSError):
                    conn.settimeout(IDLE_TIMEOUT)
        finally:
            with contextlib.suppress(OSError):
                conn.close()
            self._release_connection()

    def stop(self):
        self._stop_flag.set()
        with contextlib.suppress(OSError):
            if self._server is not None:
                self._server.close()
        # 主动连一次自身，唤醒可能正阻塞在 accept 上的循环（部分平台 close 不解除阻塞）
        with contextlib.suppress(OSError):
            wake = socket.create_connection((self._host, self.actual_port or self._port), timeout=1)
            wake.close()

    def wait_bind(self, timeout=10.0):
        self._ready.wait(timeout)
        return self.bind_error is None and self._server is not None


class MCPBridge(QObject):
    """插件内 MCP 桥接服务的外观（单例）。

    用法（由 qgis_agent.py 调用）：
        bridge = MCPBridge.get()
        ok, msg = bridge.start(port=9876, token="…", allow_dangerous=False)
        bridge.stop()
    """

    statusChanged = pyqtSignal(str)

    _instance = None

    def __init__(self):
        super().__init__()
        self._thread = None
        self._handler = None
        self._port = None
        # 空占位：真实令牌在服务启动时由设置 / 会话文件注入，这里不是凭据值。
        # B105 是按变量名里的 token 字样匹配的，此处属误报。
        self._token = ""  # nosec B105
        self._allow_dangerous = False
        # 最近一次生成客户端配置时对解释器做的替换说明（空串 = 无需替换）。
        # 设置页把它附在「复制客户端配置」的弹窗里，免得用户看到 command
        # 不是 QGIS 的路径时以为哪里出错了。
        self.last_python_hint = ""

    # ── 单例 ──
    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = MCPBridge()
        return cls._instance

    # ── 状态 ──
    def is_running(self):
        return self._thread is not None and self._thread.isRunning()

    @property
    def port(self):
        return self._port

    @property
    def token(self):
        return self._token

    def status_text(self):
        if self.is_running():
            return "运行中 · 127.0.0.1:%d" % self._port
        return "未运行"

    # ── 工具来源（单一真源：qgis_tools）──
    @staticmethod
    def _tool_definitions():
        from .qgis_tools import TOOL_DEFINITIONS
        return TOOL_DEFINITIONS

    @staticmethod
    def _dangerous_tool_names():
        """MCP 视角下「需用户在设置页显式放行」的工具集合。

        单一真源仍是 qgis_tools，这里只做并集（叠加 run_skill 这类等价于
        执行用户代码的特权工具），**不修改插件内部的危险工具判定**，
        因此内置 Agent 的确认行为完全不变。
        """
        from .qgis_tools import _DANGEROUS_TOOLS
        return set(_DANGEROUS_TOOLS) | set(PRIVILEGED_EXTRA_TOOLS)

    @staticmethod
    def _invoke_tool(name, arguments):
        from .qgis_tools import call_tool
        return call_tool(name, arguments)

    # ── 生命周期 ──
    def start(self, port=DEFAULT_PORT, token=None, allow_dangerous=False):
        """启动服务。返回 (ok: bool, message: str)。"""
        if self.is_running():
            return True, "服务已在运行（127.0.0.1:%d）。" % self._port

        if token is None:
            token = self._token or read_session_file().get("token") or _default_token()
        if not token:
            return False, "访问令牌为空，已拒绝启动（本服务强制要求令牌）。"

        handler = MCPProtocolHandler(
            list_tools=self._tool_definitions,
            call_tool=self._invoke_tool,
            token=token,
            dangerous_tools=self._dangerous_tool_names(),
            allow_dangerous=allow_dangerous,
            info_provider=_safe_qgis_info,
        )

        thread = _BridgeServerThread(DEFAULT_HOST, port, handler, parent=None)
        thread.start()
        if not thread.wait_bind():
            err = thread.bind_error
            thread.stop()
            thread.wait(2000)
            if isinstance(err, OSError) and getattr(err, "errno", None) in (48, 98, 10048):
                return False, "端口 %d 已被占用，请换一个端口。" % port
            return False, "无法监听 127.0.0.1:%d（%s）。" % (port, err)

        self._thread = thread
        self._handler = handler
        self._port = thread.actual_port or port
        self._token = token
        self._allow_dangerous = bool(allow_dangerous)

        session_error = ""
        try:
            write_session_file(self._port, token, extra={"host": DEFAULT_HOST})
        except Exception as exc:  # noqa: BLE001 - 写不了会话文件不影响服务本身
            session_error = "（会话文件写入失败：%s，需手动填写端口与令牌）" % exc

        message = "MCP 服务已启动：127.0.0.1:%d%s" % (self._port, session_error)
        self.statusChanged.emit(message)
        return True, message

    def stop(self):
        """停止服务。返回 (ok: bool, message: str)。"""
        if not self.is_running():
            self._thread = None
            self._handler = None
            self._port = None
            clear_session_file()
            return True, "MCP 服务未在运行。"
        thread = self._thread
        try:
            thread.stop()
            if not thread.wait(5000):
                thread.terminate()
                thread.wait(1000)
        finally:
            self._thread = None
            self._handler = None
            self._port = None
            clear_session_file()
        message = "MCP 服务已停止。"
        self.statusChanged.emit(message)
        return True, message

    def apply_settings(self, token=None, allow_dangerous=None):
        """设置页改动后热更新令牌 / 危险工具开关，无需重启服务。"""
        if self._handler is None:
            if token is not None:
                self._token = token
            if allow_dangerous is not None:
                self._allow_dangerous = bool(allow_dangerous)
            return
        self._handler.configure(token=token, allow_dangerous=allow_dangerous)
        if token is not None:
            self._token = token
            if self.is_running():
                with contextlib.suppress(Exception):
                    write_session_file(self._port, token, extra={"host": DEFAULT_HOST})
        if allow_dangerous is not None:
            self._allow_dangerous = bool(allow_dangerous)

    def client_config(self, python_executable=None, server_script=None):
        """生成客户端（Claude Desktop / Cursor）可用的 mcpServers 配置片段。

        ``command`` 必须是**能真正跑起 MCP Server 的 Python 解释器**，不能直接
        用 ``sys.executable`` —— macOS 上它是 QGIS 的 GUI 主程序（详见
        ``resolve_python_executable``）。解析结果与替换说明分别落在
        ``command`` 与 ``self.last_python_hint``。
        """
        try:
            if server_script is None:
                server_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "mcp_server", "qgis_agent_mcp_server.py",
                )
            resolved, note = resolve_python_executable(
                server_script=server_script, python_executable=python_executable
            )
            self.last_python_hint = note
            python_executable = resolved
            return {
                "mcpServers": {
                    "qgis-agent": {
                        "command": python_executable,
                        "args": [server_script],
                        "env": {
                            "QGIS_AGENT_MCP_PORT": str(self._port or DEFAULT_PORT),
                            "QGIS_AGENT_MCP_TOKEN": self._token or "",
                        },
                    }
                }
            }
        except Exception:  # noqa: BLE001
            return {}
