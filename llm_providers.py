import os
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek


class ChatGLM(ChatOpenAI):
    def __init__(self, model: str = "glm-4", api_key: Optional[str] = None, **kwargs):
        api_key = api_key or os.getenv("GLM_API_KEY", "")
        super().__init__(
            model=model,
            openai_api_key=api_key,
            openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
            **kwargs
        )


class ChatXiaomiMiMo(ChatOpenAI):
    def __init__(self, model: str = "MiMo", api_key: Optional[str] = None, **kwargs):
        api_key = api_key or os.getenv("XIAOMI_API_KEY", "")
        super().__init__(
            model=model,
            openai_api_key=api_key,
            openai_api_base="https://api.xiaomi.com/v1/chat/completions",
            **kwargs
        )


class ChatGemini(ChatOpenAI):
    def __init__(self, model: str = "gemini-2.0-flash", api_key: Optional[str] = None, **kwargs):
        api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        super().__init__(
            model=model,
            openai_api_key=api_key,
            openai_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
            **kwargs
        )


LLM_PROVIDERS = {
    "GLM": {
        "class": ChatGLM,
        "models": ["glm-4", "glm-4v", "glm-4-plus", "glm-4-air", "glm-4-flash"],
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/",
        "env_key": "GLM_API_KEY",
    },
    "DeepSeek": {
        "class": ChatDeepSeek,
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "endpoint": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "XiaomiMiMo": {
        "class": ChatXiaomiMiMo,
        "models": ["MiMo", "MiMo-Pro"],
        "endpoint": "https://api.xiaomi.com/v1/chat/completions",
        "env_key": "XIAOMI_API_KEY",
    },
    "Gemini": {
        "class": ChatGemini,
        "models": ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-pro"],
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GEMINI_API_KEY",
    },
    "OpenAI": {
        "class": ChatOpenAI,
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        "endpoint": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
}


def get_llm_instance(provider: str, model: str, api_key: str, temperature: float = 0):
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"不支持的LLM提供者: {provider}")

    config = LLM_PROVIDERS[provider]
    llm_class = config["class"]

    if provider == "DeepSeek":
        return llm_class(model=model, api_key=api_key, temperature=temperature)
    elif provider in ("GLM", "XiaomiMiMo", "Gemini"):
        return llm_class(model=model, api_key=api_key, temperature=temperature)
    else:
        return llm_class(model=model, openai_api_key=api_key, temperature=temperature)


def get_default_api_key(provider: str) -> str:
    config = LLM_PROVIDERS.get(provider)
    if config:
        return os.getenv(config["env_key"], "")
    return ""
