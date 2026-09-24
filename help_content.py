# -*- coding: utf-8 -*-
"""帮助页内容 —— 纯数据 + 纯函数，不依赖 Qt / qgis。

为什么单独抽出来：

    1. 帮助页原来是 `qgis_agent_dockwidget_base_ui.py` 里一段 380 行的 HTML 字符串
       （Designer 风格文件里塞内容模板），改一次文案要在 800 行文件里翻半天；
    2. 它写死了颜色（`#333`/`#888`）和一堆 Qt 富文本**根本不支持**的属性
       （`display:flex`、`linear-gradient`、`border-radius`、`tr:nth-child`），
       实际渲染出来又乱又扁 —— 内容与样式都没法验证。抽成纯函数后可以直接单测。

Qt 富文本（QTextDocument）能力边界（本机实测，详见 references/qt_richtext_layout_probe.py）：

    ✅ 元素选择器 (h2/p/code/pre/th/td/a) + color / background-color / border / padding
    ✅ <table width="100%" cellspacing="0" cellpadding="0">（**必须用属性**，
       CSS 的 `table{width:100%}` 不生效）
    ✅ `border-collapse: collapse`
    ✅ 单格表格 + 单元格 inline style 做「卡片」（block 级元素的 border 不生效）
    ❌ CSS 自定义属性 `var(--x, fallback)`（连 fallback 一起丢）
    ❌ border-radius / flex / linear-gradient / :nth-child / <details> / JS

因此本模块只输出上面 ✅ 的那一套；颜色用 `__TOKEN__` 占位，由调用方按调色板注入。
"""

import html as html_module

# 颜色占位符 → utils.chat_colors() 的键。写成占位符而不是 % 格式化的原因：
# 模板里到处都是 `width="100%"`，用 % 格式化必须逐个转义成 %%，极易漏（踩过）。
_COLOR_TOKENS = {
    "__BG__": "chat_bg",
    "__PANEL__": "ai_bg",
    "__TOOLBG__": "tool_bg",
    "__FG__": "fg",
    "__MUTED__": "muted",
    "__BORDER__": "border",
    "__CODEBG__": "code_bg",
    "__ACCENT__": "user_edge",
}

# 未取到调色板时的兜底（浅色）
_FALLBACK_COLORS = {
    "chat_bg": "#F7F8FA",
    "ai_bg": "#F1F3F6",
    "fg": "#181C14",
    "muted": "#6B7280",
    "border": "#DFE3E8",
    "code_bg": "#E6E8EC",
    "tool_bg": "#F2F6FA",
    "user_edge": "#4A90D9",
}

_MONO = '"SF Mono", Menlo, Consolas, Monaco, monospace'
_SANS = ('-apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", '
         'Arial, sans-serif')


def _card(inner_html, color=None, bg=None):
    """单格表格实现的卡片。

    block 级元素（div/p）的 border 在 QTextDocument 里不生效，只有表格单元格能画
    左侧色条 + 底色，所以卡片一律走这里。
    """
    return (
        '<table width="100%%" cellpadding="0" cellspacing="0"'
        ' style="margin:6px 0; border-collapse:collapse;"><tr>'
        '<td style="border-left:3px solid %s; background-color:%s; padding:7px 10px;">'
        '%s</td></tr></table>'
    ) % (color or "__ACCENT__", bg or "__TOOLBG__", inner_html)


def build_tool_rows(tools, desc_limit=34):
    """由 TOOL_DEFINITIONS 生成内置工具表格行（两列：工具 / 作用）。

    工具清单必须动态生成：硬编码必然随版本过期（旧版帮助页只写了 6 个工具，
    而实际有 20 个）。
    """
    rows = []
    for tool in tools or []:
        name = (tool or {}).get("name", "")
        if not name:
            continue
        desc = ((tool or {}).get("description") or "").strip()
        # 只取第一句，避免表格被长描述撑成一大段
        for sep in ("。", "；", "，", "\n"):
            if sep in desc:
                desc = desc.split(sep)[0]
        if len(desc) > desc_limit:
            desc = desc[:desc_limit] + "…"
        rows.append(
            '<tr><td style="width:34%%;"><code>%s</code></td><td>%s</td></tr>'
            % (html_module.escape(name), html_module.escape(desc))
        )
    if not rows:
        return '<tr><td colspan="2">工具清单暂时无法读取。</td></tr>'
    return "\n".join(rows)


def apply_colors(html_text, colors):
    """把模板里的 __TOKEN__ 占位替换为实际色值。缺失的键退回浅色兜底。"""
    merged = dict(_FALLBACK_COLORS)
    for key, value in (colors or {}).items():
        if value:
            merged[key] = value
    for token, key in _COLOR_TOKENS.items():
        html_text = html_text.replace(token, str(merged.get(key) or "#000000"))
    return html_text


def build_help_html(version, colors=None, tools=None, tool_count=0):
    """生成「帮助」页签的完整 HTML（无 JS、无外部资源，可离线渲染）。"""
    rows = build_tool_rows(tools)
    if not tool_count:
        tool_count = len(tools or [])

    html_text = _TEMPLATE
    html_text = html_text.replace("__VERSION__", str(version or "以插件管理器显示为准"))
    html_text = html_text.replace("__TOOL_COUNT__", str(tool_count))
    html_text = html_text.replace("__TOOLS_ROWS__", rows)
    html_text = html_text.replace("__MONO__", _MONO)
    html_text = html_text.replace("__SANS__", _SANS)
    return apply_colors(html_text, colors)


# ──────────────────────────────────────────────────────────────
# 模板
# ──────────────────────────────────────────────────────────────
_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body { background-color: __BG__; color: __FG__; font-family: __SANS__;
       font-size: 12px; line-height: 1.55; margin: 0; padding: 2px 4px; }
h1 { color: __FG__; font-size: 17px; margin: 0 0 1px 0; }
h2 { color: __ACCENT__; font-size: 13px; margin: 15px 0 5px 0;
     border-bottom: 1px solid __BORDER__; padding-bottom: 3px; }
h3 { color: __FG__; font-size: 12px; margin: 9px 0 3px 0; }
p { margin: 4px 0; }
ul, ol { margin: 4px 0; padding-left: 18px; }
li { margin: 2px 0; }
a { color: __ACCENT__; }
code { background-color: __CODEBG__; color: __FG__; font-family: __MONO__;
       font-size: 11px; }
pre { background-color: __CODEBG__; color: __FG__; font-family: __MONO__;
      font-size: 11px; border-left: 3px solid __BORDER__; padding: 7px 9px;
      margin: 5px 0; white-space: pre-wrap; word-break: break-word; }
table { margin: 5px 0; }
th { background-color: __TOOLBG__; color: __MUTED__; border: 1px solid __BORDER__;
     padding: 4px 7px; text-align: left; font-size: 11px; }
td { border: 1px solid __BORDER__; padding: 4px 7px; vertical-align: top; }
.sub { color: __MUTED__; font-size: 11px; margin: 0 0 8px 0; }
.dim { color: __MUTED__; font-size: 11px; }
</style>
</head>
<body>

<h1>🗺️ QGIS Agent</h1>
<p class="sub">v__VERSION__ · 把大语言模型接进 QGIS：用中文描述任务，Agent 自己调用 QGIS 工具完成</p>

<h2>🚀 30 秒上手</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
<tr><td style="width:26px; text-align:center; border:none; padding:5px 4px; background-color:__PANEL__;"><b>1</b></td>
    <td style="border:none; padding:5px 4px; background-color:__PANEL__;">在「模型」页添加一个模型：填 Base URL、模型名、API Key，
        然后点「测试连接与诊断」确认可用</td></tr>
<tr><td style="text-align:center; border:none; padding:5px 4px; background-color:__PANEL__;"><b>2</b></td>
    <td style="border:none; padding:5px 4px; background-color:__PANEL__;">回到「对话」页，直接用中文下达任务，例如
        <code>对道路图层做 100 米缓冲区</code></td></tr>
<tr><td style="text-align:center; border:none; padding:5px 4px; background-color:__PANEL__;"><b>3</b></td>
    <td style="border:none; padding:5px 4px; background-color:__PANEL__;">涉及代码执行会先弹确认框；生成的代码、执行日志与
        调试建议都在「报告」页</td></tr>
</table>

__CARD_TIP__

<h2>🧭 页签都在做什么</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
<tr><th style="width:64px;">页签</th><th>作用</th></tr>
<tr><td>对话</td><td>下达任务、查看回复（带思考过程，可折叠展开）</td></tr>
<tr><td>历史</td><td>历史对话列表，可搜索 / 加载 / 删除</td></tr>
<tr><td>模型</td><td>模型增删、测试连接与诊断、浏览器兼容 TLS</td></tr>
<tr><td>MCP</td><td>把 QGIS 工具暴露给 Claude Desktop / Cursor 等外部 Agent：启停服务、端口与令牌、复制客户端配置、连通性自检</td></tr>
<tr><td>工作流</td><td>本次任务已执行的步骤可视化</td></tr>
<tr><td>报告</td><td>生成的 PyQGIS 代码、执行日志、SmartDebugger 诊断结论（<b>排查报错看这里</b>）</td></tr>
<tr><td>帮助</td><td>本页</td></tr>
</table>

<h2>💬 能做什么</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
<tr><th style="width:88px;">类别</th><th>示例指令</th></tr>
<tr><td>查看数据</td><td><code>查看当前项目有哪些图层，各自多少要素</code></td></tr>
<tr><td>加载数据</td><td><code>添加图层 /data/roads.shp</code>（用绝对路径）</td></tr>
<tr><td>坐标转换</td><td><code>把 roads 图层重投影到 EPSG:3857</code></td></tr>
<tr><td>空间分析</td><td><code>对道路图层做 100 米缓冲区</code></td></tr>
<tr><td>属性筛选</td><td><code>筛选面积大于 100 的建筑</code></td></tr>
<tr><td>符号化</td><td><code>按高度字段给建筑图层分级设色</code></td></tr>
<tr><td>出图</td><td><code>把当前地图渲染成 PNG 存到 /tmp/map.png</code></td></tr>
</table>
<p class="dim">任务越具体越准：带上图层名、字段名、参数值与输出路径。</p>

<h2>⚙️ 模型配置要点</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
<tr><th style="width:76px;">项目</th><th>说明</th></tr>
<tr><td>支持的<br/>服务商</td>
    <td>DeepSeek / OpenAI / GLM / Gemini / MiMo，以及<b>任何 OpenAI 兼容接口</b>
        （含本地 llama.cpp、Ollama、LM Studio）</td></tr>
<tr><td>端点写法</td>
    <td>以 <code>/v1</code> 结尾，例如 <code>https://api.deepseek.com/v1</code>、
        <code>http://127.0.0.1:8080/v1</code></td></tr>
<tr><td>API Key</td>
    <td>本地 / 自托管服务不校验密钥，但<b>不能留空</b>（填任意占位符，如 <code>sk-local</code>）</td></tr>
<tr><td>模型名</td>
    <td>必须与服务端 <code>/v1/models</code> 列出的名字一致。llama.cpp 默认是启动参数
        <code>--model</code> 的文件名（或 <code>--alias</code> 指定的名字）</td></tr>
<tr><td>上下文</td>
    <td>插件每次请求都携带全部工具定义（约 2.6k token）。本地服务上下文别开太小：
        llama.cpp 建议 <code>--ctx-size 8192</code> 以上</td></tr>
<tr><td>工具调用</td>
    <td>所有 GIS 操作都依赖 function calling。llama.cpp 需启动时带 <code>--jinja</code>，
        且所用 GGUF 的 chat template 支持 tools</td></tr>
<tr><td>诊断</td>
    <td>「模型」页的<b>「测试连接与诊断」</b>会逐项检查：地址是否可达、模型名是否匹配、
        上下文是否够用、是否支持工具调用，并直接列出服务端可用的模型名</td></tr>
</table>

__CARD_LOCAL__

<h2>🔌 MCP 服务（可选）</h2>
<p>在独立的「<b>MCP</b>」页签里开启后，Claude Desktop、Cursor 等支持 MCP 的外部 Agent
可以直接驱动本 QGIS 执行 GIS 任务。</p>
<ul>
<li>点「复制客户端配置」拿到现成的 <code>mcpServers</code> JSON，<b>直接粘贴即可</b>——
    其中的 <code>command</code> 会自动换成本机确实能跑起来的 Python 解释器
    （不是 QGIS 主程序，那是个 GUI 程序，不会讲 MCP 协议）</li>
<li>仅在 <code>127.0.0.1</code> 上监听，并强制校验访问令牌，局域网其它机器连不上</li>
<li>端口与令牌写入 <code>~/.qgis_agent/mcp_session.json</code>（权限 0600），
    外部 MCP Server 会自动读取，通常无需手工配置</li>
<li>默认<b>不</b>随插件启动；涉及危险工具（如 <code>execute_pyqgis</code>）默认要求二次确认</li>
</ul>

<h2>❓ 常见问题</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
<tr><th style="width:96px;">现象</th><th>处理</th></tr>
<tr><td>请求失败</td>
    <td>对话里的错误卡片会给出<b>服务端原文</b>，点「复制报错详情」可复制给他人排查；
        点「诊断连接」逐项定位</td></tr>
<tr><td>本地模型<br/>别处能用<br/>这里不行</td>
    <td>最常见两个原因：① 服务端不支持工具调用（其它客户端只聊天，不发 tools）；
        ② 服务端上下文太小。<b>用「诊断连接」一次就能确认</b></td></tr>
<tr><td>连不上</td>
    <td>核对地址与端口；本地服务注意地址是否指向运行推理服务的那台机器；
        若接口在 TLS 握手阶段被网关重置，可在「模型」页开启「浏览器兼容 TLS」
        （需先 <code>pip install curl_cffi</code>）</td></tr>
<tr><td>图层加载失败</td>
    <td>用绝对路径；确认文件本身可被 QGIS 打开；路径含中文/空格时注意转义</td></tr>
<tr><td>执行到一半<br/>想停下</td>
    <td>点发送按钮旁的「停止」；长任务都在后台线程执行，界面不会卡死</td></tr>
<tr><td>插件无法加载</td>
    <td>需要 QGIS ≥ 3.22（Qt5 / Qt6 均已适配）</td></tr>
</table>

<h2>🛡️ 安全与隐私</h2>
<ul>
<li>生成的 PyQGIS 代码在执行前会做 AST 静态扫描，并弹窗请你确认；「跳过确认」需自行承担风险</li>
<li>对话记录、模型配置只存在本机；MCP 会话文件权限为 0600</li>
<li>你的任务描述与必要的项目上下文会发送给你配置的模型服务，请注意敏感数据</li>
</ul>

<h2>📋 内置工具（__TOOL_COUNT__ 个）</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
<tr><th style="width:34%;">工具</th><th>作用</th></tr>
__TOOLS_ROWS__
</table>

<h2>🔗 链接</h2>
<ul>
<li><a href="https://github.com/bunkmr/qgis-agent">GitHub 仓库</a> ·
    <a href="https://github.com/bunkmr/qgis-agent/issues">问题反馈</a></li>
<li><a href="https://qgis.org">QGIS 官网</a></li>
</ul>
<p class="dim">MIT License · Made by bunkmr ·
Inspired by SpatialAnalysisAgent (GIBD, Penn State University)</p>

</body>
</html>
"""

# 两处卡片内容单独出来，便于模板保持可读（也便于测试按关键词断言）
_TEMPLATE = _TEMPLATE.replace(
    "__CARD_TIP__",
    _card(
        '<b>✨ 支持的模型</b><br>'
        'DeepSeek / OpenAI / GLM / Gemini / MiMo，以及任何 OpenAI 兼容接口 —— '
        '包括 <code>llama.cpp</code>、<code>Ollama</code>、<code>LM Studio</code> 等本地服务。'
        '<br><span class="dim">模型配置、端点写法与本地服务的注意事项见下方「模型配置要点」。</span>'
    ),
)

_TEMPLATE = _TEMPLATE.replace(
    "__CARD_LOCAL__",
    _card(
        '<b>💡 用本地模型跑不出结果？先看这三条</b>'
        '<ol style="margin:4px 0; padding-left:18px;">'
        '<li>地址是否指向运行推理服务的那台机器（<code>127.0.0.1</code> 只指本机）</li>'
        '<li>模型名是否与服务端 <code>/v1/models</code> 完全一致</li>'
        '<li>服务端上下文是否够大、是否支持工具调用（llama.cpp 需 <code>--jinja</code>）</li>'
        '</ol>'
        '<span class="dim">点「模型」页的「测试连接与诊断」，以上四项会自动逐条检查并给出结论。</span>'
    ),
)
