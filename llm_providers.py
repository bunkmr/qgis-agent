import os

import httpx
from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek


def get_llm_instance(provider, model, api_key, endpoint, temperature=0):
    # 创建一个不使用系统代理的 httpx client，避免代理导致 DNS 解析失败
    # httpx 0.24.0+ 使用 proxies 参数（字典格式）
    try:
        http_client = httpx.Client(proxy=None)
    except TypeError:
        # httpx 新版本使用 proxies 参数
        http_client = httpx.Client(proxies={})

    # 部分网关（如 Cloudflare 代理的本地模型）会按 User-Agent 做 Bot 防护，
    # 给 Python SDK 的请求返回 403 "Your request was blocked"。
    # openai SDK 的 default_headers 属性中 **_custom_headers 会覆盖自带 UA，因此这里能生效。
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    # 统一加请求超时与最小重试，避免端点不可达时线程永久挂起（表现=发送后永远无回复）
    llm_timeout = 180
    llm_retries = 1
    if provider == "DeepSeek":
        return ChatDeepSeek(
            model=model,
            api_key=api_key,
            temperature=temperature,
            http_client=http_client,
            timeout=llm_timeout,
            max_retries=llm_retries,
            default_headers=browser_headers,
        )
    # 其他所有 provider（GLM, XiaomiMiMo, Gemini, OpenAI, Custom 等）都走 OpenAI 兼容接口
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        openai_api_base=endpoint,
        temperature=temperature,
        http_client=http_client,
        timeout=llm_timeout,
        max_retries=llm_retries,
        default_headers=browser_headers,
    )


def get_default_api_key(provider):
    env_map = {
        "GLM": "GLM_API_KEY",
        "DeepSeek": "DEEPSEEK_API_KEY",
        "XiaomiMiMo": "XIAOMI_API_KEY",
        "Gemini": "GEMINI_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
    }
    return os.getenv(env_map.get(provider, ""), "")
