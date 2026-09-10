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
    - 返回的 dict 键固定：category / title / message / hint / action / retryable。
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
CATEGORY_TLS = "tls_blocked"
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
            # TLS 握手阶段被网关重置 / 拦截：Cloudflare 等反爬网关按客户端指纹（JA3）
            # 直接 RST 非浏览器的 TLS 栈（httpx / curl 都在握手阶段失败），UA 伪装无效。
            r"start_tls",
            r"SSL_ERROR_SYSCALL",
            r"TLS\s+handshake",
            r"handshake\s+(error|failed|reset)",
            r"ssl[\s_-]*reset",
        ],
        "接口在 TLS 握手阶段被拦截",
        "模型接口在 TLS 握手阶段被对端重置了连接。这通常不是网络断开，而是 Cloudflare 等反爬网关按客户端指纹（JA3）拦掉了非浏览器请求——普通 httpx / curl 的 TLS 栈会在握手时被直接 RST。",
        "建议：① 优先换成官方接口（如 https://api.deepseek.com/v1），最稳妥；② 若必须使用当前网关，可在「模型配置」设置页开启「浏览器指纹 TLS」选项（需额外 pip install curl_cffi）绕过该拦截。",
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
            r"上下文(长度|过长|超出)|超出(最大)?上下文",
        ],
        "对话内容过长",
        "本次请求携带的历史对话过多，已超出模型的上下文长度上限。",
        "请新建一个对话重新开始，或在设置中减少携带的历史消息条数后再试。",
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
            r"模型不存在|不支持该模型",
        ],
        "模型不可用",
        "所选择的模型在服务端不存在，或当前账号无权使用该模型。",
        "请在「设置 / 模型配置」中核对模型名称（含版本后缀），或换用一个可用模型。",
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
        "请在「设置 / 模型配置」中换用支持工具调用的模型后重试。",
        ACTION_SWITCH_MODEL,
        False,
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
        "本次请求未能完成，错误原因无法自动识别。",
        "请重试一次；若反复失败，可在「设置 / 模型配置」中检查服务地址与 API Key，或换用其他模型。",
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
