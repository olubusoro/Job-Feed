"""
Application configuration loaded from environment variables.
Override by creating a .env file in the project root.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    # Switch to postgres by setting DATABASE_URL=postgresql+psycopg2://user:pass@host/db
    DATABASE_URL: str = "sqlite:///./jobfeed.db"

    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-please"
    SESSION_MAX_AGE: int = 60 * 60 * 24 * 30  # 30 days in seconds

    # Only users who know this code can sign up
    INVITE_CODE: str = "friends-only"

    # ── Admin ─────────────────────────────────────────────────────────────────
    ADMIN_EMAIL: str = ""  # If set, this user gets the admin panel

    # ── Scheduler ────────────────────────────────────────────────────────────
    FETCH_INTERVAL_HOURS: int = 3

    # ── HTTP client ───────────────────────────────────────────────────────────
    HTTP_TIMEOUT: int = 10
    HTTP_MAX_WORKERS: int = 15
    HTTP_USER_AGENT: str = (
        "JobFeed/1.0 (+https://github.com/jobfeed; job-aggregator)"
    )
    HTTP_MAX_RETRIES: int = 3

    # ── App ───────────────────────────────────────────────────────────────────
    APP_TITLE: str = "JobFeed"
    DEBUG: bool = False


settings = Settings()
