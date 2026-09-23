import os

import httpx
from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek
import logging
logger = logging.getLogger(__name__)


class _CurlTransport(httpx.HTTPTransport):
    """httpx 兼容 Transport：把请求转交给 curl_cffi，改用其自带的浏览器 TLS 栈。

    为什么不直接把 curl_cffi.Session 当 http_client 传给 openai/langchain：
    新版 openai SDK 对 http_client 做 isinstance(httpx.Client) 严格类型检查，
    curl_cffi.Session 不是 httpx.Client 子类，会直接抛 TypeError。
    用一个真正的 httpx.Client + 自定义 Transport 包一层，既能过类型检查，又能走 curl_cffi 的 TLS 栈。
    """

    def __init__(self, impersonate="chrome", **kwargs):
        super().__init__(**kwargs)
        from curl_cffi import Session
        self._session = Session(impersonate=impersonate)

    def handle_request(self, request):
        cf = self._session.request(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
            data=request.content,
        )
        headers = httpx.Headers(cf.headers)
        # curl_cffi 已自动解压，但会保留 Content-Encoding 头，httpx 会二次解压报错，
        # 因此这里剥掉编码/长度相关头，避免重复解码。
        for _k in ("content-encoding", "transfer-encoding", "content-length"):
            headers.pop(_k, None)
        return httpx.Response(
            status_code=cf.status_code,
            headers=headers,
            content=cf.content,
            request=request,
        )


class _AsyncCurlTransport(httpx.AsyncHTTPTransport):
    """异步版本，对应 _CurlTransport。"""

    def __init__(self, impersonate="chrome", **kwargs):
        super().__init__(**kwargs)
        from curl_cffi import AsyncSession
        self._session = AsyncSession(impersonate=impersonate)

    async def handle_async_request(self, request):
        cf = await self._session.request(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
            data=request.content,
        )
        headers = httpx.Headers(cf.headers)
        for _k in ("content-encoding", "transfer-encoding", "content-length"):
            headers.pop(_k, None)
        return httpx.Response(
            status_code=cf.status_code,
            headers=headers,
            content=cf.content,
            request=request,
        )


def browser_tls_available():
    """「浏览器兼容 TLS」的可选依赖 curl_cffi 是否真的可用。

    只捕 ImportError 是不够的：包装了一半（原生扩展 ABI 不匹配、动态库缺失、
    被安全策略拒绝加载）时 import 抛的是 OSError/SystemError 等别的异常。
    这里一律视为「不可用」，避免异常从调用链里逃逸。
    """
    try:
        import curl_cffi  # noqa: F401  仅做可用性检查，真正客户端在 Transport 内 lazy 使用
    except Exception:  # noqa: BLE001 - 任何导入期异常都归为「不可用」
        return False
    return True


def resolve_browser_tls(requested):
    """把「设置里是否勾选浏览器兼容 TLS」解析成「本次实际能否启用」。

    返回 (effective, unavailable_reason)：
      - 未勾选          → (False, "")
      - 勾选且依赖可用  → (True, "")
      - 勾选但依赖不可用 → (False, "未安装 curl_cffi")

    注意最后一种：**降级，不报错**。curl_cffi 只是可选加速项，缺了它不应该
    让整个插件不可用（历史 bug：勾选后每次调用都抛 RuntimeError，
    表现就是「测试连接失败」并且对话完全不能用）。
    """
    if not requested:
        return False, ""
    if browser_tls_available():
        return True, ""
    logger.warning(
        "已请求「浏览器兼容 TLS」，但 curl_cffi 不可用，本次改用标准 TLS 栈。"
        "如需启用，请在 QGIS 自带的 Python 中执行：pip install curl_cffi"
    )
    return False, "未安装 curl_cffi"


def get_llm_instance(provider, model, api_key, endpoint, temperature=0, timeout=180, browser_tls=False):
    # 浏览器兼容 TLS（可选）：部分网关会依据客户端 TLS 指纹判断请求来源，
    # 非浏览器客户端可能在握手阶段被中断（httpcore 报 Connection reset by peer）。
    # 开启后改用 curl_cffi 的浏览器 TLS 栈以提高这类接口的连接成功率。
    # 默认关闭：curl_cffi 是带原生扩展的可选依赖，因此只作为「设置里显式开启」的选项，
    # 不进默认发布路径。依赖缺失时**降级为标准 TLS 栈**，绝不因此让调用失败。
    browser_tls = resolve_browser_tls(browser_tls)[0]
    if browser_tls:
        # 用 httpx 兼容的 Transport 把请求转交给 curl_cffi：
        # 既走浏览器 TLS 栈，又能过 openai/langchain 的 isinstance(httpx.Client) 检查。
        http_client = httpx.Client(transport=_CurlTransport(impersonate="chrome"), timeout=timeout)
        http_async_client = httpx.AsyncClient(transport=_AsyncCurlTransport(impersonate="chrome"), timeout=timeout)
        browser_headers = {}
    else:
        # 创建一个不使用系统代理的 httpx client，避免代理导致 DNS 解析失败
        # httpx 0.24.0+ 使用 proxies 参数（字典格式）
        try:
            http_client = httpx.Client(proxy=None)
        except TypeError:
            # httpx 新版本使用 proxies 参数
            http_client = httpx.Client(proxies={})
        http_async_client = None
        # 兼容性请求头：部分自托管 / 网关后的 OpenAI 兼容端点会按 User-Agent 拒绝
        # 非浏览器客户端（返回 403 "Your request was blocked"），因此发送一个常规浏览器 UA。
        # openai SDK 的 default_headers 中 **_custom_headers 会覆盖自带 UA，因此这里能生效。
        browser_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    # 统一加请求超时与最小重试，避免端点不可达时线程永久挂起（表现=发送后永远无回复）
    # timeout 可由调用方覆盖（测试连接用较短超时，对话用默认 180s）
    llm_timeout = timeout
    llm_retries = 1
    if provider == "DeepSeek":
        llm = ChatDeepSeek(
            model=model,
            api_key=api_key,
            temperature=temperature,
            http_client=http_client,
            http_async_client=http_async_client,
            timeout=llm_timeout,
            max_retries=llm_retries,
            default_headers=browser_headers,
        )
    else:
        # 其他所有 provider（GLM, XiaomiMiMo, Gemini, OpenAI, Custom 等）都走 OpenAI 兼容接口
        llm = ChatOpenAI(
            model=model,
            openai_api_key=api_key,
            openai_api_base=endpoint,
            temperature=temperature,
            http_client=http_client,
            http_async_client=http_async_client,
            timeout=llm_timeout,
            max_retries=llm_retries,
            default_headers=browser_headers,
        )
    # 暴露底层 httpx 客户端，供插件卸载 / QGIS 关闭时主动 close() 以中断在途请求，
    # 避免工作线程卡在 socket 等待导致 QGIS 关闭界面一直转圈。
    try:
        llm._http_client = http_client
    except Exception as _e:
        logger.debug("ignored exception", exc_info=True)
    return llm


def get_default_api_key(provider):
    env_map = {
        "GLM": "GLM_API_KEY",
        "DeepSeek": "DEEPSEEK_API_KEY",
        "XiaomiMiMo": "XIAOMI_API_KEY",
        "Gemini": "GEMINI_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
    }
    return os.getenv(env_map.get(provider, ""), "")
