# -*- coding: utf-8 -*-
"""
天气查询示例技能 - 展示如何编写自定义技能

这个技能演示了:
1. 如何定义技能参数
2. 如何调用外部 API
3. 如何返回结果
"""

import json
import urllib.request
import urllib.parse
from skills.skill_manager import Skill, SkillResult


def handler(city: str = "Beijing", units: str = "metric") -> SkillResult:
    """
    查询天气信息

    Args:
        city: 城市名称
        units: 单位 (metric, imperial)

    Returns:
        SkillResult: 天气信息
    """
    try:
        # 使用 Open-Meteo 免费 API（无需 API Key）
        # 首先获取城市坐标
        geocoding_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"

        req = urllib.request.Request(geocoding_url)
        with urllib.request.urlopen(req, timeout=10) as response:
            geo_data = json.loads(response.read().decode("utf-8"))

        if not geo_data.get("results"):
            return SkillResult(success=False, error=f"City not found: {city}")

        location = geo_data["results"][0]
        lat = location["latitude"]
        lon = location["longitude"]

        # 获取天气数据
        temp_unit = "celsius" if units == "metric" else "fahrenheit"
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            f"&temperature_unit={temp_unit}"
            f"&timezone=auto"
        )

        req = urllib.request.Request(weather_url)
        with urllib.request.urlopen(req, timeout=10) as response:
            weather_data = json.loads(response.read().decode("utf-8"))

        current = weather_data.get("current", {})

        # 天气代码转换为描述
        weather_codes = {
            0: "晴天",
            1: "大部晴朗",
            2: "多云",
            3: "阴天",
            45: "雾",
            48: "雾凇",
            51: "小毛毛雨",
            53: "中毛毛雨",
            55: "大毛毛雨",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            80: "小阵雨",
            81: "中阵雨",
            82: "大阵雨",
            95: "雷暴",
        }

        weather_code = current.get("weather_code", 0)
        weather_desc = weather_codes.get(weather_code, f"代码 {weather_code}")

        result = {
            "city": location.get("name", city),
            "country": location.get("country", ""),
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "weather": weather_desc,
            "units": units,
        }

        return SkillResult(
            success=True,
            output=result,
            metadata={"city": city, "lat": lat, "lon": lon}
        )

    except Exception as e:
        return SkillResult(success=False, error=str(e))


# 技能定义
SKILL = Skill(
    name="weather",
    description="查询城市天气信息，包括温度、湿度、风速等。",
    version="1.0.0",
    author="QGIS Agent Examples",
    category="utility",
    parameters={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称（英文）",
                "default": "Beijing"
            },
            "units": {
                "type": "string",
                "description": "温度单位 (metric: 摄氏度, imperial: 华氏度)",
                "default": "metric"
            }
        },
        "required": []
    },
    handler=handler,
    tags=["weather", "utility", "example"],
)
