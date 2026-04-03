from __future__ import annotations

import logging

import requests
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

WTTR_BASE = "https://wttr.in"


@tool
def get_weather(location: str = "Seoul") -> dict:
    """지정한 도시의 현재 날씨와 3일 예보를 조회합니다. API 키 불필요.

    Args:
        location: 도시명 (예: "Seoul", "Busan", "Tokyo", "New York")

    Returns:
        현재 날씨 (온도, 체감온도, 습도, 풍속, 상태) + 3일 예보
    """
    logger.info("[TOOL] get_weather 호출: location=%s", location)

    try:
        resp = requests.get(
            f"{WTTR_BASE}/{location}",
            params={"format": "j1"},
            headers={"Accept-Language": "ko"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # 현재 날씨
        current = data.get("current_condition", [{}])[0]
        result = {
            "location": location,
            "current": {
                "temp_c": current.get("temp_C", ""),
                "feels_like_c": current.get("FeelsLikeC", ""),
                "humidity": current.get("humidity", ""),
                "wind_speed_kmph": current.get("windspeedKmph", ""),
                "wind_dir": current.get("winddir16Point", ""),
                "description": current.get("lang_ko", [{}])[0].get("value", current.get("weatherDesc", [{}])[0].get("value", "")),
                "precip_mm": current.get("precipMM", ""),
                "uv_index": current.get("uvIndex", ""),
            },
            "forecast": [],
        }

        # 3일 예보
        for day in data.get("weather", [])[:3]:
            result["forecast"].append({
                "date": day.get("date", ""),
                "max_temp_c": day.get("maxtempC", ""),
                "min_temp_c": day.get("mintempC", ""),
                "avg_temp_c": day.get("avgtempC", ""),
                "total_snow_cm": day.get("totalSnow_cm", ""),
                "sun_hour": day.get("sunHour", ""),
                "description": day.get("hourly", [{}])[4].get("lang_ko", [{}])[0].get("value", "") if day.get("hourly") else "",
            })

        logger.info("[TOOL] get_weather: 조회 성공 (%s, %s°C)", location, result["current"]["temp_c"])
        return result
    except requests.HTTPError as exc:
        logger.error("[TOOL] get_weather 실패: %s", exc, exc_info=True)
        return {"error": f"날씨 조회 실패: {exc.response.status_code}"}
    except Exception as exc:
        logger.error("[TOOL] get_weather 실패: %s", exc, exc_info=True)
        return {"error": f"날씨 조회 실패: {exc}"}
