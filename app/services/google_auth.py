from __future__ import annotations

import logging
from pathlib import Path

from app.config import Config

logger = logging.getLogger(__name__)

_creds_cache = None


def _get_credentials():
    """유효한 Google OAuth 자격증명을 반환한다. 없으면 None."""
    global _creds_cache
    config = Config()
    token_path = Path(config.google_token_path)
    client_secret_path = Path(config.google_client_secret_path)

    if not client_secret_path.exists():
        logger.warning("[AUTH] client_secret 파일 없음 → None 반환")
        return None

    try:
        from google.oauth2.credentials import Credentials

        if _creds_cache and _creds_cache.valid:
            return _creds_cache

        creds = None
        if token_path.exists():
            all_scopes = list(set(config.google_calendar_scopes + config.google_gmail_scopes))
            creds = Credentials.from_authorized_user_file(str(token_path), all_scopes)

        if creds and creds.valid:
            _creds_cache = creds
            return creds
        elif creds and creds.expired and creds.refresh_token:
            logger.info("[AUTH] 토큰 만료됨 → 리프레시 시도")
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            logger.info("[AUTH] 토큰 리프레시 완료")
            _creds_cache = creds
            return creds
        else:
            logger.warning("[AUTH] 유효한 인증 정보 없음 → None 반환")
            return None
    except Exception as exc:
        logger.error("[AUTH] 자격증명 획득 실패: %s", exc, exc_info=True)
        return None


def get_calendar_service():
    """Google Calendar API 서비스 객체를 반환한다. 매 호출마다 새 인스턴스 생성 (thread-safe)."""
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
    """Gmail API 서비스 객체를 반환한다. 매 호출마다 새 인스턴스 생성 (thread-safe)."""
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
    """OAuth 인증 플로우를 실행한다. Calendar + Gmail 스코프를 합쳐서 인증. 성공 시 True."""
    config = Config()
    client_secret_path = Path(config.google_client_secret_path)
    token_path = Path(config.google_token_path)

    # Calendar + Gmail 스코프 합산
    all_scopes = list(set(config.google_calendar_scopes + config.google_gmail_scopes))
    logger.info("[AUTH] run_oauth_flow: 시작 (client_secret=%s, token=%s, scopes=%s)", client_secret_path, token_path, all_scopes)

    if not client_secret_path.exists():
        logger.error("[AUTH] run_oauth_flow: client_secret 파일 없음: %s", client_secret_path)
        print(f"오류: {client_secret_path}이 없습니다.")
        print("Google Cloud Console에서 OAuth 클라이언트 자격증명을 다운로드하세요.")
        return False

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_path), all_scopes
        )
        logger.info("[AUTH] run_oauth_flow: 로컬 서버 시작 (브라우저 인증 대기)")
        creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        logger.info("[AUTH] run_oauth_flow: 토큰 저장 완료 → %s", token_path)

        # 캐시 초기화
        global _creds_cache
        _creds_cache = None

        print("Google Calendar + Gmail 인증 완료!")
        logger.info("[AUTH] run_oauth_flow: OAuth 인증 성공")
        return True
    except Exception as exc:
        logger.error("[AUTH] run_oauth_flow: OAuth 실패: %s", exc, exc_info=True)
        print(f"OAuth 인증 실패: {exc}")
        return False
