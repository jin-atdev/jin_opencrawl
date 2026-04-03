from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import Config

logger = logging.getLogger(__name__)

_creds = None  # 캐시된 Credentials


def _get_credentials():
    """유효한 Google OAuth 자격증명을 반환한다. token.json에서 로드."""
    global _creds
    config = Config()
    client_secret_path = Path(config.google_client_secret_path)

    if not client_secret_path.exists():
        logger.warning("[AUTH] client_secret 파일 없음 → None 반환")
        return None

    try:
        from google.oauth2.credentials import Credentials

        if _creds and _creds.valid:
            return _creds

        all_scopes = list(set(config.google_calendar_scopes + config.google_gmail_scopes))
        token_path = Path(config.google_token_path)

        if not token_path.exists():
            logger.warning("[AUTH] token.json 없음 → None 반환")
            return None

        creds = Credentials.from_authorized_user_file(str(token_path), all_scopes)

        if creds.valid:
            _creds = creds
            return creds
        elif creds.expired and creds.refresh_token:
            logger.info("[AUTH] 토큰 만료됨 → 리프레시 시도")
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            logger.info("[AUTH] 토큰 리프레시 완료")
            _creds = creds
            return creds
        else:
            logger.warning("[AUTH] 유효한 인증 정보 없음 → None 반환")
            return None
    except Exception as exc:
        logger.error("[AUTH] 자격증명 획득 실패: %s", exc, exc_info=True)
        return None


def get_calendar_service():
    """Google Calendar API 서비스 객체를 반환한다."""
    creds = _get_credentials()
    if creds is None:
        return None

    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)
        logger.info("[AUTH] get_calendar_service: Calendar API 서비스 빌드 완료")
        return service
    except Exception as exc:
        logger.error("[AUTH] get_calendar_service: 예외 발생: %s", exc, exc_info=True)
        return None


def get_gmail_service():
    """Gmail API 서비스 객체를 반환한다."""
    creds = _get_credentials()
    if creds is None:
        return None

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        logger.info("[AUTH] get_gmail_service: Gmail API 서비스 빌드 완료")
        return service
    except Exception as exc:
        logger.error("[AUTH] get_gmail_service: 예외 발생: %s", exc, exc_info=True)
        return None


def run_oauth_flow() -> bool:
    """OAuth 인증 플로우를 실행한다. Calendar + Gmail 스코프를 합쳐서 인증."""
    global _creds
    config = Config()
    client_secret_path = Path(config.google_client_secret_path)

    all_scopes = list(set(config.google_calendar_scopes + config.google_gmail_scopes))

    if not client_secret_path.exists():
        logger.error("[AUTH] run_oauth_flow: client_secret 파일 없음: %s", client_secret_path)
        print(f"오류: {client_secret_path}이 없습니다.")
        return False

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_path), all_scopes
        )
        creds = flow.run_local_server(port=0)

        token_path = Path(config.google_token_path)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

        _creds = None  # 캐시 초기화
        logger.info("[AUTH] run_oauth_flow: OAuth 인증 성공")
        print("Google Calendar + Gmail 인증 완료!")
        return True
    except Exception as exc:
        logger.error("[AUTH] run_oauth_flow: OAuth 실패: %s", exc, exc_info=True)
        print(f"OAuth 인증 실패: {exc}")
        return False
