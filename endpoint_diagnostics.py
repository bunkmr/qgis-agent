# -*- coding: utf-8 -*-
"""端点连接诊断 —— 一次跑完「地址 / 模型名 / 上下文 / 工具支持」四项检查。

为什么单独抽一个模块：

    用户报「本地 llama.cpp 用不了，但同一个模型在别的客户端能用」时，真正要回答的
    问题不是「连不连得上」（发一次请求就知道了），而是「到底哪一项不匹配」。
    OpenAI 兼容生态里最容易踩的四件事恰好都不体现在「连接成功」上：

      1. Base URL 少了 /v1            → 404，表现为「服务地址不正确」
      2. 模型名与服务端不一致          → llama.cpp 报 model 'x' not found
      3. 服务端上下文开得太小          → 报 exceeds the available context size
      4. 模型/模板不支持工具调用        → 别的客户端（纯对话）能用，这里不能用

    第 4 条尤其典型：插件的「测试连接」只发一条纯文本消息，因此**测试通过 ≠ 对话可用**。
    本模块会额外发一次带 tools 的请求，把这条差异显式暴露出来。

设计约束：
    - 不 import Qt / qgis，可脱离 QGIS 单测；
    - httpx 延迟导入（本模块要保持「在任何环境都能 import」）；
    - 任何一步失败都不抛异常，只往报告里追加一行；每步各自带 timeout。
"""

import json
import re
import contextlib

try:  # 包内导入
    from .error_classifier import (
        CATEGORY_CONTEXT_LENGTH,
        CATEGORY_MODEL,
        CATEGORY_TOOL,
        CATEGORY_UNKNOWN,
        classify_error,
        summarize_error,
    )
except ImportError:  # 顶层模块导入（`unittest discover -s tests` 场景）
    from error_classifier import (  # type: ignore
        CATEGORY_CONTEXT_LENGTH,
        CATEGORY_MODEL,
        CATEGORY_TOOL,
        CATEGORY_UNKNOWN,
        classify_error,
        summarize_error,
    )

# 报告条目级别
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_FAIL = "fail"
LEVEL_INFO = "info"

_LEVEL_TAG = {
    LEVEL_OK: "[通过]",
    LEVEL_WARN: "[注意]",
    LEVEL_FAIL: "[失败]",
    LEVEL_INFO: "[信息]",
}

# 一次请求最多列出多少个服务端模型名（llama.cpp 只有 1 个，网关可能有几百个）
MAX_LISTED_MODELS = 8

# 插件单次请求里工具定义部分的大小（字符），用于估算上下文占用
_ESTIMATE_CHARS_PER_TOKEN = 3.2

# 探测工具调用时用的最小工具集合（只用来观察服务端是否接受 tools 字段）
MINIMAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "qa_probe",
            "description": "诊断用探针，无副作用",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string", "description": "任意字符串"}},
                "required": ["value"],
            },
        },
    }
]

# 探测对话时带上的系统消息 —— **必须带**。
# 插件的每一次真实请求都以 SystemMessage 开头，而 Qwen3 系等 chat template 对
# system 的位置很挑剔（出现第二条 system 时直接 raise）。不带 system 的探测
# 复现不了这类故障：实测用户的现场就是——诊断报「对话接口可用 HTTP 200 /
# 工具调用可用」，真实对话却每次都 500（Error: Jinja Exception: System message
# must be at the beginning.）。
PROBE_SYSTEM = "你是 QGIS 助手。这是一次连接自检，请只回复 pong。"


# ──────────────────────────────────────────────────────────────
# 纯函数部分（不依赖 httpx，可直接单测）
# ──────────────────────────────────────────────────────────────
def normalize_endpoint(endpoint):
    """规整用户填写的 Base URL。

    返回 (primary, candidates, notes)：
      - primary    : 规整后的地址（补协议、去尾部斜杠）
      - candidates : 待尝试的地址列表。用户没写 /v1 时，会把「原样」与「+ /v1」都列上
                     —— llama.cpp / Ollama 两种写法都能通，但网关通常只认 /v1。
      - notes      : 规整过程中发现的、值得告诉用户的问题（list[str]）
    """
    notes = []
    raw = (endpoint or "").strip()
    if not raw:
        return "", [], ["未填写服务地址（Base URL）。"]

    # 去掉尾部的斜杠，避免拼出 //chat/completions
    raw = raw.rstrip("/")

    if not re.match(r"^https?://", raw, re.IGNORECASE):
        notes.append("地址缺少 http:// 或 https:// 前缀，已按 http:// 处理。")
        raw = "http://" + raw

    # 本地地址提示：127.0.0.1 指的是「运行 QGIS 的这台机器」
    host = raw.split("://", 1)[1].split("/")[0]
    if host.startswith(("127.0.0.1", "localhost")):
        notes.append(
            "地址指向本机（%s）。若推理服务跑在另一台机器上，请改成那台机器的 IP。" % host
        )

    candidates = [raw]
    if not re.search(r"/v\d+(/|$)", raw):
        candidates.append(raw + "/v1")
        notes.append("地址未包含版本路径（如 /v1），将同时尝试「原样」与「追加 /v1」。")

    return raw, candidates, notes


def estimate_tokens(char_count):
    """按字符数粗略估算 token 数（中英混排取 3.2 字符/token 的经验值）。"""
    try:
        return int(max(0, char_count) / _ESTIMATE_CHARS_PER_TOKEN)
    except Exception:
        return 0


def plugin_request_scale():
    """估算本插件单次请求的固定开销（工具定义部分）。

    返回 (chars, est_tokens)。取不到工具定义时返回 (0, 0)。
    """
    try:
        try:
            from .qgis_tools import TOOL_DEFINITIONS
        except ImportError:
            from qgis_tools import TOOL_DEFINITIONS  # type: ignore
        chars = len(json.dumps(TOOL_DEFINITIONS, ensure_ascii=False))
    except Exception:
        return 0, 0
    return chars, estimate_tokens(chars)


def _pick_closest(model, available):
    """从服务端给出的模型名里挑一个和用户填写最接近的，用于「你是不是想填这个」。"""
    if not model or not available:
        return ""
    target = str(model).strip().lower()
    # 完全一致（忽略大小写）优先
    for name in available:
        if name.strip().lower() == target:
            return name
    # 其次：含 tag / 版本后缀的包含关系（ollama 的 qwen3:8b 与 qwen3 互为包含）
    for name in available:
        low = name.strip().lower()
        if target in low or low in target:
            return name
    # 再次：去掉 -_. 等分隔符后比对
    norm = lambda s: re.sub(r"[-_.:\s]", "", s.lower())  # noqa: E731
    for name in available:
        if norm(name) == norm(model):
            return name
    return ""


# ──────────────────────────────────────────────────────────────
# HTTP 探测部分
# ──────────────────────────────────────────────────────────────
def _build_client(timeout, browser_tls=False):
    """构造 httpx 客户端；不可用时返回 (None, 原因)。

    代理策略与 llm_providers.get_llm_instance 保持一致：显式关闭系统代理，
    因为代理会让本地地址解析失败。
    """
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        return None, "httpx 不可用：%s" % exc

    transport = None
    if browser_tls:
        try:
            try:
                from .llm_providers import _CurlTransport
            except ImportError:
                from llm_providers import _CurlTransport  # type: ignore
            transport = _CurlTransport(impersonate="chrome")
        except Exception:  # noqa: BLE001
            transport = None  # curl_cffi 不可用则退回标准栈，不阻断诊断

    try:
        if transport is not None:
            return httpx.Client(transport=transport, timeout=timeout), ""
        try:
            return httpx.Client(proxy=None, timeout=timeout), ""
        except TypeError:
            return httpx.Client(proxies={}, timeout=timeout), ""
    except Exception as exc:  # noqa: BLE001
        return None, "无法创建 HTTP 客户端：%s" % exc


def _headers(api_key):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    return headers


def _body_snippet(response, limit=200):
    """把响应体压成一行短文本，优先抽出 message 字段。"""
    try:
        text = response.text or ""
    except Exception:
        return ""
    text = text.strip()
    if not text:
        return ""
    summary = summarize_error(text)
    return summary[:limit] if summary else text[:limit]


def _get_json(client, url, api_key, timeout):
    """GET 一个 JSON 接口。返回 (status, payload, snippet, error_text)。"""
    try:
        resp = client.get(url, headers=_headers(api_key))
        status = resp.status_code
        payload = None
        if status == 200:
            try:
                payload = resp.json()
            except Exception:
                payload = None
        return status, payload, _body_snippet(resp), ""
    except Exception as exc:  # noqa: BLE001
        return None, None, "", str(exc)


def _post_chat(client, base, api_key, model, timeout, with_tools=False,
               with_system=True):
    """向 {base}/chat/completions 发一条最小请求。返回 (status, snippet, error_text)。

    with_system 默认为 True：插件的真实请求总是以 SystemMessage 开头，探测也
    必须跟上，否则「模板不接受 system」这类故障永远测不出来（详见 PROBE_SYSTEM）。
    """
    messages = []
    if with_system:
        messages.append({"role": "system", "content": PROBE_SYSTEM})
    messages.append({"role": "user", "content": "ping"})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4,
        "stream": False,
        "temperature": 0,
    }
    if with_tools:
        payload["tools"] = MINIMAL_TOOLS
        payload["tool_choice"] = "auto"
    try:
        resp = client.post(
            base.rstrip("/") + "/chat/completions",
            headers=_headers(api_key),
            json=payload,
        )
        return resp.status_code, _body_snippet(resp), ""
    except Exception as exc:  # noqa: BLE001
        return None, "", str(exc)


def _models_url(base):
    return base.rstrip("/") + "/models"


def _props_url(base):
    """llama.cpp 的 /props 挂在根路径（不带 /v1），返回含 n_ctx 的运行时信息。"""
    try:
        scheme, rest = base.split("://", 1)
        host = rest.split("/", 1)[0]
        return "%s://%s/props" % (scheme, host)
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────
def diagnose(provider, model, api_key, endpoint, timeout=8, browser_tls=False):
    """跑完四项检查，返回结构化报告。

    返回 dict：
        ok          : bool    是否整体可用（连接通 + 模型名匹配 + 工具可用）
        headline    : str     一句话结论
        base_url    : str     实际可用的 Base URL（空字符串表示没找到）
        provider    : str
        model       : str
        checks      : list[dict] 每项 {level, title, detail}
        suggestions : list[str]  可执行的下一步建议
    """
    result = {
        "ok": False,
        "headline": "",
        "base_url": "",
        "provider": provider or "",
        "model": model or "",
        "n_ctx": 0,
        "model_mismatch": False,
        "checks": [],
        "suggestions": [],
    }
    checks = result["checks"]
    suggestions = result["suggestions"]

    def add(level, title, detail=""):
        checks.append({"level": level, "title": title, "detail": detail})

    if not str(endpoint or "").strip():
        add(LEVEL_FAIL, "未填写服务地址", "请先在「模型配置」中填写 Base URL。")
        result["headline"] = "未填写服务地址，无法诊断。"
        return result
    if not str(model or "").strip():
        add(LEVEL_FAIL, "未填写模型名", "请先在「模型配置」中填写模型名称。")
        result["headline"] = "未填写模型名，无法诊断。"
        return result

    client, client_err = _build_client(timeout, browser_tls=browser_tls)
    if client is None:
        add(LEVEL_FAIL, "无法发起诊断请求", client_err)
        result["headline"] = client_err or "无法发起诊断请求。"
        return result

    try:
        _run_checks(client, provider, model, api_key, endpoint, timeout,
                    add, suggestions, result)
    except Exception as exc:  # noqa: BLE001 - 诊断自身绝不向上抛
        add(LEVEL_INFO, "诊断中断", str(exc))
    finally:
        with contextlib.suppress(Exception):
            client.close()

    _finalize(result)
    return result


def _run_checks(client, provider, model, api_key, endpoint, timeout,
                add, suggestions, result):
    """实际的四步检查。拆出来是为了让 diagnose 的异常兜底更清晰。"""
    provider = provider or "Custom"

    # ── 第 0 步：地址规整 ──
    primary, candidates, notes = normalize_endpoint(endpoint)
    for note in notes:
        add(LEVEL_INFO, "地址规整", note)

    # ── 第 1 步：连通性 + 模型清单（顺带确定哪个候选地址可用）──
    working_base = ""
    available_models = []
    last_failure = ""
    for base in candidates:
        status, payload, snippet, err = _get_json(client, _models_url(base), api_key, timeout)
        if err:
            last_failure = "%s 不可达：%s" % (base, err)
            continue
        if status == 200 and isinstance(payload, dict):
            working_base = base
            for item in payload.get("data") or []:
                name = (item or {}).get("id") if isinstance(item, dict) else None
                if name:
                    available_models.append(str(name))
            add(LEVEL_OK, "服务可达", "GET %s/models → HTTP 200" % base)
            if base != primary:
                add(LEVEL_INFO, "已自动修正地址",
                    "原地址 %s 不可用，实际可用的是 %s（补上了版本路径）。"
                    % (primary, base))
                _add_suggestion(suggestions,
                    "建议把「模型配置」里的地址改成 %s，避免每次请求都要试探。" % base)
            break
        last_failure = "%s 返回 HTTP %s%s" % (
            base, status, ("：" + snippet) if snippet else "")
        if status is not None:
            add(LEVEL_WARN, "模型清单接口异常", last_failure)

    if not working_base:
        add(LEVEL_FAIL, "服务不可达",
            last_failure or "所有候选地址都无法访问（/models 未返回 200）。")
        _add_suggestion(suggestions,
            "确认推理服务已启动、端口正确，并检查地址是否可以从本机访问"
            "（例如在浏览器打开 %s/models）。" % (candidates[0] if candidates else ""))
        # 地址不通时不再发对话请求：只会得到同一条连接错误，把报告读法弄乱
        _probe_chat(client, "", api_key, model, timeout, add, suggestions, result,
                    reachable=False)
        return

    result["base_url"] = working_base

    # ── 第 2 步：模型名比对 ──
    # ⚠️ 这里**不立刻下"失败"结论**，只登记为「信息」。
    #    原因：单模型推理服务（llama.cpp 等）的 model 字段其实是**被忽略**的
    #    —— /v1/models 报的是 --alias 的名字，而 /v1/chat/completions 收任何
    #    名字都返回 200。此时若把它判成失败，报告就会自相矛盾：第 2 项说
    #    「服务端没有这个模型」、第 4 项却是「对话接口可用 HTTP 200」，
    #    用户会以为诊断坏了（实测用户现场就是这个）。定性推迟到第 4 步，
    #    按对话实测结果决定是否升级为失败。
    model_check_title = ""
    if available_models:
        shown = "、".join(available_models[:MAX_LISTED_MODELS])
        if len(available_models) > MAX_LISTED_MODELS:
            shown += "…（共 %d 个）" % len(available_models)
        if model in available_models:
            add(LEVEL_OK, "模型名匹配", "服务端提供 %s" % shown)
        else:
            closest = _pick_closest(model, available_models)
            result["model_mismatch"] = True
            model_check_title = "模型名与服务端清单不一致"
            add(LEVEL_INFO, model_check_title,
                "你填的是「%s」，服务端 /v1/models 报的是：%s" % (model, shown))
            if closest:
                _add_suggestion(suggestions,
                    "把模型名改成「%s」（服务端实际使用的名字）。" % closest)
            else:
                # 没有相近候选时不能说「改成上面列出的其中一个」——太空泛，
                # 用户面对一长串名字还是不知道填哪个。llama.cpp 这类单模型
                # 服务只有一个名字，直接点名即可。
                _add_suggestion(
                    suggestions,
                    "服务端实际提供的是「%s」，模型名请照它填写。" % available_models[0])
            _add_suggestion(suggestions,
                "单模型推理服务（llama.cpp 等）通常会忽略模型名，填错也照样能用；"
                "但若你的地址后面是网关 / 多模型服务，就必须填对，否则会选错模型。")
    else:
        add(LEVEL_INFO, "未取到模型清单",
            "/models 返回 200 但没有 data 字段，可能是网关屏蔽了该接口。")

    # ── 第 3 步：llama.cpp 上下文长度 ──
    props_url = _props_url(working_base)
    if props_url:
        status, payload, _snippet, _err = _get_json(client, props_url, api_key, timeout)
        n_ctx = 0
        if status == 200 and isinstance(payload, dict):
            n_ctx = payload.get("n_ctx") or 0
            if not n_ctx:
                gen = payload.get("default_generation_settings") or {}
                if isinstance(gen, dict):
                    n_ctx = gen.get("n_ctx") or 0
        if n_ctx:
            result["n_ctx"] = int(n_ctx)
            chars, est_tokens = plugin_request_scale()
            detail = "服务端上下文 n_ctx = %d token" % n_ctx
            if est_tokens:
                detail += "；本插件单次请求仅工具定义就约 %d token（另加系统提示与历史）" % est_tokens
            if est_tokens and est_tokens > n_ctx * 0.8:
                add(LEVEL_FAIL, "上下文长度不足", detail)
                _add_suggestion(suggestions,
                    "把服务端上下文调大（llama.cpp 用 --ctx-size / -c，"
                    "建议至少 %d）；否则每次请求都会被服务端拒绝。"
                    % max(8192, est_tokens * 3))
            else:
                add(LEVEL_INFO, "上下文长度", detail)

    # ── 第 4 步：纯对话 + 工具调用 ──
    _probe_chat(client, working_base, api_key, model, timeout, add, suggestions, result,
                model_check_title=model_check_title)


def _relabel(checks, title, level):
    """把某一项检查的级别改成 level（用于「先登记、后定性」的检查项）。

    模型名比对就属于这一类：单看清单下不了结论（单模型服务会忽略该字段），
    必须等对话实测结果出来才能定性。
    """
    if not title:
        return False
    for check in checks or []:
        if check.get("title") == title:
            check["level"] = level
            return True
    return False


def _category_of(snippet):
    """取报错原文的归因分类（永不抛异常）。"""
    if not snippet:
        return None
    try:
        return (classify_error(snippet) or {}).get("category")
    except Exception:  # noqa: BLE001
        return None


def _add_suggestion(suggestions, text):
    """去重后追加建议（同一条建议重复出现只会稀释重点）。"""
    text = (text or "").strip()
    if text and text not in suggestions:
        suggestions.append(text)


def _suggest_from_error(snippet, result, suggestions, skip_categories=()):
    """按服务端返回的原文给出针对性建议。

    复用 error_classifier 的规则：诊断报告里那句建议应该跟着**真实报错**走，
    而不是固定写「模型名不匹配」—— 报错是上下文不足时那句话就是误导。
    skip_categories 用来避免和前面已经给出的定向建议重复（例如模型名不匹配已在
    第 2 步明确指出，这里就不必再贴一遍通用说明）。
    """
    if not snippet:
        return
    try:
        info = classify_error(snippet)
    except Exception:  # noqa: BLE001
        return

    category = info.get("category")
    if category == CATEGORY_UNKNOWN or category in skip_categories:
        return

    n_ctx = result.get("n_ctx") or 0
    if category == CATEGORY_CONTEXT_LENGTH and n_ctx:
        chars, est_tokens = plugin_request_scale()
        _add_suggestion(
            suggestions,
            "服务端上下文只有 %d token%s，明显不够：请调大后重启服务"
            "（llama.cpp 用 --ctx-size / -c，Ollama 用 num_ctx，LM Studio 改 Context Length）。"
            % (n_ctx, ("，而本插件单次请求仅工具定义就约 %d token" % est_tokens)
               if est_tokens else ""),
        )
        return

    hint = info.get("hint") or ""
    if hint:
        _add_suggestion(suggestions, hint)


def _probe_chat(client, base, api_key, model, timeout, add, suggestions, result,
                reachable=True, model_check_title=""):
    """发两次请求：不带 tools（模拟「测试连接」）与带 tools（模拟真实对话）。

    两次结果必须分开看 —— 这是本模块存在的核心理由：**纯对话通过不等于插件可用**。
    插件的所有 GIS 操作都走工具调用，模型/模板不支持 tools 时，别处（普通聊天客户端）
    完全正常，这里每次都失败。
    """
    if not reachable:
        add(LEVEL_INFO, "对话探测已跳过", "服务地址不通，先解决连接问题再复测。")
        result["ok"] = False
        return

    # 4a 纯对话（已带 system 消息，与插件真实请求形态一致）
    skip = (CATEGORY_MODEL,) if result.get("model_mismatch") else ()
    status, snippet, err = _post_chat(client, base, api_key, model, timeout)
    chat_ok = status == 200
    if chat_ok:
        add(LEVEL_OK, "对话接口可用",
            "POST %s/chat/completions → HTTP 200" % base.rstrip("/"))
    elif err:
        add(LEVEL_FAIL, "对话接口不可用", "请求异常：%s" % err)
        _add_suggestion(suggestions, "检查服务日志，确认推理服务是否正常加载了模型。")
    else:
        # snippet 已含 "HTTP NNN · ..."，不再另加状态码前缀，避免 "HTTP 400：HTTP 400"
        add(LEVEL_FAIL, "对话接口返回错误", snippet or "HTTP %s" % status)
        # 只有报错**确实是模型名问题**时，才把第 2 步登记的「信息」升为失败。
        # 报错另有原因时保留信息级 —— 否则又会出现「一口咬定模型名错、
        # 真实原因在别处」的误导（用户现场就是这种）。
        if result.get("model_mismatch") and _category_of(snippet) == CATEGORY_MODEL:
            _relabel(result.get("checks"), model_check_title, LEVEL_FAIL)
        _suggest_from_error(snippet, result, suggestions, skip_categories=skip)

    # 4b 带工具（真实对话走的是这条路径）
    status_t, snippet_t, err_t = _post_chat(
        client, base, api_key, model, timeout, with_tools=True)
    if status_t == 200:
        add(LEVEL_OK, "工具调用可用", "服务端接受 tools 字段。")
    elif err_t:
        add(LEVEL_WARN, "工具调用未能验证", "请求异常：%s" % err_t)
    elif snippet_t and snippet_t == snippet:
        # 与纯对话同一个错：不必把同一条报错和同一类建议再说一遍
        add(LEVEL_INFO, "工具调用同样失败", "与上一条原因相同。")
    else:
        add(LEVEL_FAIL, "工具调用不可用",
            "带 tools 的请求返回 %s" % (snippet_t or ("HTTP %s" % status_t)))
        _add_suggestion(
            suggestions,
            "本插件的 GIS 操作全部依赖工具调用（function calling）。"
            "若服务端不支持，请确认 llama.cpp 启动时带 --jinja、且所用 GGUF 的 chat template "
            "支持 tools；或换一个支持工具调用的模型。",
        )
        _suggest_from_error(snippet_t, result, suggestions,
                            skip_categories=skip + (CATEGORY_TOOL,))

    result["ok"] = bool(chat_ok and status_t == 200)


def _finalize(result):
    """根据 checks 生成一句话结论。"""
    checks = result["checks"]
    fails = [c for c in checks if c["level"] == LEVEL_FAIL]
    warns = [c for c in checks if c["level"] == LEVEL_WARN]

    if not fails and not warns:
        result["headline"] = "连接、模型名、上下文与工具调用四项全部通过。"
    elif fails:
        result["headline"] = "发现 %d 个问题：%s" % (
            len(fails), "；".join(c["title"] for c in fails[:3]))
    else:
        result["headline"] = "基本可用，但有 %d 处需要注意：%s" % (
            len(warns), "；".join(c["title"] for c in warns[:3]))
    result["ok"] = result["ok"] and not fails


def format_report(result):
    """把结构化报告渲染成可直接展示 / 复制的纯文本。"""
    lines = []
    lines.append("结论：%s" % (result.get("headline") or "（无）"))
    lines.append("服务商：%s        模型：%s"
                 % (result.get("provider") or "-", result.get("model") or "-"))
    lines.append("可用地址：%s" % (result.get("base_url") or "（未找到可用的 Base URL）"))
    lines.append("")
    lines.append("检查明细：")
    for check in result.get("checks") or []:
        tag = _LEVEL_TAG.get(check.get("level"), "·")
        detail = check.get("detail") or ""
        lines.append("  %s %s%s" % (tag, check.get("title", ""),
                                    ("　" + detail) if detail else ""))
    suggestions = result.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append("建议：")
        for idx, item in enumerate(suggestions, 1):
            lines.append("  %d. %s" % (idx, item))
    return "\n".join(lines)
