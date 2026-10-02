"""KB Compilation Platform — configuration.

Environment variables follow the source platform's conventions (DB_TYPE, DATABASE_URL, ...)
so the extracted service can be dropped into the existing deployment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (same behaviour as the source Next.js app).
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


class Settings:
    """Runtime settings, read from env with source-compatible names."""

    # --- Database (dual dialect: pg / mysql, switched by DB_TYPE) ---
    DB_TYPE: str = os.getenv("DB_TYPE", "pg")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SCHEMA_NAME: str = os.getenv("SCHEMA_NAME", "public")
    DB_MAX_CONNECTIONS: int = int(os.getenv("DB_MAX_CONNECTIONS", "10"))
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "1") == "1"

    # --- Vector store (PostgreSQL + pgvector, for chunk embeddings only) ---
    # The knowledge BUSINESS tables (kb_datasource / kb_document / doc_chunk /
    # wiki_* / kb_agent) live on the FRAMEWORK relational store (DATABASE_URL);
    # only the `kb_embedding` vector table lives on this PG store so pgvector
    # and HNSW search are available. VECTOR_STORE_TYPE=es is reserved for a
    # future Elasticsearch backend.
    KNOWLEDGE_DATABASE_URL: str = os.getenv("KNOWLEDGE_DATABASE_URL", "")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1024"))
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")

    # --- Auth ---
    AUTH_SECRET: str = os.getenv("AUTH_SECRET", os.getenv("NEXTAUTH_SECRET", ""))
    SSO_ENABLED: bool = os.getenv("SSO_ENABLED", "0") == "1"
    SSO_URL: str = os.getenv("SSO_URL", "")
    COOKIES_MAX_AGE: int = int(os.getenv("COOKIES_MAX_AGE", "172800"))
    # Comma-separated user ids that bypass the RBAC path check (source
    # proxy's AUTH_ADMIN_USERS equivalent). plat-mgr role also bypasses.
    AUTH_ADMIN_USERS: str = os.getenv("AUTH_ADMIN_USERS", "")

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

    # --- Knowledge file storage (uploaded KB documents) ---
    # Local directory where uploaded document bytes are kept. The Celery
    # doc-process task reads from `storage_path` on kb_document.
    KB_STORAGE_DIR: str = os.getenv("KB_STORAGE_DIR", "")

    # --- Vector store backend ---
    # pg (default, pgvector) | es (reserved). Business tables always live on
    # the framework store; only chunk embeddings go to the vector backend.
    VECTOR_STORE_TYPE: str = os.getenv("VECTOR_STORE_TYPE", "pg")

    # --- Agent gateway (hermes-agent skill execution service) ---
    # Base URL of the separately-deployed agent-gateway (default host:8080).
    # In docker-compose the worker reaches it via host.docker.internal:8080
    # (gateway binds host 8080 -> container 8080).
    AGENT_GATEWAY_BASE_URL: str = os.getenv("AGENT_GATEWAY_BASE_URL", "http://127.0.0.1:8080")
    # Admin token for the gateway's management APIs (MCP/skills). Empty = no token.
    AGENT_GATEWAY_ADMIN_TOKEN: str = os.getenv("AGENT_GATEWAY_ADMIN_TOKEN", "")
    # Global wait budget for a single gateway task (a full skill run takes
    # 10-25 min; mirrors the gateway's SKILL_SCRIPT_TIMEOUT_S=3600).
    AGENT_GATEWAY_TIMEOUT_S: int = int(os.getenv("AGENT_GATEWAY_TIMEOUT_S", "3600"))
    # Polling interval for GET /tasks/{id} while the agent runs.
    AGENT_GATEWAY_POLL_INTERVAL_S: int = int(os.getenv("AGENT_GATEWAY_POLL_INTERVAL_S", "15"))

    # --- Server ---
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

    @property
    def kb_storage_dir(self) -> str:
        """Resolve the KB document storage directory (creates it lazily)."""
        base = self.KB_STORAGE_DIR or str(Path(__file__).resolve().parent.parent / "data" / "kb_documents")
        Path(base).mkdir(parents=True, exist_ok=True)
        return base

    @property
    def is_pg(self) -> bool:
        return self.DB_TYPE in ("pg", "postgres", "postgresql", "kingbasees8")

    def require_database_url(self) -> str:
        if not self.DATABASE_URL:
            raise RuntimeError("Missing required env var: DATABASE_URL")
        return self.DATABASE_URL

    def require_knowledge_database_url(self) -> str:
        """URL for the PG + pgvector VECTOR store (kb_embedding only).

        Falls back to DATABASE_URL when KNOWLEDGE_DATABASE_URL is unset (e.g.
        a single-PG dev deployment), but refuses to use MySQL for the vector
        store (pgvector types are PG-only). Not needed when
        VECTOR_STORE_TYPE=es (reserved backend).
        """
        url = self.KNOWLEDGE_DATABASE_URL or self.DATABASE_URL
        if not url:
            raise RuntimeError("Missing required env var: KNOWLEDGE_DATABASE_URL")
        if url.startswith("mysql"):
            raise RuntimeError(
                "KNOWLEDGE_DATABASE_URL must be PostgreSQL (pgvector required), got mysql"
            )
        return url

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
