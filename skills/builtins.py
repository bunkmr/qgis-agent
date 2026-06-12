# -*- coding: utf-8 -*-
"""
内置技能 - 网络搜索、数据分析等常用功能
"""

import json
import urllib.request
import urllib.parse
from .skill_manager import Skill, SkillResult, get_skill_manager


def web_search_handler(query: str, num_results: int = 5, engine: str = "duckduckgo") -> SkillResult:
    """
    网络搜索技能

    Args:
        query: 搜索查询
        num_results: 返回结果数量
        engine: 搜索引擎 (duckduckgo, google, bing)

    Returns:
        SkillResult: 搜索结果
    """
    try:
        if engine == "duckduckgo":
            results = _search_duckduckgo(query, num_results)
        elif engine == "google":
            results = _search_google(query, num_results)
        elif engine == "bing":
            results = _search_bing(query, num_results)
        else:
            return SkillResult(success=False, error=f"Unsupported engine: {engine}")

        return SkillResult(
            success=True,
            output=results,
            metadata={"engine": engine, "query": query, "count": len(results)}
        )
    except Exception as e:
        return SkillResult(success=False, error=str(e))


def _search_duckduckgo(query: str, num_results: int) -> list[dict]:
    """使用 DuckDuckGo 搜索"""
    try:
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=num_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return results
    except ImportError:
        # 如果 duckduckgo_search 未安装，使用简易 HTTP 请求
        return _search_duckduckgo_http(query, num_results)


def _search_duckduckgo_http(query: str, num_results: int) -> list[dict]:
    """使用 HTTP 请求搜索 DuckDuckGo"""
    url = f"https://duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {"User-Agent": "Mozilla/5.0"}

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        html = response.read().decode("utf-8")

    # 简单解析结果
    results = []
    # 这里需要实际的 HTML 解析，暂时返回空列表
    # 建议安装 duckduckgo_search 库获得更好的支持
    return results


def _search_google(query: str, num_results: int) -> list[dict]:
    """使用 Google Custom Search API"""
    # 需要 API Key 和 Custom Search Engine ID
    # 这里提供一个框架实现
    import os

    api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
    cse_id = os.getenv("GOOGLE_CSE_ID")

    if not api_key or not cse_id:
        return [{"error": "Google Search API key not configured"}]

    url = (
        f"https://www.googleapis.com/customsearch/v1?"
        f"key={api_key}&cx={cse_id}&q={urllib.parse.quote(query)}&num={num_results}"
    )

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    results = []
    for item in data.get("items", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    return results


def _search_bing(query: str, num_results: int) -> list[dict]:
    """使用 Bing Search API"""
    import os

    api_key = os.getenv("BING_SEARCH_API_KEY")
    if not api_key:
        return [{"error": "Bing Search API key not configured"}]

    url = (
        f"https://api.bing.microsoft.com/v7.0/search?"
        f"q={urllib.parse.quote(query)}&count={num_results}"
    )

    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    results = []
    for item in data.get("webPages", {}).get("value", []):
        results.append({
            "title": item.get("name", ""),
            "url": item.get("url", ""),
            "snippet": item.get("snippet", ""),
        })
    return results


def gis_data_search_handler(query: str, data_type: str = "all") -> SkillResult:
    """
    GIS 数据搜索技能

    搜索公开的 GIS 数据源

    Args:
        query: 搜索查询
        data_type: 数据类型 (vector, raster, all)

    Returns:
        SkillResult: 搜索结果
    """
    try:
        # 知名 GIS 数据源
        data_sources = [
            {
                "name": "Natural Earth",
                "url": "https://www.naturalearthdata.com/",
                "description": "全球矢量和栅格数据",
                "types": ["vector", "raster"],
            },
            {
                "name": "GADM",
                "url": "https://gadm.org/",
                "description": "全球行政边界数据",
                "types": ["vector"],
            },
            {
                "name": "OpenStreetMap",
                "url": "https://www.openstreetmap.org/",
                "description": "全球开放地图数据",
                "types": ["vector"],
            },
            {
                "name": "USGS Earth Explorer",
                "url": "https://earthexplorer.usgs.gov/",
                "description": "卫星影像和地形数据",
                "types": ["raster"],
            },
            {
                "name": "地理空间数据云",
                "url": "https://www.gscloud.cn/",
                "description": "中国地理空间数据",
                "types": ["vector", "raster"],
            },
        ]

        # 过滤数据类型
        if data_type != "all":
            data_sources = [d for d in data_sources if data_type in d["types"]]

        # 简单关键词匹配
        results = []
        query_lower = query.lower()
        for source in data_sources:
            if (query_lower in source["name"].lower() or
                query_lower in source["description"].lower()):
                results.append(source)

        # 如果没有匹配，返回所有结果
        if not results:
            results = data_sources

        return SkillResult(
            success=True,
            output=results,
            metadata={"query": query, "data_type": data_type}
        )
    except Exception as e:
        return SkillResult(success=False, error=str(e))


def format_search_results_handler(results: list, format: str = "markdown") -> SkillResult:
    """
    格式化搜索结果

    Args:
        results: 搜索结果列表
        format: 输出格式 (markdown, html, json)

    Returns:
        SkillResult: 格式化后的结果
    """
    try:
        if format == "markdown":
            output = _format_as_markdown(results)
        elif format == "html":
            output = _format_as_html(results)
        elif format == "json":
            output = json.dumps(results, ensure_ascii=False, indent=2)
        else:
            return SkillResult(success=False, error=f"Unsupported format: {format}")

        return SkillResult(success=True, output=output)
    except Exception as e:
        return SkillResult(success=False, error=str(e))


def _format_as_markdown(results: list) -> str:
    """格式化为 Markdown"""
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        snippet = r.get("snippet", "")

        lines.append(f"### {i}. {title}")
        if url:
            lines.append(f"链接: {url}")
        if snippet:
            lines.append(f"{snippet}")
        lines.append("")

    return "\n".join(lines)


def _format_as_html(results: list) -> str:
    """格式化为 HTML"""
    html_parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        snippet = r.get("snippet", "")

        html_parts.append(f'<div style="margin: 10px 0;">')
        html_parts.append(f'<h3 style="margin: 0;">{i}. {title}</h3>')
        if url:
            html_parts.append(f'<a href="{url}">{url}</a>')
        if snippet:
            html_parts.append(f'<p style="color: #666;">{snippet}</p>')
        html_parts.append(f'</div>')

    return "\n".join(html_parts)


def register_builtin_skills(manager: SkillManager = None):
    """注册所有内置技能"""
    if manager is None:
        manager = get_skill_manager()

    # 网络搜索技能
    manager.register(Skill(
        name="web_search",
        description="搜索互联网获取信息。支持 DuckDuckGo、Google、Bing 等搜索引擎。",
        version="1.0.0",
        author="QGIS Agent",
        category="search",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询"
                },
                "num_results": {
                    "type": "integer",
                    "description": "返回结果数量",
                    "default": 5
                },
                "engine": {
                    "type": "string",
                    "description": "搜索引擎 (duckduckgo, google, bing)",
                    "default": "duckduckgo"
                }
            },
            "required": ["query"]
        },
        handler=web_search_handler,
        tags=["search", "web", "internet"],
    ))

    # GIS 数据搜索技能
    manager.register(Skill(
        name="gis_data_search",
        description="搜索公开的 GIS 数据源，包括矢量数据、栅格数据、卫星影像等。",
        version="1.0.0",
        author="QGIS Agent",
        category="search",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询"
                },
                "data_type": {
                    "type": "string",
                    "description": "数据类型 (vector, raster, all)",
                    "default": "all"
                }
            },
            "required": ["query"]
        },
        handler=gis_data_search_handler,
        tags=["search", "gis", "data"],
    ))

    # 格式化结果技能
    manager.register(Skill(
        name="format_results",
        description="格式化搜索结果为 Markdown、HTML 或 JSON 格式。",
        version="1.0.0",
        author="QGIS Agent",
        category="utility",
        parameters={
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "description": "搜索结果列表"
                },
                "format": {
                    "type": "string",
                    "description": "输出格式 (markdown, html, json)",
                    "default": "markdown"
                }
            },
            "required": ["results"]
        },
        handler=format_search_results_handler,
        tags=["format", "utility"],
    ))

    print(f"Registered {len(manager.get_all())} built-in skills")
