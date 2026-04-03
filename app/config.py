from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env_path = _PROJECT_ROOT / ".env"
    load_dotenv(env_path)


_load_env()


class Config:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Tavily
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # Notion
    notion_token: str = os.getenv("NOTION_TOKEN", "")

    # GitHub
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_username: str = os.getenv("GITHUB_USERNAME", "")
    github_default_repo: str = os.getenv("GITHUB_DEFAULT_REPO", "")

    # Google Calendar
    google_client_secret_path: str = os.getenv(
        "GOOGLE_CLIENT_SECRET_PATH", "credentials/client_secret.json"
    )
    google_token_path: str = os.getenv(
        "GOOGLE_TOKEN_PATH", "credentials/token.json"
    )
    google_calendar_scopes: list[str] = [
        s.strip()
        for s in os.getenv(
            "GOOGLE_CALENDAR_SCOPES",
            "https://www.googleapis.com/auth/calendar",
        ).split(",")
    ]

    # Google Gmail
    google_gmail_scopes: list[str] = [
        s.strip()
        for s in os.getenv(
            "GOOGLE_GMAIL_SCOPES",
            "https://www.googleapis.com/auth/gmail.send,https://www.googleapis.com/auth/gmail.readonly",
        ).split(",")
    ]

    # Discord
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", "")

    # Heartbeat
    heartbeat_enabled: bool = os.getenv("HEARTBEAT_ENABLED", "false").lower() == "true"
    heartbeat_interval: int = int(os.getenv("HEARTBEAT_INTERVAL", "30"))
    heartbeat_channel_id: int = int(os.getenv("HEARTBEAT_CHANNEL_ID", "0"))
    heartbeat_active_start: str = os.getenv("HEARTBEAT_ACTIVE_START", "09:00")
    heartbeat_active_end: str = os.getenv("HEARTBEAT_ACTIVE_END", "22:00")

    # PostgreSQL
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://hwangjin-yeong:@localhost:5432/jin_db"
    )
