# -*- coding: utf-8 -*-
"""QGIS Agent MCP 桥接协议层。

纯标准库实现，**不依赖 Qt / QGIS**，因此可以在裸 Python 下直接单测。

本层只做三件事：
1. 访问令牌校验（`hmac.compare_digest` 常数时间比较）；
2. 行分隔 JSON 的编解码与错误兜底；
3. 把 `list_tools` / `call_tool` 请求路由到注入的回调。

进程与传输分层：
- `mcp_bridge.py`    —— QGIS 插件内，负责 TCP socket 与 Qt 线程调度；
- `mcp_protocol.py`  —— 本文件，双方共用的协议与常量；
- `mcp_server/qgis_agent_mcp_server.py` —— 插件外部，stdio MCP Server（给 Claude/Cursor 用）。

两边通过 `~/.qgis_agent/mcp_session.json` 交换端口与令牌，
或用环境变量 QGIS_AGENT_MCP_PORT / QGIS_AGENT_MCP_TOKEN 覆盖。
"""

import contextlib
import hmac
import json
import os

# ── 协议常量（插件侧与 MCP Server 侧必须一致）──
PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876

SESSION_DIRNAME = ".qgis_agent"
SESSION_FILENAME = "mcp_session.json"

ENV_PORT = "QGIS_AGENT_MCP_PORT"
# 环境变量【名】，不是密钥值本身 —— B105 按变量名里的 token 字样匹配，属误报。
ENV_TOKEN = "QGIS_AGENT_MCP_TOKEN"  # nosec B105
ENV_SESSION_FILE = "QGIS_AGENT_MCP_SESSION_FILE"

# 单行请求上限，防止畸形/恶意超长输入吃满内存
MAX_LINE_BYTES = 4 * 1024 * 1024

# ── 错误码 ──
ERR_BAD_REQUEST = "bad_request"
ERR_UNAUTHORIZED = "unauthorized"
ERR_UNKNOWN_METHOD = "unknown_method"
ERR_TOOL_NOT_ALLOWED = "tool_not_allowed"
ERR_INTERNAL = "internal_error"


def session_file_path():
    """返回会话文件的绝对路径（跨 QGIS 版本稳定，不随 profile 变动）。"""
    return os.path.join(os.path.expanduser("~"), SESSION_DIRNAME, SESSION_FILENAME)


def write_session_file(port, token, extra=None):
    """把端口与令牌写入会话文件，供外部 MCP Server 自动发现。

    写入前收紧权限（0600），避免同机其他用户读取令牌。
    优先「写临时文件 + 原子替换」；某些平台 / 沙箱会拒绝 rename（EPERM），
    此时退化为直接覆盖写最终路径（牺牲原子性，但会话文件读坏的后果
    仅仅是需要手工填端口与令牌，可接受）。
    返回写入路径；失败时抛异常，由调用方决定是否降级。
    """
    path = session_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 目录收紧到 0700：令牌文件本身已是 0600，但他人仍可列出目录名，
    # 顺手把目录也收紧，多用户主机上更干净。
    with contextlib.suppress(OSError):
        os.chmod(os.path.dirname(path), 0o700)
    payload = {"protocol": PROTOCOL_VERSION, "port": int(port), "token": token}
    if extra:
        payload.update(extra)
    data = json.dumps(payload, ensure_ascii=False)

    def _write_to(target):
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(data)
        # Windows 等平台可能不支持 posix 权限，忽略
        with contextlib.suppress(OSError):
            os.chmod(target, 0o600)

    tmp = path + ".tmp"
    # 先写临时文件再原子替换；replace 被拒（EPERM）时退化为直接覆盖写最终路径
    with contextlib.suppress(OSError):
        _write_to(tmp)
        with contextlib.suppress(OSError):
            os.replace(tmp, path)
            return path

    _write_to(path)
    with contextlib.suppress(OSError):
        os.remove(tmp)
    return path


def read_session_file(path=None):
    """读取会话文件；不存在或损坏时返回 {}。"""
    path = path or session_file_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def clear_session_file(path=None):
    """删除会话文件（服务停止时调用），失败静默。"""
    path = path or session_file_path()
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def dumps(obj):
    """紧凑 JSON 编码：不转义非 ASCII，且不含裸换行（行协议要求单行）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def to_mcp_tool(tool_def):
    """把插件原生工具定义转成 MCP 的 tools/list 条目。

    插件内 `TOOL_DEFINITIONS` 的字段是
    ``{"name":…, "description":…, "parameters":{…}}``，
    MCP 规范要求 ``{"name":…, "description":…, "inputSchema":{…}}``
    —— 只有键名不同，这里做唯一一次转换，避免两边各维护一份工具清单。
    """
    schema = tool_def.get("parameters")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}, "required": []}
    return {
        "name": tool_def.get("name", ""),
        "description": tool_def.get("description", ""),
        "inputSchema": schema,
    }


class MCPProtocolHandler(object):
    """行协议请求处理器。

    参数：
    - list_tools: 无参可调用对象，返回插件原生工具定义列表；
    - call_tool:  ``(name, arguments) -> dict``，真正执行工具；
    - token:      访问令牌；为空表示拒绝一切请求（不做"无令牌即放行"）；
    - dangerous_tools: 危险工具名集合；
    - allow_dangerous: 是否允许外部 Agent 看到并调用危险工具；
    - info_provider:   无参可调用对象，返回附加状态信息（版本等）。
    """

    def __init__(self, list_tools, call_tool, token=None, dangerous_tools=(),
                 allow_dangerous=False, info_provider=None,
                 service_name="qgis-agent"):
        self._list_tools = list_tools
        self._call_tool = call_tool
        self._token = token or ""
        self._dangerous_tools = set(dangerous_tools or ())
        self._allow_dangerous = bool(allow_dangerous)
        self._info_provider = info_provider
        self._service_name = service_name

    # ── 配置热更新（设置页改动后调用，无需重启服务）──
    def configure(self, token=None, allow_dangerous=None):
        if token is not None:
            self._token = token or ""
        if allow_dangerous is not None:
            self._allow_dangerous = bool(allow_dangerous)

    @property
    def token(self):
        return self._token

    def check_token(self, candidate):
        """常数时间比较访问令牌。未配置令牌时一律拒绝。"""
        if not self._token:
            return False
        if not isinstance(candidate, str):
            return False
        return hmac.compare_digest(candidate, self._token)

    # ── 工具可见性 ──
    def visible_tools(self):
        tools = list(self._list_tools() or [])
        if self._allow_dangerous:
            return tools
        return [t for t in tools if t.get("name") not in self._dangerous_tools]

    # ── 请求处理 ──
    def handle_request(self, req):
        """处理一条已解析的请求，返回响应字典（永不抛异常）。"""
        if not isinstance(req, dict):
            return self._error(ERR_BAD_REQUEST, "请求必须是 JSON 对象。")

        if not self.check_token(req.get("token")):
            return self._error(
                ERR_UNAUTHORIZED,
                "访问令牌无效。请在 QGIS Agent 的「MCP」页签中复制最新令牌。",
            )

        method = req.get("method")
        params = req.get("params")
        if not isinstance(params, dict):
            params = {}

        if method == "ping":
            return self._ok(self._ping_payload())
        if method in ("list_tools", "tools/list"):
            return self._ok({"tools": [to_mcp_tool(t) for t in self.visible_tools()]})
        if method in ("call_tool", "tools/call"):
            return self._handle_call(params)
        return self._error(ERR_UNKNOWN_METHOD, "未知方法: %r" % (method,))

    def _ping_payload(self):
        payload = {
            "pong": True,
            "service": self._service_name,
            "protocol": PROTOCOL_VERSION,
            "allow_dangerous": self._allow_dangerous,
        }
        if callable(self._info_provider):
            # 状态信息失败不能影响 ping
            with contextlib.suppress(Exception):
                extra = self._info_provider()
                if isinstance(extra, dict):
                    payload.update(extra)
        return payload

    def _handle_call(self, params):
        name = params.get("name") or params.get("tool")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if not name or not isinstance(name, str):
            return self._error(ERR_BAD_REQUEST, "缺少工具名 name。")

        if name in self._dangerous_tools and not self._allow_dangerous:
            return self._error(
                ERR_TOOL_NOT_ALLOWED,
                "工具 %s 属于危险操作，当前未在设置中允许外部调用。"
                "如需使用，请在 QGIS Agent 的「MCP」页签中勾选"
                "「允许外部调用危险工具」（每次执行仍会在 QGIS 界面弹窗确认）。" % name,
            )

        try:
            result = self._call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - 任何异常都转成结构化错误回给客户端
            return self._error(ERR_INTERNAL, "工具执行异常: %s" % (exc,))
        if not isinstance(result, dict):
            result = {"result": result}
        return self._ok(result)

    # ── 行协议 ──
    def handle_line(self, line):
        """处理一行请求，返回一行响应；空行返回 None。"""
        if isinstance(line, bytes):
            if len(line) > MAX_LINE_BYTES:
                return dumps(self._error(ERR_BAD_REQUEST, "请求超过大小上限。"))
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                return dumps(self._error(ERR_BAD_REQUEST, "请求不是合法 UTF-8。"))
        if not isinstance(line, str):
            return dumps(self._error(ERR_BAD_REQUEST, "请求必须是字符串。"))
        if len(line.encode("utf-8", "ignore")) > MAX_LINE_BYTES:
            return dumps(self._error(ERR_BAD_REQUEST, "请求超过大小上限。"))
        line = line.strip()
        if not line:
            return None
        try:
            req = json.loads(line)
        except ValueError:
            return dumps(self._error(ERR_BAD_REQUEST, "请求不是合法 JSON。"))
        return dumps(self.handle_request(req))

    # ── 响应构造 ──
    @staticmethod
    def _ok(result):
        return {"ok": True, "result": result}

    @staticmethod
    def _error(code, message):
        return {"ok": False, "code": code, "error": message}


def build_response(payload):
    """把 handler 的返回体包装成带 ok 字段的行协议响应（供 socket 层使用）。"""
    if payload is None:
        return None
    return dumps(payload)
