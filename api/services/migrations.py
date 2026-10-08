"""Startup schema migration runner (Alembic).

api-server 启动时自动把框架库 schema 升到最新（Alembic upgrade head）。
- 只管理框架库（DATABASE_URL，见 alembic.ini/env.py）；向量库 kb_embedding
  仍走 scripts/knowledge_schema.sql（HNSW 索引 alembic 表达不了）。
- 幂等：已是最新时 no-op；首次启动对老库先 stamp 基线（scripts 说明）。
- 受 AUTO_MIGRATE_ON_START 控制（默认开）；sqlite/未配置 DATABASE_URL 跳过。
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def run_migrations_on_startup() -> None:
    """Apply pending Alembic migrations to the framework store.

    Safe to call from FastAPI lifespan. Fail-fast: a schema that cannot be
    migrated is a broken deploy — better to refuse startup than serve 500s.
    """
    from api.config import get_settings

    settings = get_settings()
    if not settings.AUTO_MIGRATE_ON_START:
        logger.info("[migrate] AUTO_MIGRATE_ON_START=0 — skip")
        return
    url = settings.DATABASE_URL or ""
    if not url or url.startswith("sqlite"):
        logger.info("[migrate] sqlite / no DATABASE_URL — skip (tests/fresh dev)")
        return
    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    logger.info("[migrate] alembic upgrade head (framework store)")
    command.upgrade(cfg, "head")
    logger.info("[migrate] schema up to date")