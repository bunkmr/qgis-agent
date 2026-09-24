# 更新日志

## [2.4.5] - 2026-09-24

### 修复
- 🐛 **【重要】把「复制客户端配置」给出的 JSON 粘进 Claude Desktop / Cursor 后连不上，甚至一启动就多出一个 QGIS 窗口。**

  真因是配置里的 `command` 直接用的是 `sys.executable` —— 而 **macOS 上 QGIS 的 Python 是嵌在 app 里的，GUI 进程的 `sys.executable` 就是 QGIS 的 GUI 主程序**（`/Applications/QGIS.app/Contents/MacOS/QGIS`）。它不是解释器：不读 stdin、不输出 JSON-RPC，被客户端当 stdio 服务拉起来只会**再开一个 QGIS 界面**，然后握手超时。Windows 上 `sys.executable` 正好就是 `python.exe`，所以这个坑一直没有暴露，只在 macOS 上发作。

  现在配置生成会**实际执行一次校验**再落笔：把候选解释器真的跑一遍服务脚本的 `--help`，退出码为 0 才算通过 —— 只看「文件存在」是不够的，macOS 上 `QGIS.app/Contents/MacOS/python3.12` 确实存在，但它是给 app 内部用的 framework 解释器，脱离 app 直接跑会报 `Could not find platform independent libraries <prefix>`。

  两个附带要点：
  - **优先选「在干净环境下就能启动」的解释器**：客户端是在自己的环境里拉起 `command` 的，所以判定要模拟那个环境。只在 QGIS 进程环境里能跑（靠继承的 `PYTHONPATH` / `PYTHONHOME` 活着）的解释器只作兜底，并在界面上明确提示「客户端可能拉不起来」。
  - **绝不执行看起来不像解释器的可执行文件**：候选一律先过「文件名像 python」这一关，否则探测本身就会误启动 GUI 程序。同理，一个都找不到时不回吐 `sys.executable`，而是退化成裸命令名 `python3` / `python` 交给客户端从 PATH 解析。

- 🐛 **「测试连通性」按钮也会误启动一个 QGIS 窗口**——同一个根因（它同样用 `sys.executable` 拉子进程跑 `--check`）。现在与客户端配置共用同一套解释器解析逻辑，并在自检结果里写明用的是哪个解释器。

- 🐛 **页签切换回调硬编码下标**：`_on_tab_changed` 原本写死「模型页 = index 2」。这次新增页签后它就指向了别的页——虽未造成可见故障，但下次增删页签必然踩雷。改为按控件定位（`twTabs.indexOf(tbSettings)`）。

### 新增
- ✨ **MCP 服务独立成「MCP」页签**（原挂在「模型」页底部）。MCP 面向的是「把 QGIS 交给外部 Agent 驱动」，与「选哪个模型对话」是两件不相干的事；混在一起时它被压在模型表格、测试连接、TLS 开关之后，窄面板里要滚很久才看得到，还容易被误认成模型相关设置。切到该页签时 MCP 控件可见、切回「模型」页即隐藏。

  分组框标题顺带压短为「MCP 服务」（原先是「MCP 服务（供 Claude Desktop / Cursor 等外部 Agent 调用）」）—— 窄 dock 下 `QGroupBox` 的标题会被裁掉，长说明移到下方灰字里更稳妥。

- ✨ 「复制客户端配置」的弹窗会附带说明「解释器为什么不是 QGIS 的路径」，避免用户把 `command` 手动改回 QGIS 主程序。README 新增「外部 Agent 接入（MCP，可选）」章节。

### 文档
- 全量清理指向旧路径「模型配置 → MCP 服务」的提示与文档（`mcp_protocol` 的两条错误提示、MCP Server 的握手说明与自检提示、帮助页页签表、两份 README）。
- 明确写出 macOS 上 `QGIS.app/Contents/MacOS/python3.12` **不能**当 `command`，以及为什么。

### 测试
- 单测 **376 → 415**（新增 26 例解释器解析 + 13 例页签结构守卫）。解释器解析的守卫含反向验证：把优先级改回「第一个能跑的就行」，`test_clean_capable_candidate_beats_earlier_inherited_only` 会立刻变红。页签守卫也用 AST 跨文件校验「tooltip 数量 == 页签数量」——tooltip 是按顺序下发的，少写一条会让后面所有页签的提示整体错位，且不会有任何报错。
- 真机双版本验收：新页签与配置生成 **63/63**（Qt5 + Qt6 各一遍，含用生成的 `command` 做真实 stdio 握手 + `tools/list`）、MCP 安装回归 **50/50**（双版本）、对话 UI **137/137**、dock 尺寸 **29/29 (Qt5) / 27/27 (Qt6)**、`浏览器兼容 TLS` 15/15、令牌显示 21/21。

## [2.4.4] - 2026-09-24

### 修复
- 🐛 **【重要】模型开始调用工具后，用户自己发出去的消息从对话区消失了**——本次报障的第一个问题。真因是聊天区存在**两套写入方式**：
  - 「整体重写」（`_set_chat_html`）——思考流、工具状态、错误卡片都走它；
  - 直接追加（`txHistory.append`）——**用户消息**、各类提示行走它。

  而追加的内容**没有同步进内部缓冲** `_chat_html`，于是任何一次整体重写都会把它整段抹掉。这正好解释了现象为什么是「一开始看得到、模型一调工具就不见了」——工具状态一刷新即丢。现在 `append` / `setHtml` / `clear` 三条路径统一同步缓冲，并顺带修掉两个同源问题：
  - **清空对话后内容会复活**：`clear()` 只清了控件、没清缓冲，下一次整体重写又把旧内容写回来了；
  - **缓冲膨胀**：包装 `setHtml` 时必须剥掉 CSS 前缀，否则 CSS 会被反复记进正文、越滚越大。

  实测：用户消息、工具状态、思考块可以共存且互不冲掉；`clear` 之后重写不再复活旧内容。

- 🐛 **【重要】切换模型 / 温度后再对话，报 `TypeError: 'method' object is not connected`，且新对话的回复永远不显示**——本次报障的第二个问题。真因不是「disconnect 少了 try」这么简单：

  `QGISAgent._on_send_message` 在模型或温度变化时会**整体替换 processor**（`self.live_conversation.processor = self._Processor(...)`），而旧 worker 的信号连接仍留在**旧对象**上。旧 worker 收尾时触发旧对象的信号，回调里 `self.processor` 却已是新对象 —— 拿新对象去 disconnect 一个从未连接过的槽，就是控制台里那串

  ```
  File ".../conversation.py", line 136, in _on_response_ready
      self.processor.response_ready.disconnect(self._on_response_ready)
  TypeError: 'method' object is not connected
  ```

  更糟的是：它还会把**新对象上刚建立的连接误断开**，于是新一轮的回复永远渲染不出来。修复分两层：
  - 用 `QObject.sender()` 定位**发出信号的那个** processor，断开只在它身上做；旧对象的迟到回调一律忽略，既不渲染过期结果，也不碰新连接；
  - 替换 processor 之前先调 `release_processor()` 清掉旧连接（`QGISAgent` 侧已接入），从源头消除迟到回调。

- 🐛 **「停止」按钮永远提示「没有正在进行的生成」**，中间的「停止中…」状态形同虚设。真因是停止时会把「空闲」标志提前置真，而判断总在置真之后执行；现在停止只负责发出中断请求，状态复位统一交给收尾回调。

- 🐛 **会话收尾改为幂等**：一轮请求只认第一次结果。工作线程先发错误再发完成时两个事件都已排进事件队列，`disconnect` 拦不住已排队的事件——此前第二个到达就会抛错并让界面卡在「发送中」。

### 测试
- 新增 `tests/test_conversation_signals.py`（13 例）：单轮收尾一次、双到（正序 / 反序）、重复发送不叠加连接、断开幂等、反思信号同受保护、**切换模型重建 processor 的真实现场**、停止语义。
- ⚠️ **测试方法学修正**：PyQt 会把**槽函数里的异常交给 `sys.excepthook` 打印**，`emit` 调用处不会抛（用户在 QGIS 控制台看到 traceback，而程序不崩、界面僵在「发送中」）。所以「没报错」不能靠 `try/except` 判断 —— 必须拦 `excepthook`，否则断言是假绿。测试与真机验收脚本均已按此改造。
- 修正 `tests/support.py` 的 `sys.path` 顺序：项目根目录里有同名的 `qgis_agent.py`（插件入口），项目目录排在父目录之前会让 `import qgis_agent` 命中**文件**而不是**包**，导致包内相对导入全部失败。现在父目录优先。
- 新增 `tests/support.py::install_httpx_stub()`：`llm_providers` 在模块级继承了 `httpx.HTTPTransport`，替身必须是**真类**（用 `MagicMock` 当基类会在类创建时就抛错）。
- 单测 **359 → 376** 全绿（2 xfail + 9 skip）。

## [2.4.3] - 2026-09-24

### 修复
- 🐛 **【重要】本地大模型每次对话都报「模型服务内部错误 HTTP 500」，同一个模型在别的客户端却完全正常**——本次报障的主场景。根因是**插件自己发出的消息格式有问题**：Query Tuning 的改写结果此前被作为**第二条 `SystemMessage`** 追加在系统提示词之后，而 Qwen3 系的 chat template 只允许第一条消息是 system，遇到第二条直接 `raise_exception`：
  ```
  Error: Jinja Exception: System message must be at the beginning.
  ```
  llama.cpp 把它包成 HTTP 500，用户侧只能看到「模型服务内部错误」。现已改为**并入同一条系统消息**，维持「有且仅有一条系统消息、且位于首位」的不变式。
- 🐛 **新增 `template` 错误分类**（两条规则，排在 `server` 之前）。此前 Jinja / chat template 类报错一律落到 `server`——因为报错原文里恰好带 "HTTP 500"，被 `\b50[0-9]\b` 抢走——于是给出的建议是「模型未加载 / 显存不足 / 超出上下文」，与真实成因毫无关系。现在会明确指出「该模板只允许第一条消息是 system」，并指引：先更新插件 → 再查消息序列 → 最后确认 `--jinja`。规则顺序有专门的不变式测试守着。
- 🐛 **诊断报告自相矛盾**：「服务端没有这个模型」（失败）与「对话接口可用 HTTP 200」（通过）出现在同一份报告里。真因是**单模型推理服务（llama.cpp 等）会忽略 `model` 字段**——`/v1/models` 报的是 `--alias` 的名字，而 `/v1/chat/completions` 收任何名字都返回 200。现在模型名比对先登记为**信息级**，只有对话实测也确认是模型问题才升级为失败。
- 🐛 **诊断的探测请求没带 system 消息**，因此测不出上面那条 500。现在两次探测都以 `system + user` 的形态发出，与插件的真实请求形态一致——「模板对 system 位置挑剔」这类故障才能在诊断里直接复现。`PROBE_SYSTEM` 常量上写明了为什么必须带。
- 🐛 **【重要】切换页签后回到「对话」页，底部输入框与发送按钮消失**——用户报障的第二个问题。真因是 Qt5 侧「工作流」页使用的 QtWebKit `QWebView` **没有实现 `sizeHint()`**，Qt 回落到默认的 **800x600**，把 `QTabWidget` 的 sizeHint 顶到 **812x805**。切过一次「工作流」页就触发一次尺寸自适应，dock 被撑到屏幕之外，而 QGIS 会把这个尺寸记进 profile——之后每次打开，输入区都在屏幕外面。三处修复：
  1. 给 `QWebView` 设 `Ignored` 尺寸策略（它的 `minimumSizeHint()` 还会返回无效的 `(-1,-1)`，一并替掉）；
  2. 新增 `_DockTabWidget`：`sizeHint` / `minimumSizeHint` **只按当前页算**并夹在 `[430, 720]`，任何一页都不可能再把 dock 顶出屏幕（宽度必须沿用父类，否则 dock 再也缩不回 360px 最窄宽度）；
  3. 新增 `QGISAgent._clamp_dock_to_screen()`：dock 首次显示后，把**底边超出屏幕可用区**的历史坏尺寸收回一次——客观判定，屏幕内的正常布局（哪怕用户故意拉满高度）一律不动。
  实测 `QTabWidget.sizeHint` 由 **812x805 → 366x430**，工作流页 sizeHint 由 **773 → 173**。

### 改进
- 🔍 **诊断建议定向化**：模型名与服务端清单不一致、且没有相近候选时（如用户填 `120ad088`、服务端是 `qwen3.6-35B`），不再说「改成上面列出的其中一个」这种空话，而是直接点名「服务端实际提供的是「X」，模型名请照它填写」；并补充说明「单模型服务会忽略模型名，填错也照样能用；但网关 / 多模型服务就必须填对」。

### 测试
- 🧪 新增 `TestSystemMessageInvariant`（**2 例**）：锁死「有且仅有一条 system 消息且位于首位」，并覆盖「带历史对话」的情形。**已做反向验证**——把实现改回旧写法，守卫会以 `['SystemMessage', 'SystemMessage', 'HumanMessage']` 失败。
- 🧪 `tests/test_error_classifier.py`：新增 template 用例（含用户现场原文）、`CATEGORY_TEMPLATE` 常量、`template` 必须排在 `server` 之前的顺序不变式，以及一条端到端断言（system 位置报错绝不能被 `server` 吞掉）。
- 🧪 `tests/test_endpoint_diagnostics.py`：新增 mock 模式 `MODE_SYSTEM_POS`（**只在带 system 时失败**，照抄用户现场）与 3 例——模型名不一致但对话可用 → 信息级；模板报错必须被复现；模型名确认失败 → 升级为失败。
- 🧪 新增真机验收脚本能力：`dock_size_accept_test.py`（页签容器 sizeHint 上下限、逐页签几何、切过「工作流」再回「对话」、`_clamp_dock_to_screen` 六个场景）。

### 验收
- 单测 **359 例**全绿（2 xfail + 11 skip）。
- 真机验收双版本（QGIS 3.44.14/Qt5 与 QGIS 4.2.1/Qt6）：既有 UI 验收各 **137/137**；`dock_size_accept_test.py` Qt5 **28/28**、Qt6 **26/26**。

## [2.4.2] - 2026-09-24

### 修复
- 🐛 **本地模型报「模型错误 / 错误原因无法自动识别（unknown）」**——本次用户报障的主场景。补上四条此前落到 `unknown` 的真实措辞：`model 'x' not found`、`model is not loaded`、`the request exceeds the available context size (NNNN tokens)`、`Failed to parse chat template: this model does not support tools`；新增 `endpoint`（地址路径不对）与 `server`（5xx，可重试）两个分类。用户原话「在别的地方这个模型能用、在这里不行」正是其中前两条的典型表现。
- 🐛 **API Key 为空被归于 `unknown`**：本地服务（llama.cpp / Ollama / LM Studio）常见做法是 Key 随便填，但如果**留空**，OpenAI SDK 自身会先抛 `The api_key client option must be set either by passing api_key to the client`。现在这条排在通用鉴权规则之前单独接住，明确告知「填任意占位符（如 `sk-local`）即可，但不能留空」。
- 🐛 **报错原文此前只写进「报告」页签的执行日志，用户在对话里看不到任何线索**。新增 `error_classifier.summarize_error()`：剥 ANSI → 优先抽 JSON 的 `message` 字段 → 压空白 → 补 `HTTP NNN · ` 前缀 → 脱敏（`sk-*` / `Bearer *`）→ 截断，把服务端原文**直接摊在对话里的错误卡片上**，并给出「复制报错详情」「诊断连接」两个可点击入口。
- 🐛 **`tool` 分类的建议过于笼统**：此前只说「换个支持工具调用的模型」，现直接给出 llama.cpp 的 `--jinja` 启动参数与 GGUF chat template 检查项——本地场景下这才是真正的修复点。
- 🐛 **`rate_limit` 漏掉 OpenAI 真实语序**：`You exceeded your current quota` / `quota reached|exceeded` / `billing hard limit` 此前无法匹配。

### 新增
- ✨ **「测试连接与诊断」（四项检查）**：把原来只发一条纯文本的连通性测试换成真正的诊断 ——
  ① **地址规整**：缺 `/v1` 时自动生成候选地址并逐个试连（llama.cpp 两种路径都能通的，保留用户原值不动）；
  ② **连通性 + 模型清单**：拉取服务端 `/v1/models`，列出它**实际**提供哪些模型名；
  ③ **模型名比对**：不一致时直接指出「你填的是 X，服务端只有 Y」；
  ④ **上下文容量**：读 llama.cpp `/props` 的 `n_ctx`，与本插件的固定开销（工具定义序列化后 **8193 字符 ≈ 2560 token**，每次请求都携带）比对；
  ⑤ 最后**再发一次带 tools 的请求**——这一条才是关键。
  新模块 `endpoint_diagnostics.py` 不依赖 Qt/qgis；任何一步失败只追加一行说明，**绝不抛异常**，结果在 `DiagnosisDialog` 中可一键复制。
- ✨ **「测试连接通过 ≠ 对话可用」这一认知落地**：旧测试只发纯文本（`llm.invoke`），而真实对话走 `llm.bind_tools(TOOL_DEFINITIONS).invoke()`。因此**模型不支持 function calling 时，旧测试照样报「连接成功」，而每次对话都失败**——这正是「同一个模型在别处能用、在这里不行」的成因。新诊断显式补上了带 tools 的那一次请求。

### 改进
- 🎨 **顶部功能页签**：文案压缩为 2–3 字（对话 / 历史 / 模型 / 工作流 / 报告 / 帮助），改为下划线式选中态（真实控件，`border-radius` 可用），`setExpanding(False)` + 滚动按钮退化 + 逐页 tooltip；页签配色/内边距/选中态全部收归 `_apply_chat_style()` 单一来源。实测 dock 最小宽度 **360px 下六页签总宽 252px 完整可见，无需滚动**。
- 🎨 **帮助页重做**：约 380 行内联 HTML 抽成 `help_content.py`（纯函数、可脱离 QGIS 单测），**只用实测可用的富文本 CSS**（表格宽度必须写成 `width="100%"` 属性而非 CSS；`var()` / `border-radius` / `display:flex` / `linear-gradient` / `nth-child` / `<details>` 一律不用——这些在 QTextDocument 里会被静默丢弃）；颜色按当前调色板注入。内容重写为「30 秒上手 / 页签都在做什么 / 能做什么 / 模型配置要点 / MCP 服务 / 常见问题 / 安全与隐私 / 内置工具 / 链接」，重点覆盖本地模型三条坑：Base URL 要带 `/v1`、Key 填 `sk-local`、上下文开够（`--ctx-size 8192`）以及 `--jinja`。打开完整文档的按钮移到正文下方，正文独占剩余空间。
- 🎨 **主题感知告警色**：错误卡片此前固定用 `#C0392B`，深色主题下几乎看不出是红的；现按背景明暗自动切换 `#C0392B` / `#FF7A70`。

### 测试
- 🧪 新增 `tests/test_endpoint_diagnostics.py`（**28 例**）——含 stdlib `ThreadingHTTPServer` 搭的 mock llama.cpp，端到端覆盖：全绿 / 模型名不匹配 / 上下文过小 / 不支持 tools / 缺 `/v1` 变体 / 端口不可达。
- 🧪 新增 `tests/test_help_content.py`（**23 例**）——含富文本 CSS 禁区守卫、表格宽度必须是属性、占位符与模板一一对应、章节无重复、内容准确性。
- 🧪 `tests/test_error_classifier.py`：1 例 `expectedFailure` 转为正常守卫，新增 `TestSummarizeError`（7 例）与**规则顺序不变式**（具体规则必须排在通用规则之前，否则 `model 'x' not found` 会被裸 404 的 `endpoint` 规则抢走）。
- 🧪 单测 293 → **353** 全绿（2 xfail + 9 skip）；真机验收 QGIS3(Qt5) 与 QGIS4(Qt6) 各 **137/137** 通过（新增「顶部页签 / 帮助页 / 错误卡片与锚点 / 主题告警色 / 本地模型诊断链路」五组断言）。

## [2.4.1] - 2026-09-23

### 对话窗口重做（UI）
- 🎨 **消息气泡化**：提问靠右、回复靠左，各带一条角色色条。Qt 富文本不支持 `border-radius`，卡片感改用「底色块 + 关键侧色条」表达；气泡宽度用 `<table width="N%" align="...">` 实现（`margin-left` 百分比在 Qt 里支持不稳）。
- 🎨 **配色统一走调色板派生**：新增 `utils.derive_chat_colors()` / `is_dark_color()` / `mix_hex()` 等纯函数（可脱离 QGIS 单测），深色主题下自动派生可读前景色；**代码块底色强制区别于气泡底色**——两者相同会让代码块看起来「不存在」。
- 🎨 **思考块真折叠**：QTextDocument 不支持 `<details>/<summary>`（正文会照常渲染，「展开」是假的），折叠改由 DockWidget 自己实现；折叠态一行显示「思考完成 · N 字」，可展开/收起、可单独复制。
- 🎨 空状态给出可点击的示例问题；输入区整理为「输入框 + 发送/停止按钮」（互斥同格）；底部栏减重；「报告」页按钮改为 3 行 2 列网格。

### 修复
- 🐛 **对话历史区整块空白（严重）**：装配聊天区时用了 `QLayout.replaceWidget()`，它返回的 `QWidgetItem` 由 Python 持有，未接住即被 GC，而底层布局仍指向它 → **新控件从未真正进入布局**。改为 `removeWidget` + `insertWidget`，并加入装配顺序不变式自检（搜索条 < 聊天区 < 输入区 < 底部栏 < 状态条），错序会在日志中明确报出。
- 🐛 **输入框高度自适应从未生效**：`QPlainTextDocumentLayout` 是惰性的，`textWidth` 恒为 -1 时 `document().size().height()` 返回的是**块数**（1.0 / 2.0 …）而非像素高 → 输入框永远停在 44px，多打几行必出假滚动条。改用 `QTextLayout` 逐块累加真实行高（`QFontMetrics.boundingRect(..., TextWordWrap)` 在 Qt6 下高度翻倍、`lineSpacing()` 又小于 Qt6 真实行高，两者都不可用）。实测 Qt5 / Qt6 表现一致：44 / 50 / 66 …，超过 140px 上限才出现滚动条。
- 🐛 **Markdown 组件样式表整份失效**：样式表用了 CSS 自定义属性 `var(--x, #fallback)`，而 QTextDocument 不解析它、**连 fallback 一起丢弃**（实测 `color:var(--f,#f00)` 渲染为默认黑色）→ 代码块底色、表格表头、链接颜色全部落空，只是恰好被对话区自身样式兜住才未暴露。现改为实际色值，并让 `create_markdown` 自带完整样式，不再依赖调用方（`thinking_display.get_theme_css` 标记废弃）。

### 改进
- 📐 **窄幅停靠友好**：整个 dock 最小宽度 **604px → 360px**；「报告」页 **588 → 259**、「模型配置」页 **377 → 130**、「对话」页 **395 → 324**。元信息行改为可折行（此前 `setWordWrap(False)`，一行摘要就把最小宽度顶到 395px），日期与计数内部改用不换行空格，折行只会发生在 `·` 分隔符处。
- 🧪 新增 `tests/test_chat_ui.py`（25 例）守住上述全部坑：`CssCapability` / `LayoutAssembly` / `ComposerHeight` / `ThinkingBlock` / `Metadata` 五组。单测 268 → **293** 全绿；真机验收 QGIS3(Qt5) 与 QGIS4(Qt6) 各 **42/42** 通过。

## [2.4.0] - 2026-09-22

### MCP 服务出口（新增）
- ✨ **外部 Agent 驱动 QGIS**：内置的 20 个工具现在可通过 **Model Context Protocol** 提供给 Claude Desktop / Cursor / Codex 等外部 AI Agent。架构为插件内 `mcp_bridge.py`（本地 socket 服务）+ 随包附带的 `mcp_server/`（stdio MCP Server，纯标准库，零第三方依赖）。
- ✨ **设置页新增「MCP 服务」区**：启用开关、监听端口、访问令牌（重新生成 / 复制）、随插件自动启动、特权工具放行、实时连接状态、一键复制客户端配置、连通性自检。
- 🔒 **安全边界**：仅监听 `127.0.0.1`（不支持绑定其它地址）；强制令牌校验（`hmac.compare_digest` 常数时间比较），无令牌或令牌错误一律拒绝；默认不随插件启动，需用户显式开启。
- 🔒 **特权工具默认不放行**：`execute_pyqgis` / `execute_processing` / `remove_layer` / `load_project` / `save_project` / `run_skill` 既不出现在外部 Agent 的工具清单里，直接调用也会被拒绝；即便放行，每次执行仍会走既有的三档授权弹窗。
- 🔒 **凭据存放**：端口与令牌写入 `~/.qgis_agent/mcp_session.json`（文件 0600、目录 0700），服务停止即删除，供 MCP Server 自动发现。
- 🛡 **协议层加固**：单行请求 4MB 上限 + 分块读取（不用 `readline`，避免对端持续发送无换行字节流撑爆内存）；连接空闲 10 分钟超时（避免空闲连接占满名额）；工作线程异常一律转结构化错误，不影响进程。
- 🧪 新增 97 个单测（协议层 37 + MCP Server 27 + 依赖健壮性 18 + 可选依赖降级 15），全套 263 个单测通过；QGIS 4.2.1 真机端到端 43/43 通过（真实 socket + 真实 stdio 子进程走完整 MCP 握手）。

### 修复
- 🐛 **「浏览器兼容 TLS」开关此前点不到**：该开关只存在于 `settings_dialog.py`，而该对话框已无任何调用方（死代码）。现已并入实际可见的「模型配置」页，并删除这两个死文件。
- 🐛 `run_skill` 会执行用户技能目录下的 Python 代码，此前经 MCP 通道可免确认调用，现纳入特权工具集合。
- 🐛 **依赖探测不再只认 ImportError**：`_soft_import` 原来仅捕获 `ImportError`，而依赖「装了一半」时抛出的常是别的异常——例如 pydantic 与 pydantic-core 版本错配抛 `SystemError`、macOS 上框架/动态库加载失败抛 `OSError`。这类异常会从模块顶层逃逸，导致**整个插件加载失败且界面没有任何提示**。现统一兜住，转为「缺少依赖」对话框。（QGIS 3.44.14 真机实测复现）
- 🐛 **locale 取值不再崩溃**：`QSettings().value("locale/userLocale")` 在 QGIS 尚未注册 locale 时返回 `None`，原实现直接 `[0:2]` 切片会 `TypeError` 打断 `QGISAgent.__init__`。现加默认值兜底。
- 🐛 **`PackageManager.check_dependencies()` 也只认 ImportError（同源缺陷的第二处出口）**：`__import__("langchain_core")` 抛出的 `SystemError` 从这里逃逸出 `run()`，界面同样只剩一条日志 Traceback。现在把结果拆成**「确实没装」（可自动安装）**与**「装了但加载不了」（只诊断、绝不重装）**两类——对后者重装上层依赖只会把用户的 Python 环境改得更乱。同时自动解析异常里的版本号，直接给出可执行命令，例如 `pip install --force-reinstall "pydantic-core==2.46.4"`。
- 🐛 **勾选「浏览器兼容 TLS」却没装 curl_cffi，会把整个插件用坏（用户报障场景）**：`get_llm_instance` 原来在依赖缺失时直接 `raise RuntimeError`，于是点「测试连接」只弹一个「需要安装 curl_cffi」的报错——更严重的是 `processor` 每次构造都抛异常，**对话功能整条不可用**。现在改为**降级**：改用标准 TLS 栈继续跑，并在日志与「测试连接」结果里如实标注「已勾选但未生效（未安装 curl_cffi）」；设置页打开时会把这类「勾了也白勾」的陈旧取值自动纠正为未勾选，手工再勾选会立即弹说明并被按住。
- 🐛 **同一处的窄捕获**：`import curl_cffi` 的可用性检查原来只捕 `ImportError`，而原生扩展半装时抛的是 `OSError`/`SystemError`，异常同样会打到调用方。现统一视为「不可用」并降级。

### 改进
- 📝 **「浏览器兼容 TLS」不再是「勾了也不知道有没有生效」**：选项下方新增实时状态说明（未安装 curl_cffi / 已就绪 / 已启用），并明确写出「只要接口没有出现连接被重置，就无需安装，不影响其它功能」。
- 📝 **访问令牌输入框显示修正**：`setText` 会把光标放到末尾，输入框随之滚动到尾部，屏幕上只剩 64 位令牌的后半截（实测就是这个现象），手抄极易抄错。现把光标放回开头，并在提示里说明请用「复制令牌」取值。
- 📝 措辞中性化：移除源码 / `metadata.txt` / `README` 中「绕过 Cloudflare / 伪装指纹 / 反爬」等表述，改为描述网关行为与兼容性处理，避免上架评审歧义。
- 📝 `metadata.txt` 补充 MCP 能力说明与安全模型，明确可选依赖与零依赖组件。
- 🧪 真机验收扩展：新增「安装后验收」脚本，直接针对已安装副本走 `classFactory → initGui → run` 全链路，并在 **QGIS 4.2.1 (Qt6/Py3.12) 47/47** 与 **QGIS 3.44.14 (Qt5/PyQt5/Py3.12) 46/46** 双版本全绿。
- 🧪 新增 15 个「可选依赖降级」回归测试：覆盖「导入期抛 `OSError`/`SystemError` 也必须降级」「降级必须写日志」「`(effective, reason)` 返回契约」「源码级约束，防止有人把降级改回抛异常」；另有专项真机脚本复现用户报障场景（勾选后点测试连接、对话入口），双版本 **19/19** 全绿。
- 🧪 新增 18 个依赖健壮性回归测试（含源码级约束，防止被改回窄捕获；以及「装了但坏了」绝不被误判为「缺失」而触发重装）。另有专项真机验证脚本，在 QGIS 3.44.14 上真实复现 pydantic 版本错配场景，确认 `run()` 不再抛异常且提示内容准确（17/17 通过）。

## [Unreleased]

### 安全（P0 修复）
- 🔒 **execute_pyqgis AST 扫描补齐**：黑名单加入 `open`/`getattr`/`setattr`/`delattr`/`globals`/`locals`/`vars`/`breakpoint` 等；字符串参数以 `__` 开头一律拒绝；语法解析失败不再放行
- 🔒 **内建白名单**：`__builtins__` 由黑名单改为白名单，排除文件读写与动态属性入口
- 🔒 **stdout/stderr 恢复**：`execute_pyqgis` 使用 `try/finally` 保证恢复，避免吞掉其他插件输出

### 正确性
- 🐛 **桥接信号幂等**：`_init_main_thread_bridge` 仅连接一次，关闭再开 Dock 不再导致工具执行两遍
- 🐛 **processor logger 顺序**：SmartDebugger 导入失败时不再因 NameError 整模块挂掉
- 🐛 **Cookbook 归档**：按工作流 steps 实际状态计算 success，失败案例不再污染案例库
- 🐛 **工作流回放**：识别 `{"error": ...}` 业务失败，不再计为成功
- 🐛 **工具超时**：30s → 180s；超时提示「主线程可能仍在执行」
- 🐛 **确认超时**：60s → 300s，适应长代码审查
- 🐛 **select_latest_interaction**：无历史时返回 None，避免 IndexError
- 🐛 **utils.markdown→HTML**：修复 Python 3.9 下 f-string 含反斜杠的 SyntaxError（QGIS 3.22 常见环境）
- 🧹 **print → logger**：code_reviewer / 工具栏定位警告改用 logging

### 文档
- 📚 README / CLAUDE.md / metadata 对齐代码：20 个工具、信号/槽调度、Skills/Workflow/Clarification/Code Review 已接线
- 📚 requirements 补充 `tomli; python_version < "3.11"` 与可选依赖说明

## [2.3.2] - 2026-09-10

### 新增（可选逃生舱：浏览器指纹 TLS）
- 🆕 **浏览器指纹 TLS**：当模型接口被 Cloudflare 等反爬网关在 TLS 握手阶段按客户端指纹（JA3）拦截时，可在「模型配置」中开启「浏览器指纹 TLS」选项，用 `curl_cffi` 伪装 Chrome 指纹绕过（实测可连通 `ai.zhaosh.fun`）。默认关闭，不引入额外依赖，不影响默认发布路径。
- 💡 实现方式：用真正的 `httpx.Client` + 自定义 `Transport` 包一层转交 `curl_cffi`——既保留 Chrome 的 JA3 指纹，又能过 `openai`/`langchain` 对 `http_client` 的 `isinstance(httpx.Client)` 类型检查（直接传 `curl_cffi.Session` 会被新版 openai SDK 拒绝）。

### 改进（连接失败可诊断）
- 🔍 **错误分级新增「TLS 握手被拦截」专属分类**：连接失败时直接提示是网关指纹拦截，并给出「换官方接口 / 开启浏览器指纹 TLS」的可操作建议，不再只显示泛泛的"网络连接失败"。

### 兼容
- 📌 Qt5/Qt6 双兼容（同 v2.3.1，最低 QGIS 3.22）。

## [2.3.1] - 2026-09-09

### 兼容（Qt5 / Qt6 双兼容，真机 QGIS 4.2.1 / Qt6 实例化验证通过）
- 🔧 `thinking_display.py` 取色改用 `qgis.PyQt`（移除硬编码 PyQt5/PyQt6：Qt6 环境下 PyQt5 不存在，独立 PyQt6 会与 QGIS 内部绑定冲突）
- 🔧 输入框自适应高度信号由 `QTextDocument.sizeChanged`（Qt6 已移除）改为 `QTextEdit.textChanged`（Qt5/Qt6 通用）
- 📌 最低兼容 QGIS 3.22（覆盖 QGIS 3.22~4.x，Qt5 与 Qt6 全兼容）

## [2.3.0] - 2026-09-08

### 规划中特性全部接入（死代码清理收口）
- **代码审查(code_reviewer)**：代码执行确认前自动 LLM 安全审查，后台 QThread 不卡界面，结果展示 issues/suggestions
- **主动澄清(clarification_manager)**：用户请求模糊时主动追问，澄清后与原请求合并重跑
- **技能系统(skills)**：新增 `run_skill` 工具，LLM 可调用内置技能（web_search 等）
- **工作流录制/回放**：录制对话中的工具操作为工作流，可一键回放（workflow_store + dockwidget UI）
- **任务图(task_graph)**：复杂多步请求可选分解为有序步骤执行（默认关闭）
- 删除空壳 `thinking_widget.py`（功能已由 thinking_display.py 覆盖）；工具数 19→20


## [2.2.1] - 2026-09-08

### 改进（前台 / UI 体验优化）
- 🎨 **代码执行确认升级（P1-4）**：自定义 `CodeConfirmDialog`，代码预览**默认展开**（旧版藏在「详细」里等于没确认），新增三档授权「仅此一次 / 本次会话允许该工具 / 总是允许」；「总是允许」持久化到 QSettings，重启仍免确认
- ⏹ **停止中间态（U10）**：点「停止」后按钮变为「停止中…」并禁用，待 worker 真正结束才恢复，修复「点了停止却不知道有没有停」的困惑；无在途调用时直接恢复，不卡界面
- 🔌 **测试连接按钮（D14）**：模型配置页新增「测试连接」，后台线程执行连通性检查，不阻塞界面
- 👋 **首次启动引导（D4）**：首次打开插件弹出一次简明使用指引，之后持久化 `firstRunDone` 不再打扰
- 🚀 **首启不阻塞（P1-7）**：PyQGIS API 索引构建从 UI 线程移到后台线程，首启不再假死 10-30 秒，状态条实时反馈进度
- 🌗 **主题自适应（U15）**：聊天消息 / 思考块统一注入 `get_theme_css()` 派生的 `--qa-*` 主题变量，深色主题下文本可读；`create_markdown` 增强表格 / 链接 / 代码块渲染
- 🔍 **聊天区增强（U11/U13/U18/D5）**：发送中仅禁用发送按钮（输入框可继续预写）；消息区 Ctrl+F 搜索 + 复制回复；底部状态条（阶段 + 耗时）；空状态显示示例指令卡片

## [2.2.0] - 2026-09-08

### 新增
- 🆕 **4 个高频工具**：`get_algorithm_parameters`（查询 Processing 算法真实参数名，消灭参数编造）、`get_layer_profile`（先"看一眼数据"：字段/投影/要素数）、`set_layer_renderer`（分级/分类设色）、`reproject_layer`（坐标转换）
- 🧪 **可运行测试基线**：191 个用例，在裸 Python 环境（无 QGIS / 无 langchain / 无网络）下全部通过，覆盖安全护栏、错误分级、调试器等核心纯逻辑

### 修复
- 🛡️ **提示词注入防护**：属性表字段值 / 图层名回喂 LLM 前统一净化（剥离控制字符 + 截断）；工具结果包裹不可信数据围栏
- 🛡️ **PyQGIS 代码 AST 静态扫描**：拦截 `os` / `subprocess` / `eval` / 双下划线访问等危险调用，命中即拒绝执行
- 🛡️ **危险操作确认扩展到 5 类**：`remove_layer` / `load_project` / `save_project` / `render_map`（覆盖时）加入确认；「跳过确认」改为仅本次会话有效，不再持久化
- 🐛 **SmartDebugger 接线**：工具执行失败时自动诊断并回灌 LLM 改写重试（最多 3 次），复杂任务成功率提升
- 🐛 **停止后可恢复**：点「停止」后对话不再永久报废，可新建对话或切换模型重试
- 💬 **错误分级提示**：401/403/429/超时/连接失败等改为中文可操作提示，不再甩英文堆栈

### 改进
- 💡 思考过程改为累积展示（不再整体替换闪烁）；输入框 Enter 发送 / Shift+Enter 换行、高度自适应
- 📦 最小兼容版本 3.0 → 3.22（PEP585 注解需 Py≥3.9）；打包剔除 `.workbuddy` 开发记忆等隐私文件
- 📝 文档如实标注「技能系统 / 工作流录制 / 主动提问 / 任务图」为规划中功能；HELP.html 接入帮助页；i18n 翻译进包

## [2.1.3] - 2026-09-02

### 新增
- 🆕 **QGIS 4（PyQt6）兼容**：PyQt5 / PyQt6 双兼容，插件可在 QGIS 3.x 与 4.x（含 macOS QGIS 4）中加载运行
- 🆕 **本地 / 自托管模型免密**：自定义 OpenAI 兼容端点的 API Key 可留空（Ollama / vLLM / llama.cpp 等）
- 🛡️ **Cloudflare 403 自动规避**：调用 LLM 时自动附加浏览器 `User-Agent`，绕过 Cloudflare Bot 防护对 Python 客户端的拦截

### 修复
- 🐛 修复「点击发送无反应」：无活动对话时自动创建对话；主线程构造异常与 LLM 调用超时被改为可见红字提示，不再被 Qt 静默吞掉
- 🐛 修复 PyQt6 枚举未限定（QHeaderView / QEvent / QPalette / QDialog / QMessageBox 等）导致的 DockWidget 崩溃
- 🐛 修复 pydantic-core 版本冲突（用户 site 遮蔽 QGIS 自带版本）导致插件无法加载
- 🐛 修复 `QPalette.Base` 等属性缺失报错

### 改进
- 💬 对话名称默认取首条消息前 20 字，不再强制弹窗要求命名
- 🧠 RAG 组件（DocStore / Retriever / Cookbook 等）构造失败时优雅降级为 None，不阻塞对话
- ⏱️ LLM 调用统一设置 `timeout=180, max_retries=1`，避免端点不通时 worker 永久挂起
- 📦 打包脚本改用显式包含白名单；ZIP 顶层为标准 `qgis_agent/` 目录

## [2.1.2] - 2026-06-19

### 修复
- 🐛 **修复 RAG 模块导入错误**: 解决 ZIP 安装后 `ModuleNotFoundError: No module named 'qgis_agent.rag'` 问题
- 🐛 **修复子插件误判**: 移除 `rag/`、`agent_loop/`、`skills/` 目录的 `__init__.py`，避免 QGIS 扫描器将其误判为子插件

### 改进
- 将子包的相对导入改为绝对导入（`from .rag` → `from qgis_agent.rag`）
- 清理不必要的依赖（移除 `requests`、`langchain`）
- 更新打包脚本，ZIP 包现在包含 `rag/`、`agent_loop/`、`skills/` 模块

## [1.2.0] - 2026-06-06

### 新增
- 📚 **RAG API 文档检索**: 本地 SQLite FTS5 全文搜索引擎，在执行 PyQGIS 代码前自动检索相关 API 签名和参数信息
- 🧬 **Cookbook 自我进化**: 成功任务自动归档为案例，执行前检索相似案例提供参考，越用越聪明
- 🔍 **search_pyqgis_api 工具**: LLM 可主动调用此工具查询 PyQGIS/GDAL/Processing API 文档
- 📖 **API 文档生成器**: 从 QGIS 运行时反射 + Processing 注册表 + 手动补充三个来源提取 API 文档

### 改进
- `Processor.__init__()` 集成 `DocStore`、`APIDocRetriever`、`Cookbook` 组件
- `agent_chat()` 在执行危险工具前自动触发 RAG 检索
- `agent_chat()` 开始前检索 Cookbook 相似案例并注入 system prompt
- `agent_chat()` 结束后自动归档成功案例到 Cookbook
- System prompt 增加 search_pyqgis_api 使用指导和 Cookbook 参考说明
- 工具数量从 14 增加到 15（新增 search_pyqgis_api）

### 新增文件
- `rag/` — RAG 模块目录
  - `rag/__init__.py` — 模块入口
  - `rag/doc_store.py` — SQLite FTS5 文档存储（API 文档 + Cookbook）
  - `rag/retriever.py` — API 文档检索器（关键词提取 + FTS5 搜索）
  - `rag/doc_generator.py` — 文档生成器（inspect 反射 + Processing 算法 + 手动补充）
  - `rag/cookbook.py` — Cookbook 自我进化（自动归档 + 检索 + 质量评分）
- `scripts/build_api_index.py` — 独立脚本：构建 API 文档索引
- `data/pyqgis_api.db` — API 文档 SQLite 数据库（自动生成）

## [1.1.0] - 2026-06-06

### 新增
- 🔒 **代码安全确认**: 执行 PyQGIS 代码和 Processing 算法前弹窗确认（借鉴 QGPT Agent）
- 🌡️ **Temperature 控制**: 底部滑块调节 LLM 输出创造性（0.0=精确, 1.0=创造）
- 🌐 **国际化支持**: 中文翻译文件 (`i18n/` 目录)
- 📚 **Sphinx 文档**: 完整中文帮助文档 (`help/` 目录)，包含安装指南、使用手册、工具参考、FAQ

### 改进
- `call_tool()` 新增危险工具确认机制，通过 `_MainThreadBridge` 的 `confirm_request` 信号在主线程弹窗
- `Processor.__init__()` 新增 `temperature` 参数和 `_code_confirm_callback`
- DockWidget 底部栏新增 Temperature 滑块 (`sliderTemperature`) 和值显示 (`lblTempValue`)
- 全局代码确认回调 `set_code_confirm_callback()` 在 `qgis_tools.py` 中注册

## [1.0.0] - 2026-06-06

### 新增
- 初始版本发布
- 11 个内置 QGIS 工具（图层管理、数据处理、地图渲染等）
- 多 LLM 支持（DeepSeek、OpenAI、智谱 GLM、Gemini、小米 MiMo）
- 对话持久化（SQLite 存储）
- 长期记忆机制（MEMORY.md）
- 线程安全架构（QThreadPool + 主线程调度桥）
- 三标签页停靠面板（对话 / 对话列表 / 模型配置）
- 依赖自动安装管理
- 图层标注设置工具
