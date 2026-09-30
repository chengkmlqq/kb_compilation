"""KB Compilation Platform — configuration.

Environment variables follow data-synth conventions (DB_TYPE, DATABASE_URL, ...)
so the extracted service can be dropped into the existing deployment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (same behaviour as data-synth Next.js).
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


class Settings:
    """Runtime settings, read from env with data-synth-compatible names."""

    # --- Database (dual dialect: pg / mysql, switched by DB_TYPE) ---
    DB_TYPE: str = os.getenv("DB_TYPE", "pg")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SCHEMA_NAME: str = os.getenv("SCHEMA_NAME", "public")
    DB_MAX_CONNECTIONS: int = int(os.getenv("DB_MAX_CONNECTIONS", "10"))
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "1") == "1"

    # --- Auth ---
    AUTH_SECRET: str = os.getenv("AUTH_SECRET", os.getenv("NEXTAUTH_SECRET", ""))
    SSO_ENABLED: bool = os.getenv("SSO_ENABLED", "0") == "1"
    SSO_URL: str = os.getenv("SSO_URL", "")
    COOKIES_MAX_AGE: int = int(os.getenv("COOKIES_MAX_AGE", "172800"))

    # --- Redis / Celery ---
    # All URLs come from env only. If unset, Celery falls back to its own
    # localhost defaults for local development.
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "")
    CELERY_TIMEZONE: str = os.getenv("CELERY_TIMEZONE", "Asia/Shanghai")
    JOB_BEAT_SCAN_INTERVAL_SECONDS: int = int(
        os.getenv("JOB_BEAT_SCAN_INTERVAL_SECONDS", "30")
    )

    # --- Server ---
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

    @property
    def is_pg(self) -> bool:
        return self.DB_TYPE in ("pg", "postgres", "postgresql", "kingbasees8")

    def require_database_url(self) -> str:
        if not self.DATABASE_URL:
            raise RuntimeError("Missing required env var: DATABASE_URL")
        return self.DATABASE_URL

    def effective_broker_url(self) -> str:
        """Resolve Celery broker URL: explicit env wins, fall back to REDIS_URL."""
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        return self.REDIS_URL or ""

    def effective_result_backend(self) -> str:
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        return self.REDIS_URL or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
