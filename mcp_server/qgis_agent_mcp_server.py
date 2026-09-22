#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QGIS Agent MCP Server（stdio，零第三方依赖）。

把 QGIS Agent 插件暴露成标准 MCP 服务，供 Claude Desktop / Cursor / Codex
等 MCP 客户端调用，从而用外部 Agent 驱动本机 QGIS。

架构（与 GitHub 上主流 QGIS MCP 项目一致的双组件设计）：

    MCP 客户端  --stdio JSON-RPC-->  本脚本  --TCP 127.0.0.1-->  QGIS 插件内的 mcp_bridge

设计要点：
1. **零依赖**：只用 Python 标准库，任意 Python 3.8+ 即可运行，无需 pip install；
2. **工具清单不复制**：`tools/list` 每次都向插件查询（插件是唯一真源），
   所以插件新增工具后这里自动跟上；
3. **凭据自动发现**：端口与令牌优先取环境变量，否则读
   `~/.qgis_agent/mcp_session.json`（插件启动服务时写入），用户通常零配置；
4. **stdout 只输出协议**：任何日志一律走 stderr，避免污染 JSON-RPC 流。

用法（由 MCP 客户端拉起，也可手工自检）：

    python3 qgis_agent_mcp_server.py            # 作为 MCP 服务运行
    python3 qgis_agent_mcp_server.py --check    # 自检：探测插件内的桥接服务
"""

import argparse
import json
import os
import socket
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
SESSION_DIRNAME = ".qgis_agent"
SESSION_FILENAME = "mcp_session.json"

ENV_PORT = "QGIS_AGENT_MCP_PORT"
ENV_TOKEN = "QGIS_AGENT_MCP_TOKEN"
ENV_SESSION_FILE = "QGIS_AGENT_MCP_SESSION_FILE"

SERVER_NAME = "qgis-agent"
SERVER_VERSION = "1.0.0"

# 声明支持的 MCP 协议版本；客户端请求的版本若在其中则回显，否则回退到最后一个
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
FALLBACK_PROTOCOL_VERSION = "2025-06-18"

# 单条结果文本上限，避免把超长结果整个塞进模型上下文
MAX_RESULT_CHARS = 200000
BRIDGE_TIMEOUT = 600.0


def log(message):
    """日志一律写 stderr —— stdout 必须留给 JSON-RPC。"""
    sys.stderr.write("[qgis-agent-mcp] %s\n" % message)
    sys.stderr.flush()


def session_file_path():
    override = os.environ.get(ENV_SESSION_FILE)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), SESSION_DIRNAME, SESSION_FILENAME)


def read_session_file():
    try:
        with open(session_file_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class BridgeClient(object):
    """到 QGIS 插件内 mcp_bridge 的 TCP 客户端（行分隔 JSON）。"""

    def __init__(self, host=None, port=None, token=None):
        session = read_session_file()
        self.host = host or session.get("host") or DEFAULT_HOST
        self.port = int(port or os.environ.get(ENV_PORT) or session.get("port") or DEFAULT_PORT)
        self.token = token or os.environ.get(ENV_TOKEN) or session.get("token") or ""
        self._sock = None
        self._stream = None

    # ── 连接管理 ──
    def _connect(self):
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=BRIDGE_TIMEOUT)
        self._sock = sock
        self._stream = sock.makefile("rwb")

    def close(self):
        for obj in (self._stream, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._stream = None
        self._sock = None

    def request(self, method, params=None):
        """发送一次请求并返回响应体（dict）。失败抛 BridgeError。"""
        payload = {"method": method, "token": self.token}
        if params:
            payload["params"] = params
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        last_error = None
        for attempt in (1, 2):
            try:
                if self._stream is None:
                    self._connect()
                self._stream.write(line.encode("utf-8"))
                self._stream.flush()
                raw = self._stream.readline()
                if not raw:
                    raise BridgeError("桥接服务已断开连接")
                return json.loads(raw.decode("utf-8"))
            except (OSError, ValueError, BridgeError) as exc:
                last_error = exc
                self.close()
                if attempt == 1:
                    continue
        raise BridgeError(str(last_error) if last_error else "未知错误")

    def describe(self):
        return "%s:%d" % (self.host, self.port)


class BridgeError(Exception):
    pass


HELP_HINT = (
    "请确认：① QGIS 正在运行；② 插件「模型配置 → MCP 服务」中已点「启动」；"
    "③ 端口 / 令牌与设置页一致（默认 9876）。"
)


class MCPServer(object):
    """MCP 协议（stdio，JSON-RPC 2.0 行分隔）实现。"""

    def __init__(self):
        self.bridge = BridgeClient()
        self.protocol_version = FALLBACK_PROTOCOL_VERSION
        self.initialized = False

    # ── 传输层 ──
    def _write(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        out = sys.stdout.buffer
        out.write(data + b"\n")
        out.flush()

    def _result(self, msg_id, result):
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id, code, message):
        self._write({"jsonrpc": "2.0", "id": msg_id,
                     "error": {"code": code, "message": message}})

    # ── 方法实现 ──
    def handle(self, msg):
        if not isinstance(msg, dict):
            self._error(None, -32600, "Invalid Request: 消息必须是 JSON 对象")
            return
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}

        is_notification = msg_id is None

        if method == "initialize":
            self._on_initialize(msg_id, params)
        elif method in ("notifications/initialized", "initialized"):
            self.initialized = True
        elif method == "tools/list":
            self._on_tools_list(msg_id)
        elif method == "tools/call":
            self._on_tools_call(msg_id, params)
        elif method == "ping":
            if not is_notification:
                self._result(msg_id, {})
        elif method in ("notifications/cancelled", "notifications/roots/list_changed"):
            pass
        elif method in ("resources/list", "prompts/list"):
            if not is_notification:
                self._result(msg_id, {"resources": []} if method.startswith("resources")
                             else {"prompts": []})
        else:
            if not is_notification:
                self._error(msg_id, -32601, "Method not found: %s" % method)

    def _on_initialize(self, msg_id, params):
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            self.protocol_version = requested
        else:
            self.protocol_version = FALLBACK_PROTOCOL_VERSION
        self._result(msg_id, {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "本服务把 QGIS Agent 插件暴露为工具。QGIS 必须正在运行且插件里的"
                "「MCP 服务」已启动。工具清单由插件动态提供；危险操作（执行任意 PyQGIS "
                "代码等）即使被允许，也会在 QGIS 界面上弹出确认框，需要用户点击确认。"
            ),
        })

    def _on_tools_list(self, msg_id):
        try:
            resp = self.bridge.request("list_tools")
        except BridgeError as exc:
            self._error(msg_id, -32000, "无法连接 QGIS 插件（%s）：%s %s"
                        % (self.bridge.describe(), exc, HELP_HINT))
            return
        if not resp.get("ok"):
            self._error(msg_id, -32000, "QGIS 插件拒绝了请求：%s"
                        % (resp.get("error") or "未知错误"))
            return
        tools = (resp.get("result") or {}).get("tools") or []
        self._result(msg_id, {"tools": tools})

    def _on_tools_call(self, msg_id, params):
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if not isinstance(name, str) or not name:
            self._error(msg_id, -32602, "Invalid params: 缺少 name（需为非空字符串）")
            return
        try:
            resp = self.bridge.request("call_tool", {"name": name, "arguments": arguments})
        except BridgeError as exc:
            self._result(msg_id, self._text_result(
                "无法连接 QGIS 插件（%s）：%s %s" % (self.bridge.describe(), exc, HELP_HINT),
                is_error=True))
            return

        if not resp.get("ok"):
            self._result(msg_id, self._text_result(
                resp.get("error") or "插件返回了未分类错误", is_error=True))
            return

        result = resp.get("result")
        is_error = isinstance(result, dict) and bool(result.get("error"))
        text = self._format_result(result)
        self._result(msg_id, self._text_result(text, is_error=is_error))

    @staticmethod
    def _format_result(result):
        if isinstance(result, str):
            text = result
        else:
            try:
                text = json.dumps(result, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                text = repr(result)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…（结果过长已截断）"
        return text

    @staticmethod
    def _text_result(text, is_error=False):
        payload = {"content": [{"type": "text", "text": text}]}
        if is_error:
            payload["isError"] = True
        return payload

    # ── 主循环 ──
    def serve(self):
        stdin = sys.stdin.buffer
        while True:
            try:
                raw = stdin.readline()
            except (KeyboardInterrupt, OSError):
                break
            if not raw:
                break
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._error(None, -32700, "Parse error: 无法解析 JSON")
                continue
            try:
                self.handle(msg)
            except Exception as exc:  # noqa: BLE001 - 单条消息异常不应终止服务
                log("处理消息失败: %s" % exc)
                if isinstance(msg, dict) and msg.get("id") is not None:
                    self._error(msg.get("id"), -32603, "Internal error: %s" % exc)
        self.bridge.close()
        return 0


def run_check(client=None):
    """自检：探测插件内的桥接服务是否可达。返回进程退出码。"""
    client = client or BridgeClient()
    print("会话文件: %s (%s)" % (session_file_path(),
                              "存在" if os.path.exists(session_file_path()) else "不存在"))
    print("桥接地址: %s" % client.describe())
    print("令牌长度: %d" % len(client.token or ""))
    try:
        resp = client.request("ping")
    except BridgeError as exc:
        print("结果: 连接失败 —— %s" % exc)
        print(HELP_HINT)
        return 1
    if not resp.get("ok"):
        print("结果: 服务可达但拒绝请求 —— %s" % (resp.get("error") or "未知错误"))
        return 2
    info = resp.get("result") or {}
    print("结果: 连接成功")
    print("  QGIS 版本: %s" % info.get("qgis_version", "未知"))
    print("  当前工程: %s" % (info.get("project_path") or "(未保存)"))
    print("  图层数量: %s" % info.get("layer_count", "未知"))
    print("  暴露危险工具: %s" % info.get("allow_dangerous"))
    try:
        listed = client.request("list_tools")
        tools = (listed.get("result") or {}).get("tools") or []
        print("  可见工具数: %d —— %s" % (len(tools), ", ".join(t["name"] for t in tools)))
    except BridgeError as exc:
        print("  列出工具失败: %s" % exc)
        return 1
    finally:
        client.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="QGIS Agent MCP Server（stdio，零依赖）")
    parser.add_argument("--check", action="store_true",
                        help="自检：探测 QGIS 插件内的桥接服务是否可达，然后退出")
    parser.add_argument("--host", default=None, help="桥接服务主机（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=None, help="桥接服务端口（默认 9876）")
    parser.add_argument("--token", default=None, help="访问令牌（默认读环境变量或会话文件）")
    args = parser.parse_args(argv)

    server = MCPServer()
    if args.host or args.port or args.token:
        server.bridge = BridgeClient(host=args.host, port=args.port, token=args.token)
    if args.check:
        return run_check(server.bridge)
    return server.serve()


if __name__ == "__main__":
    sys.exit(main())
