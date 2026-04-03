from __future__ import annotations

import logging

from app.config import Config

logger = logging.getLogger(__name__)


def get_tavily_tool():
    """Tavily 웹 검색 도구를 생성한다. API 키가 없으면 None 반환."""
    config = Config()
    if not config.tavily_api_key:
        logger.warning("[TOOL] Tavily API 키 없음 → 웹 검색 도구 비활성화")
        return None

    try:
        from langchain_tavily import TavilySearch

        tool = TavilySearch(
            api_key=config.tavily_api_key,
            max_results=5,
            search_depth="advanced",
            include_answer=True,
            include_raw_content=False,
        )
        logger.info("[TOOL] Tavily 웹 검색 도구 생성 완료 (max_results=5, depth=advanced)")
        return tool
    except Exception as exc:
        logger.error("[TOOL] Tavily 도구 생성 실패: %s", exc, exc_info=True)
        return None
