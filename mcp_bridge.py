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
import socket
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


def _default_token():
    """生成 32 字节随机十六进制令牌。"""
    return os.urandom(32).hex()


def _safe_qgis_info():
    """采集少量 QGIS 状态信息，失败时返回空字典。"""
    info = {}
    try:
        from qgis.core import Qgis, QgsProject
        info["qgis_version"] = Qgis.QGIS_VERSION
        project = QgsProject.instance()
        info["project_path"] = project.fileName() or ""
        info["layer_count"] = len(project.mapLayers())
    except Exception:  # noqa: BLE001 - 状态信息缺失不影响服务
        pass
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
            try:
                server.close()
            except OSError:
                pass
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
                    try:
                        conn.close()
                    except OSError:
                        pass
                    continue
                self._conn_count += 1
            worker = threading.Thread(
                target=self._serve_connection, args=(conn,), daemon=True
            )
            worker.start()

        try:
            server.close()
        except OSError:
            pass

    def _release_connection(self):
        with self._conn_lock:
            self._conn_count = max(0, self._conn_count - 1)

    @staticmethod
    def _reject_oversized(conn):
        """单行超限时回一条错误并（由调用方）断开，避免连接被无声关闭。"""
        payload = {"ok": False, "code": "bad_request",
                   "error": "单行请求超过 %d 字节上限，连接已关闭。" % MAX_LINE_BYTES}
        try:
            conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

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
                try:
                    conn.settimeout(IDLE_TIMEOUT)
                except OSError:
                    pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._release_connection()

    def stop(self):
        self._stop_flag.set()
        try:
            if self._server is not None:
                self._server.close()
        except OSError:
            pass
        # 主动连一次自身，唤醒可能正阻塞在 accept 上的循环（部分平台 close 不解除阻塞）
        try:
            wake = socket.create_connection((self._host, self.actual_port or self._port), timeout=1)
            wake.close()
        except OSError:
            pass

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
        self._token = ""
        self._allow_dangerous = False

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
                try:
                    write_session_file(self._port, token, extra={"host": DEFAULT_HOST})
                except Exception:  # noqa: BLE001
                    pass
        if allow_dangerous is not None:
            self._allow_dangerous = bool(allow_dangerous)

    def client_config(self, python_executable=None, server_script=None):
        """生成客户端（Claude Desktop / Cursor）可用的 mcpServers 配置片段。"""
        try:
            import sys
            if python_executable is None:
                python_executable = sys.executable or "python3"
            if server_script is None:
                server_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "mcp_server", "qgis_agent_mcp_server.py",
                )
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
