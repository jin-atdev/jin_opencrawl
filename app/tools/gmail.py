from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.services.google_auth import get_gmail_service

logger = logging.getLogger(__name__)


@tool
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    *, config: RunnableConfig | None = None,
) -> dict:
    """이메일을 보냅니다.

    Args:
        to: 수신자 이메일 주소
        subject: 이메일 제목
        body: 이메일 본문
        cc: 참조 이메일 주소 (선택, 쉼표로 구분)
        bcc: 숨은 참조 이메일 주소 (선택, 쉼표로 구분)

    Returns:
        전송 결과 (id, threadId 등)
    """
    logger.info("[TOOL] send_email 호출: to=%s, subject=%s", to, subject)

    service = get_gmail_service()
    if service is None:
        logger.error("[TOOL] send_email: Gmail 서비스 없음 (OAuth 미인증)")
        return {"error": "Gmail이 연결되지 않았습니다. Google OAuth 인증을 먼저 진행해주세요."}

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        send_result = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        logger.info("[TOOL] send_email: 전송 성공 (id=%s)", send_result.get("id"))
        return {
            "status": "success",
            "id": send_result.get("id"),
            "threadId": send_result.get("threadId"),
        }
    except Exception as exc:
        logger.error("[TOOL] send_email: 전송 실패: %s", exc, exc_info=True)
        return {"error": f"이메일 전송 실패: {exc}"}


@tool
def list_emails(
    query: str = "is:unread",
    max_results: int = 10,
    *, config: RunnableConfig | None = None,
) -> list[dict]:
    """이메일 목록을 조회합니다.

    Args:
        query: Gmail 검색 쿼리 (예: is:unread, from:xxx, subject:xxx)
        max_results: 최대 결과 수

    Returns:
        이메일 목록 (id, subject, from, date, snippet)
    """
    logger.info("[TOOL] list_emails 호출: query=%s, max_results=%d", query, max_results)

    service = get_gmail_service()
    if service is None:
        logger.error("[TOOL] list_emails: Gmail 서비스 없음 (OAuth 미인증)")
        return [{"error": "Gmail이 연결되지 않았습니다. Google OAuth 인증을 먼저 진행해주세요."}]

    try:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        messages = result.get("messages", [])
        logger.info("[TOOL] list_emails: %d개 메시지 조회됨", len(messages))

        if not messages:
            return []

        emails = []
        for msg_info in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_info["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            emails.append({
                "id": msg_info["id"],
                "subject": headers.get("Subject", "(제목 없음)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })

        logger.info("[TOOL] list_emails: %d개 이메일 반환", len(emails))
        return emails
    except Exception as exc:
        logger.error("[TOOL] list_emails: 조회 실패: %s", exc, exc_info=True)
        return [{"error": f"이메일 조회 실패: {exc}"}]


@tool
def read_email(email_id: str, *, config: RunnableConfig | None = None) -> dict:
    """이메일 본문을 읽습니다.

    Args:
        email_id: 이메일 ID (list_emails에서 조회한 id)

    Returns:
        이메일 상세 정보 (subject, from, to, date, body)
    """
    logger.info("[TOOL] read_email 호출: email_id=%s", email_id)

    service = get_gmail_service()
    if service is None:
        logger.error("[TOOL] read_email: Gmail 서비스 없음 (OAuth 미인증)")
        return {"error": "Gmail이 연결되지 않았습니다. Google OAuth 인증을 먼저 진행해주세요."}

    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=email_id, format="full")
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # 본문 추출 (text/plain 우선)
        body = _extract_body(msg.get("payload", {}))

        result = {
            "id": email_id,
            "subject": headers.get("Subject", "(제목 없음)"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "body": body,
        }
        logger.info("[TOOL] read_email: 읽기 성공 (subject=%s)", result["subject"])
        return result
    except Exception as exc:
        logger.error("[TOOL] read_email: 읽기 실패: %s", exc, exc_info=True)
        return {"error": f"이메일 읽기 실패: {exc}"}


def _extract_body(payload: dict) -> str:
    """payload에서 text/plain 본문을 추출한다."""
    # 단일 파트
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # 멀티파트
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # 중첩 멀티파트
        if part.get("parts"):
            result = _extract_body(part)
            if result:
                return result

    return "(본문을 읽을 수 없습니다)"


def get_gmail_tools() -> list:
    """사용 가능한 Gmail 도구 목록을 반환한다."""
    tools = [send_email, list_emails, read_email]
    logger.info("[TOOL] get_gmail_tools: %d개 도구 반환 (%s)", len(tools), [t.name for t in tools])
    return tools
