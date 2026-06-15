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

    if provider == "DeepSeek":
        return ChatDeepSeek(
            model=model,
            api_key=api_key,
            temperature=temperature,
            http_client=http_client,
        )
    # 其他所有 provider（GLM, XiaomiMiMo, Gemini, OpenAI, Custom 等）都走 OpenAI 兼容接口
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        openai_api_base=endpoint,
        temperature=temperature,
        http_client=http_client,
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
