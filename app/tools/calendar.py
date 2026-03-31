from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.tools import tool

from app.services.google_auth import get_calendar_service

logger = logging.getLogger(__name__)


@tool
def create_calendar_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    attendees: list[str] | None = None,
    location: str = "",
) -> dict:
    """Google Calendar에 새 일정을 생성합니다.

    Args:
        title: 일정 제목
        start: 시작 시간 (ISO 8601, 예: 2026-03-21T10:00:00+09:00)
        end: 종료 시간 (ISO 8601, 예: 2026-03-21T11:00:00+09:00)
        description: 일정 설명 (선택)
        attendees: 참석자 이메일 목록 (선택)
        location: 장소 (선택)

    Returns:
        생성된 이벤트 정보 (id, htmlLink 등)
    """
    logger.info("[TOOL] create_calendar_event 호출: title=%s, start=%s, end=%s", title, start, end)
    if description:
        logger.info("[TOOL] create_calendar_event: description=%s", description[:100])
    if attendees:
        logger.info("[TOOL] create_calendar_event: attendees=%s", attendees)
    if location:
        logger.info("[TOOL] create_calendar_event: location=%s", location)

    service = get_calendar_service()
    if service is None:
        logger.error("[TOOL] create_calendar_event: Calendar 서비스 없음 (OAuth 미인증)")
        return {"error": "Google Calendar가 연결되지 않았습니다. OAuth 인증을 먼저 진행해주세요."}

    logger.info("[TOOL] create_calendar_event: Calendar 서비스 획득 완료")
    event_body: dict = {
        "summary": title,
        "start": {"dateTime": start, "timeZone": "Asia/Seoul"},
        "end": {"dateTime": end, "timeZone": "Asia/Seoul"},
    }
    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location
    if attendees:
        event_body["attendees"] = [{"email": e} for e in attendees]

    logger.info("[TOOL] create_calendar_event: API 호출 (body=%s)", event_body)
    try:
        event = service.events().insert(calendarId="primary", body=event_body).execute()
        logger.info("[TOOL] create_calendar_event: 생성 성공 (id=%s, summary=%s)", event.get("id"), event.get("summary"))
        return {
            "status": "success",
            "id": event.get("id"),
            "htmlLink": event.get("htmlLink"),
            "summary": event.get("summary"),
            "start": event.get("start"),
            "end": event.get("end"),
        }
    except Exception as exc:
        logger.error("[TOOL] create_calendar_event: 생성 실패: %s", exc, exc_info=True)
        return {"error": f"일정 생성 실패: {exc}"}


@tool
def list_calendar_events(
    start_date: str,
    end_date: str,
    max_results: int = 10,
) -> list[dict]:
    """Google Calendar에서 일정을 조회합니다.

    Args:
        start_date: 조회 시작일 (ISO 8601, 예: 2026-03-20T00:00:00+09:00)
        end_date: 조회 종료일 (ISO 8601, 예: 2026-03-21T23:59:59+09:00)
        max_results: 최대 결과 수

    Returns:
        일정 목록
    """
    logger.info("[TOOL] list_calendar_events 호출: start=%s, end=%s, max=%d", start_date, end_date, max_results)

    service = get_calendar_service()
    if service is None:
        logger.error("[TOOL] list_calendar_events: Calendar 서비스 없음 (OAuth 미인증)")
        return [{"error": "Google Calendar가 연결되지 않았습니다."}]

    logger.info("[TOOL] list_calendar_events: Calendar 서비스 획득 완료")
    try:
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_date,
                timeMax=end_date,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = result.get("items", [])
        logger.info("[TOOL] list_calendar_events: API 응답 — %d개 일정 조회됨", len(events))
        for i, e in enumerate(events):
            logger.info("[TOOL] list_calendar_events: event[%d] summary=%s, start=%s",
                        i, e.get("summary", "(제목 없음)"),
                        e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")))
        parsed = [
            {
                "id": e.get("id"),
                "summary": e.get("summary", "(제목 없음)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                "location": e.get("location", ""),
            }
            for e in events
        ]
        logger.info("[TOOL] list_calendar_events: 반환 결과=%s", parsed)
        return parsed
    except Exception as exc:
        logger.error("[TOOL] list_calendar_events: 조회 실패: %s", exc, exc_info=True)
        return [{"error": f"일정 조회 실패: {exc}"}]


@tool
def update_calendar_event(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Google Calendar에서 기존 일정을 수정합니다.

    Args:
        event_id: 수정할 이벤트 ID (list_calendar_events에서 조회한 id)
        title: 새 제목 (변경할 경우만)
        start: 새 시작 시간 (ISO 8601, 예: 2026-03-24T14:00:00+09:00) (변경할 경우만)
        end: 새 종료 시간 (ISO 8601, 예: 2026-03-24T15:00:00+09:00) (변경할 경우만)
        description: 새 설명 (변경할 경우만)
        location: 새 장소 (변경할 경우만)

    Returns:
        수정된 이벤트 정보
    """
    logger.info("[TOOL] update_calendar_event 호출: event_id=%s", event_id)

    patch_body: dict = {}
    if title is not None:
        patch_body["summary"] = title
    if start is not None:
        patch_body["start"] = {"dateTime": start, "timeZone": "Asia/Seoul"}
    if end is not None:
        patch_body["end"] = {"dateTime": end, "timeZone": "Asia/Seoul"}
    if description is not None:
        patch_body["description"] = description
    if location is not None:
        patch_body["location"] = location

    if not patch_body:
        return {"error": "수정할 필드를 하나 이상 제공해야 합니다."}

    logger.info("[TOOL] update_calendar_event: patch_body=%s", patch_body)

    service = get_calendar_service()
    if service is None:
        logger.error("[TOOL] update_calendar_event: Calendar 서비스 없음 (OAuth 미인증)")
        return {"error": "Google Calendar가 연결되지 않았습니다. OAuth 인증을 먼저 진행해주세요."}

    try:
        event = (
            service.events()
            .patch(calendarId="primary", eventId=event_id, body=patch_body)
            .execute()
        )
        logger.info("[TOOL] update_calendar_event: 수정 성공 (id=%s, summary=%s)", event.get("id"), event.get("summary"))
        return {
            "status": "success",
            "id": event.get("id"),
            "htmlLink": event.get("htmlLink"),
            "summary": event.get("summary"),
            "start": event.get("start"),
            "end": event.get("end"),
        }
    except Exception as exc:
        logger.error("[TOOL] update_calendar_event: 수정 실패: %s", exc, exc_info=True)
        return {"error": f"일정 수정 실패: {exc}"}


@tool
def delete_calendar_event(event_id: str) -> dict:
    """Google Calendar에서 일정을 삭제합니다.

    Args:
        event_id: 삭제할 이벤트 ID (list_calendar_events에서 조회한 id)

    Returns:
        삭제 결과
    """
    logger.info("[TOOL] delete_calendar_event 호출: event_id=%s", event_id)

    service = get_calendar_service()
    if service is None:
        logger.error("[TOOL] delete_calendar_event: Calendar 서비스 없음 (OAuth 미인증)")
        return {"error": "Google Calendar가 연결되지 않았습니다. OAuth 인증을 먼저 진행해주세요."}

    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        logger.info("[TOOL] delete_calendar_event: 삭제 성공 (id=%s)", event_id)
        return {"status": "success", "id": event_id}
    except Exception as exc:
        logger.error("[TOOL] delete_calendar_event: 삭제 실패: %s", exc, exc_info=True)
        return {"error": f"일정 삭제 실패: {exc}"}


def get_calendar_tools() -> list:
    """사용 가능한 캘린더 도구 목록을 반환한다. 인증이 안 되어도 도구 자체는 반환."""
    tools = [create_calendar_event, list_calendar_events, update_calendar_event, delete_calendar_event]
    logger.info("[TOOL] get_calendar_tools: %d개 도구 반환 (%s)", len(tools), [t.name for t in tools])
    return tools
