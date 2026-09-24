# QGIS Agent MCP Server

把 QGIS Agent 插件暴露成标准 [MCP](https://modelcontextprotocol.io/) 服务，
让 **Claude Desktop / Cursor / Codex** 等外部 Agent 直接驱动本机 QGIS。

## 架构

```
MCP 客户端  ──stdio JSON-RPC──▶  qgis_agent_mcp_server.py  ──TCP 127.0.0.1──▶  QGIS 插件内的 mcp_bridge
```

两个组件：

| 组件 | 位置 | 职责 |
|---|---|---|
| MCP Server | 本目录（插件外部，独立进程） | 实现 MCP 协议、把自己的工具清单代理给插件 |
| mcp_bridge | `../mcp_bridge.py`（QGIS 插件内） | 监听回环端口、校验令牌、把工具调用调度到 QGIS 主线程 |

## 快速开始

1. 打开 QGIS → 「QGIS Agent」面板 → **MCP** 标签页（与「模型」分开的独立页签）；
2. 点 **启动服务**（端口与令牌已自动生成，通常无需改动）；
3. 点 **复制客户端配置**，把得到的 JSON 粘进客户端的 `mcpServers`；
4. 重启客户端，即可在工具列表里看到 QGIS 工具。

**Claude Desktop** 配置文件位置：

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

**Cursor**：`~/.cursor/mcp.json`（格式同为 `mcpServers`）。

配置片段形如：

```json
{
  "mcpServers": {
    "qgis-agent": {
      "command": "/path/to/python3",
      "args": ["/path/to/plugins/qgis_agent/mcp_server/qgis_agent_mcp_server.py"],
      "env": {
        "QGIS_AGENT_MCP_PORT": "9876",
        "QGIS_AGENT_MCP_TOKEN": "<在 MCP 页签点「复制令牌」拿到>"
      }
    }
  }
}
```

> ⚠️ **`command` 必须是 Python 解释器**，不要写 QGIS 主程序
> （`QGIS.app/Contents/MacOS/QGIS`、`qgis-bin.exe` 等）——那是 GUI 程序，
> 不会讲 stdio 的 JSON-RPC。**优先直接「复制客户端配置」**：插件会实际执行
> 一次校验，挑一个真的能跑起来的解释器写进去。

## 零依赖

本 Server 只用 Python 标准库，**不需要 pip install 任何东西**，
因此任意 Python 3.8+ 都可以当 `command`：系统 `python3`、Homebrew、
python.org 或 QGIS 自带的解释器都行。

⚠️ 唯一的例外是 macOS 上 QGIS 自带的 `QGIS.app/Contents/MacOS/python3.12`：
它是给 app 内部用的 framework 解释器，**脱离 app 直接运行会报**
`Could not find platform independent libraries <prefix>`，不能写进 `command`。
（插件会真的拿候选解释器跑一遍 `--help` 来判定，这类跑不起来的会被自动跳过。）

## 命令行

```bash
# 作为 MCP 服务运行（由客户端自动拉起，一般不用手工执行）
python3 qgis_agent_mcp_server.py

# 自检：确认能连上 QGIS 插件内的桥接服务
python3 qgis_agent_mcp_server.py --check

# 手动指定桥接地址（默认从环境变量或会话文件读取）
python3 qgis_agent_mcp_server.py --check --port 9876 --token <token>
```

## 凭据发现顺序

1. 环境变量 `QGIS_AGENT_MCP_PORT` / `QGIS_AGENT_MCP_TOKEN`（或 `--port` / `--token`）
2. 会话文件 `~/.qgis_agent/mcp_session.json`（插件启动服务时写入，停止时删除）
3. 默认值 `127.0.0.1:9876`（无令牌时会连接失败，属预期）

环境变量 `QGIS_AGENT_MCP_SESSION_FILE` 可指定会话文件的其它位置。

## 安全模型

| 项 | 做法 |
|---|---|
| 监听地址 | **仅 127.0.0.1**，不支持绑定其它地址，局域网内其他机器连不上 |
| 认证 | **强制令牌**（32 字节随机），常数时间比较；无令牌一律拒绝 |
| 危险工具 | `execute_pyqgis` / `execute_processing` / `remove_layer` / `load_project` / `save_project` **默认不出现在工具清单里**，需在「MCP」页签显式放开 |
| 危险操作确认 | 即便放开，每次执行仍会在 QGIS 界面上弹确认框，由用户本人点击 |
| 默认状态 | 插件启动时**不自动监听**，需用户点「启动服务」；可勾选「随插件自动启动」 |
| 会话文件 | 写入时收紧权限到 `0600`（Windows 等平台自动忽略） |

## 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 客户端报 `无法连接 QGIS 插件（127.0.0.1:9876）` | QGIS 没开，或设置页里没点「启动服务」；点设置页的 **测试连通性** 自检 |
| `访问令牌无效` | 客户端配置里的令牌与设置页不一致；点「复制客户端配置」重新粘贴 |
| `端口 9876 已被占用` | 换一个端口（设置页可改），改完重新启动服务 |
| 客户端看不到任何工具 | 确认 `tools/list` 不报错；自检通过后重启客户端（多数客户端只在启动时拉一次工具清单） |
| `execute_pyqgis` 不在工具列表 | 属预期：危险工具默认隐藏，需在设置页勾选放开 |

## 工具清单

工具清单**不在此处维护** —— `tools/list` 每次都向 QGIS 插件查询，
唯一真源是插件里的 `qgis_tools.TOOL_DEFINITIONS`。
这样插件新增工具后，MCP 侧自动跟上，不会出现两边漂移。
