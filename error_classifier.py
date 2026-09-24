# -*- coding: utf-8 -*-
"""
错误分类器模块

用途：
    把大模型 / 网络调用抛出的原始错误文本（traceback、HTTP 状态、SDK 报错等）
    归类为面向终端用户的中文提示，供 `qgis_agent.py` 在发生响应错误时展示：

        from .error_classifier import classify_error
        info = classify_error(str(err))

设计约束：
    - 纯 Python，只依赖 `re` 与 `typing`，不 import 任何 Qt / qgis 模块，
      这样该模块可以被独立引入并做单元测试；
    - `classify_error` 永不抛异常，内部异常一律退化为 unknown 分类；
    - 返回的 dict 键固定：category / title / message / hint / action / retryable；
    - 除分类外还提供 `summarize_error` / `extract_status_code`：把原始报错提炼成
      一句能直接展示的说明，避免用户只看到「错误原因无法自动识别」。
"""

import re
from typing import Optional

# ── 分类常量 ──
CATEGORY_AUTH = "auth"
CATEGORY_PERMISSION = "permission"
CATEGORY_RATE_LIMIT = "rate_limit"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_CONNECTION = "connection"
CATEGORY_CONTEXT_LENGTH = "context_length"
CATEGORY_MODEL = "model"
CATEGORY_TOOL = "tool"
CATEGORY_TEMPLATE = "template"
CATEGORY_TLS = "tls_blocked"
CATEGORY_ENDPOINT = "endpoint"
CATEGORY_SERVER = "server"
CATEGORY_UNKNOWN = "unknown"

# ── 可执行动作常量 ──
ACTION_OPEN_SETTINGS = "open_settings"
ACTION_RETRY = "retry"
ACTION_SWITCH_MODEL = "switch_model"

# Cloudflare 拦截特征，用于给出网关访问控制/UA 白名单的提示
_CLOUDFLARE_PATTERN = re.compile(
    r"cloudflare|cf-ray|cf_ray|error\s*1020|attention\s+required",
    re.IGNORECASE,
)

# 分类规则表：按「从具体到宽泛」顺序匹配，命中第一条即返回
# 每条规则: (分类, 正则列表, 标题, 说明, 建议, 动作, 可重试)
_RULES = [
    (
        # 必须排在通用 auth 之前：这里给出的建议是「填个占位符，但不能留空」，
        # 而通用 auth 的建议是「检查密钥是否正确」，对本地/自托管端点完全是误导。
        CATEGORY_AUTH,
        [
            r"api_?key\s+client\s+option\s+must\s+be\s+set",
            r"must\s+be\s+set\s+either\s+by\s+passing\s+api_?key",
            r"(缺少|未填写|没有)\s*api\s*key",
        ],
        "没有填写 API Key",
        "本次请求缺少 API Key。OpenAI 兼容协议要求该字段必须存在（服务端通常并不校验它的内容）。",
        "请在「模型配置」中为该模型填写 API Key：使用本地 / 自托管服务"
        "（llama.cpp、Ollama、LM Studio 等）时填任意占位符即可（例如 sk-local），但**不能留空**。",
        ACTION_OPEN_SETTINGS,
        False,
    ),
    (
        CATEGORY_AUTH,
        [
            r"\b401\b",
            r"invalid[\s_-]*api[\s_-]*key",
            r"incorrect[\s_-]*api[\s_-]*key",
            r"unauthorized",
            r"authentication",
            r"invalid[\s_-]*token",
            r"api[\s_-]*key[\s_-]*(is\s+)?(invalid|missing|empty)",
            r"密钥(无效|错误|缺失|为空)",
            r"未授权|鉴权失败|认证失败",
        ],
        "API Key 无效",
        "大模型服务拒绝了本次请求：API Key 不正确或已失效。",
        "请打开「设置 / 模型配置」，检查该模型的 API Key 是否填写正确、是否过期或被吊销。",
        ACTION_OPEN_SETTINGS,
        False,
    ),
    (
        CATEGORY_PERMISSION,
        [
            r"\b403\b",
            r"permission\s+denied",
            r"forbidden",
            r"your\s+request\s+was\s+blocked",
            r"access\s+denied",
            r"没有权限|权限不足|访问被拒绝",
        ],
        "没有访问权限",
        "大模型服务拒绝了本次请求：当前账号或来源没有访问该模型的权限。",
        "请确认该模型已在服务商后台开通；若使用自建网关，请检查网关的访问控制 / UA 白名单。",
        ACTION_OPEN_SETTINGS,
        False,
    ),
    (
        CATEGORY_RATE_LIMIT,
        [
            r"\b429\b",
            r"rate[\s_-]*limit",
            r"quota[\s_-]*exceed",
            r"too\s+many\s+requests",
            r"requests\s+per\s+(minute|second)",
            r"insufficient[\s_-]*quota",
            # OpenAI 真实文案是 "You exceeded your current quota"，quota 在 exceed 之后，
            # 与上面「quota 紧邻 exceed」的顺序相反（旧版因此落到 unknown）。
            r"exceed\w*\s+(your\s+)?(current\s+)?quota",
            r"quota[\s_-]*(reached|exceeded)",
            r"billing\s+(hard\s+)?limit",
            r"余额不足|欠费",
            r"限流|配额(不足|已用尽)|请求过于频繁",
        ],
        "请求过于频繁",
        "大模型服务限制了调用频率：短时间内请求太多，或套餐配额已用尽。",
        "请稍等片刻后重试；若持续出现，请检查账号余额与套餐额度，或换用额度充足的模型。",
        ACTION_RETRY,
        True,
    ),
    (
        CATEGORY_TIMEOUT,
        [
            r"timeout",
            r"timed\s+out",
            r"read\s+timeout",
            r"connect\s+timeout",
            r"deadline\s+exceed",
            r"请求超时|连接超时|读取超时",
        ],
        "请求超时",
        "本次请求在等待模型响应时超时，没有拿到完整结果。",
        "可换一个响应更快的模型，或把长任务拆成几步再试；网络较慢时也可直接重试。",
        ACTION_SWITCH_MODEL,
        True,
    ),
    (
        CATEGORY_TLS,
        [
            # TLS 握手阶段被网关重置：部分 API 网关会依据客户端 TLS 指纹判断请求来源，
            # 非浏览器的 TLS 栈（httpx / curl）可能在握手阶段就被中断。
            r"start_tls",
            r"SSL_ERROR_SYSCALL",
            r"TLS\s+handshake",
            r"handshake\s+(error|failed|reset)",
            r"ssl[\s_-]*reset",
        ],
        "接口在 TLS 握手阶段被中断",
        "模型接口在 TLS 握手阶段被对端重置了连接。这通常不是网络断开，而是网关依据客户端 TLS 指纹判断请求来源，中断了非浏览器客户端——普通 httpx / curl 会在握手阶段就失败。",
        "建议：① 优先换成官方接口（如 https://api.deepseek.com/v1），最稳妥；② 若必须使用当前网关，可在「模型配置」设置页开启「浏览器兼容 TLS」选项（需额外 pip install curl_cffi），改用浏览器 TLS 栈重试。",
        ACTION_OPEN_SETTINGS,
        False,
    ),
    (
        CATEGORY_CONNECTION,
        [
            r"connection\s+refused",
            r"\bdns\b",
            r"name\s+or\s+service\s+not\s+known",
            r"network\s+(is\s+)?unreachable",
            r"max\s+retries\s+exceeded",
            r"connection\s+(aborted|reset|error)",
            r"failed\s+to\s+(establish|resolve|connect)",
            r"temporary\s+failure\s+in\s+name\s+resolution",
            r"连接被拒绝|无法(连接|解析)|网络不可达",
        ],
        "网络连接失败",
        "无法连接到模型服务，可能是网络不通、代理未生效或服务地址填写有误。",
        "请检查网络与代理设置，并确认「设置 / 模型配置」里的服务地址（Base URL）正确无误。",
        ACTION_OPEN_SETTINGS,
        True,
    ),
    (
        CATEGORY_CONTEXT_LENGTH,
        [
            r"context[\s_-]*length",
            r"maximum\s+context",
            r"too\s+many\s+tokens",
            r"token[\s_-]*limit",
            r"reduce\s+the\s+length",
            r"context[\s_-]*window",
            # llama.cpp / 自托管推理服务的措辞：上下文叫 "context size"，且是被请求超出
            # （"the request exceeds the available context size (4096 tokens)"）。
            r"available\s+context",
            r"context[\s_-]*size",
            r"exceeds?\s+the\s+(available\s+)?context",
            r"n_ctx",
            r"上下文(长度|过长|超出|上限|大小)|超出(最大)?上下文",
        ],
        "对话内容过长",
        "本次请求的上下文超出模型上限：要么携带的历史对话过多，要么服务端的上下文窗口本身开得太小。",
        "① 先新建一个对话重新开始，减少携带的历史消息；"
        "② 若使用本地推理服务（llama.cpp / Ollama / LM Studio 等），很可能是服务端上下文设得太小 —— "
        "插件每次都会携带全部工具定义，请把服务端上下文调大后重试"
        "（llama.cpp：--ctx-size / -c；Ollama：num_ctx；LM Studio：Context Length）。",
        None,
        False,
    ),
    (
        CATEGORY_MODEL,
        [
            r"model\s+not\s+found",
            r"does\s+not\s+exist",
            r"unsupported\s+model",
            r"no\s+such\s+model",
            r"invalid\s+model",
            r"unknown\s+model",
            # llama.cpp 的两种真实措辞（实测均会落到 unknown）：
            #   {"message":"model 'qwen3' not found"}      ← 名字与服务端 --alias 不一致
            #   {"message":"model is not loaded"}          ← 服务端没加载成功
            r"model\s+['\"][^'\"]+['\"]\s+not\s+found",
            r"model\s+is\s+not\s+loaded",
            r"no\s+model\s+loaded",
            r"model\s+not\s+loaded",
            r"failed\s+to\s+load\s+(the\s+)?model",
            r"模型(不存在|未加载|未找到|加载失败)|不支持该模型",
        ],
        "模型不可用",
        "所选择的模型在服务端不存在、未加载成功，或当前账号无权使用该模型。",
        "请在「模型配置」中核对模型名称（含版本后缀）。"
        "使用本地 / 自托管服务时，模型名必须与服务端「/v1/models」列出的名称完全一致："
        "llama.cpp 默认用启动参数 --model 的文件名（或 --alias 指定的名字），"
        "ollama 是 ollama list 里的 NAME（不含 :latest 也能通过，但带 tag 更稳妥）。"
        "也可点击「测试连接与诊断」，插件会直接列出服务端实际提供的模型名。",
        ACTION_OPEN_SETTINGS,
        False,
    ),
    (
        CATEGORY_TOOL,
        [
            r"tool[\s_-]*call",
            r"function[\s_-]*calling",
            r"does\s+not\s+support\s+tools",
            r"structured\s+output",
            r"tool[\s_-]*use",
            r"不支持(工具|函数调用)|工具调用失败",
        ],
        "模型不支持工具调用",
        "当前模型未能正确处理工具调用（函数调用 / 结构化输出），导致任务无法继续。",
        "本插件的 GIS 操作全部依赖工具调用（function calling），模型不支持就无法工作。"
        "使用 llama.cpp 本地服务时，请确认启动参数带 --jinja、且所用 GGUF 的 chat template "
        "支持 tools；Ollama / LM Studio 请换用支持工具调用的模型后重试。",
        ACTION_SWITCH_MODEL,
        False,
    ),
    (
        # 排在最前（先于 endpoint / server）：这条报错的成因非常具体 ——
        # messages 里出现了**第二条** system 消息 —— 给出定向建议远比通用模板
        # 说明有用。实测原文（llama.cpp + Qwen3 系模板）：
        #   HTTP 500 · ... {{- raise_exception('System message must be at
        #   the beginning.') }} ^ Error: Jinja Exception: System message must
        #   be at the beginning.
        # 关键点：它**含 "HTTP 500"**，若 template 规则晚于 server 规则，
        # 就会被 `\b50[0-9]\b` 抢走，然后给用户一句「模型未加载/显存不足」的
        # 完全不对路的建议 —— 这正是用户报障时的现场。
        CATEGORY_TEMPLATE,
        [
            r"system\s+message\s+must\s+be\s+at\s+the\s+beginning",
            r"(系统|system)\s*消息.{0,8}(开头|最前)",
        ],
        "消息格式不被模型接受",
        "服务端套用 chat template 时报错：该模板只允许**第一条**消息是 system，"
        "本次请求里出现了位置不对的系统消息。这属于消息格式问题，"
        "既不是服务端故障，也不是模型不可用。",
        "旧版插件会把 Query Tuning 的改写结果作为第二条 system 消息发出，"
        "遇到此错时**先更新插件**（新版本已改为并入同一条系统消息）。"
        "更新后若仍出现，请检查发给模型的消息序列里是否夹带了第二条 system 消息"
        "（Qwen3 系模板会直接拒绝），并确认 llama.cpp 启动时带 --jinja。",
        ACTION_RETRY,
        True,
    ),
    (
        # 通用模板兜底：上一类没接住时，只要报错里出现 Jinja / template 就等于
        # 「模板与请求格式不匹配」，绝不能落进 server 那套「显存不足」的说辞。
        CATEGORY_TEMPLATE,
        [
            r"jinja",
            r"raise_exception",
            r"chat[\s_-]*template",
            r"failed\s+to\s+parse\s+(the\s+)?template",
            r"template[\s_-]*(error|exception)",
            r"模板(错误|解析失败|异常)",
        ],
        "模型模板不兼容",
        "服务端套用 chat template（对话模板）时报错：模型自带的模板不接受"
        "本次请求的消息格式。这不是服务端故障，也不是网络问题。",
        "使用 llama.cpp 本地服务时，请确认启动参数带 --jinja"
        "（否则特殊 token 的处理可能与模板不一致），"
        "或改用与该模板匹配的模型；Ollama / LM Studio 可换用官方推荐的模型版本。",
        ACTION_SWITCH_MODEL,
        False,
    ),
    (
        # 排在 model / tool 之后：`model 'x' not found` 必须由 model 规则先接住，
        # 这里的裸 404 只用来兜住「地址路径不对」这一类。
        CATEGORY_ENDPOINT,
        [
            r"file\s+not\s+found",
            r"\b404\b",
            r"not\s+found",
            r"cannot\s+(post|get)\s+/",
            r"不存在的(路径|地址|接口)|地址(错误|不正确)",
        ],
        "服务地址不正确",
        "请求的地址在服务端不存在（HTTP 404）：Base URL 的路径很可能不对。",
        "请检查「模型配置」里的地址。OpenAI 兼容接口通常需要以 /v1 结尾，例如"
        " http://127.0.0.1:8080/v1（llama.cpp / Ollama / LM Studio 都是这个写法）。"
        "可点击「测试连接与诊断」自动确认哪个地址可用。",
        ACTION_OPEN_SETTINGS,
        False,
    ),
    (
        CATEGORY_SERVER,
        [
            r"error\s+code:\s*5\d\d",
            r"\b50[0-9]\b",
            r"internal\s+server\s+error",
            r"server_error",
            r"bad\s+gateway",
            r"service\s+unavailable",
            r"gateway\s+time-?out",
            r"服务端(内部)?错误|服务器内部错误",
        ],
        "模型服务内部错误",
        "模型服务返回了服务器错误（HTTP 5xx）：问题出在服务端，不是你的请求格式。",
        "请查看服务端日志。本地推理服务（llama.cpp / Ollama / LM Studio 等）"
        "最常见的三种原因是：模型未加载、显存不足、请求超出上下文长度；稍后重试也可能恢复。",
        ACTION_RETRY,
        True,
    ),
]

# 预编译正则，避免每次调用重复编译
_COMPILED_RULES = [
    (category, [re.compile(p, re.IGNORECASE) for p in patterns], title, message, hint, action, retryable)
    for category, patterns, title, message, hint, action, retryable in _RULES
]

# Cloudflare 拦截时的补充建议（追加到 permission 分类的 hint 后面）
_CLOUDFLARE_HINT = "若使用自建网关，请检查网关的访问控制 / UA 白名单是否放行了本插件的请求。"


def _build_result(category: str, title: str, message: str, hint: str,
                  action: Optional[str], retryable: bool) -> dict:
    """统一构造返回结果，保证键集合固定"""
    return {
        "category": category,
        "title": title,
        "message": message,
        "hint": hint,
        "action": action,
        "retryable": bool(retryable),
    }


def _unknown_result() -> dict:
    """兜底分类"""
    return _build_result(
        CATEGORY_UNKNOWN,
        "请求失败",
        "本次请求未能完成，插件无法自动识别错误原因（原始报错见下方，可据此排查）。",
        "请先看下方的原始报错；也可点击「测试连接与诊断」，插件会直接检查地址、"
        "模型名、上下文长度与工具支持四项是否匹配。",
        None,
        False,
    )


def classify_error(error_text: str) -> dict:
    """
    将错误文本归类为面向终端用户的中文提示

    Args:
        error_text: 原始错误信息（通常是 str(err)）

    Returns:
        dict，键固定为：
            category  : str  ∈ {"auth","permission","rate_limit","timeout","connection",
                                "context_length","model","tool","unknown"}
            title     : str  简短中文标题
            message   : str  一句话中文说明
            hint      : str  可操作的下一步建议
            action    : str|None ∈ {"open_settings","retry","switch_model",None}
            retryable : bool
    """
    try:
        text = error_text if isinstance(error_text, str) else str(error_text)
        if not text.strip():
            return _unknown_result()

        for category, patterns, title, message, hint, action, retryable in _COMPILED_RULES:
            for pattern in patterns:
                if pattern.search(text):
                    # Cloudflare 拦截场景补充网关排查建议
                    if category == CATEGORY_PERMISSION and _CLOUDFLARE_PATTERN.search(text):
                        hint = f"{hint}（{_CLOUDFLARE_HINT}）"
                    return _build_result(category, title, message, hint, action, retryable)

        return _unknown_result()
    except Exception:
        # 分类器本身绝不抛异常，任何意外都退化为 unknown
        return _unknown_result()


# ──────────────────────────────────────────────────────────────
# 原始报文提炼
#
# 分类规则再全也有兜不住的时候（各家网关的措辞千奇百怪）。此前遇到未收录的报错，
# 用户只看得到「错误原因无法自动识别」，真正的信息藏在「报告」页签的日志里 ——
# 等于逼用户自己去翻。这里把原始报错里最有价值的那一句提出来，让界面直接展示。
# ──────────────────────────────────────────────────────────────

# 各家 SDK / 网关都把真正的说明放在 JSON 的 message 字段里
_JSON_MESSAGE_RE = re.compile(r"""['"]message['"]\s*:\s*(['"])(.*?)\1""", re.DOTALL)
# openai SDK 会把 HTTP 状态码拼在错误文本最前面："Error code: 404 - {...}"
_STATUS_RE = re.compile(r"error\s+code:\s*(\d{3})", re.IGNORECASE)
_STATUS_RE_LOOSE = re.compile(r"\b([45]\d\d)\b")
# 兜底提取时顺手抹掉 "'code': 404," 这类键名残留，只留下人话。
# ⚠️ 只对**看起来像 JSON / py-dict** 的文本启用：否则会把
# `openai.APIConnectionError: Connection error.` 里的类名一起吃掉。
_KEYNAME_RE = re.compile(r"""['"]?\w+['"]?\s*:\s*""")
_JSONISH_RE = re.compile(r"""[{,]\s*['"]\w+['"]\s*:""")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# 提示里绝不允许出现形似密钥的片段（错误报文经常把 Authorization 头一起吐回来）
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{6,})", re.IGNORECASE
)
_WS_RE = re.compile(r"\s+")

SUMMARY_MAX_LEN = 240


def extract_status_code(error_text) -> Optional[int]:
    """从原始错误文本里抽出 HTTP 状态码；抽不到返回 None。永不抛异常。"""
    try:
        text = error_text if isinstance(error_text, str) else str(error_text)
    except Exception:
        return None
    match = _STATUS_RE.search(text)
    if match:
        return int(match.group(1))
    match = _STATUS_RE_LOOSE.search(text)
    if match:
        return int(match.group(1))
    return None


def summarize_error(error_text, max_len: int = SUMMARY_MAX_LEN) -> str:
    """把原始报错提炼成一句可直接展示给用户的说明。

    输入通常是 langchain/openai 抛出的整段文本，例如：

        Error code: 404 - {'error': {'code': 404,
                          'message': "model 'qwen3' not found",
                          'type': 'not_found_error'}}

    输出：

        HTTP 404 · model 'qwen3' not found

    处理顺序：剥 ANSI 控制符 → 优先取 JSON message 字段 → 否则抹掉键名与状态码前缀
    → 压空白 → 脱敏 → 截断。任何异常都返回空串，绝不抛。
    """
    try:
        text = error_text if isinstance(error_text, str) else str(error_text)
    except Exception:
        return ""
    if not text.strip():
        return ""

    text = _ANSI_RE.sub(" ", text)

    message = ""
    match = _JSON_MESSAGE_RE.search(text)
    if match:
        message = match.group(2)
        # SDK 的 repr 里会带转义（\\n、\\'），去掉反斜杠让文案干净
        message = (message.replace("\\n", " ").replace("\\t", " ")
                          .replace("\\'", "'").replace('\\"', '"'))

    if not message:
        # 没有 JSON message 字段：退化为「抹掉结构符号后的全文」
        message = _STATUS_RE.sub(" ", text)
        if _JSONISH_RE.search(message):
            message = _KEYNAME_RE.sub(" ", message)

    message = _WS_RE.sub(" ", message).strip().strip("{}[]()'\",;:-").strip()
    if not message:
        return ""

    status = extract_status_code(text)
    if status is not None and str(status) not in message:
        message = "HTTP %d · %s" % (status, message)

    message = _SECRET_RE.sub("[已隐去]", message)

    if max_len and len(message) > max_len:
        message = message[:max_len].rstrip() + "…"
    return message
