"""Startup schema migration runner (Alembic).

api-server 启动时自动把框架库 schema 升到最新（Alembic upgrade head）。
- 只管理框架库（DATABASE_URL，见 alembic.ini/env.py）；向量库 kb_embedding
  仍走 scripts/knowledge_schema.sql（HNSW 索引 alembic 表达不了）。
- 幂等：已是最新时 no-op；首次启动对老库先 stamp 基线（scripts 说明）。
- 受 AUTO_MIGRATE_ON_START 控制（默认开）；sqlite/未配置 DATABASE_URL 跳过。
- 迁移前先确保 alembic 版本表列宽足够（见 ensure_version_table_width）：
  alembic 建表默认 version_num VARCHAR(32)，而本仓库 revision id 会超过 32 字符
  （0025_chat_attachment_image_object = 33），否则迁移会在写版本号时
  1406 Data too long → 启动失败（fail-fast → 容器重启循环 → 整站 502）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent

#: Alembic 默认版本表名（migrations/env.py 未自定义 version_table）
VERSION_TABLE = "alembic_version"
#: 版本列目标列宽。alembic 建表默认 32，长 revision id 会被 MySQL 拒绝
VERSION_COLUMN_LENGTH = 255


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def create_version_table_ddl(dialect: str) -> str | None:
    """新建版本表用的 DDL（version_num VARCHAR(255)），不支持则返回 None。

    alembic 自己在缺表时建的是 VARCHAR(32)：新库从 base 跑到长 revision id 时
    同样会写版本号失败，所以缺表也要按宽列预建。
    """
    if dialect in ("mysql", "mariadb"):
        return (
            f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} ("
            f"version_num VARCHAR({VERSION_COLUMN_LENGTH}) NOT NULL, "
            "PRIMARY KEY (version_num)) "
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )
    if dialect == "postgresql":
        return (
            f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} "
            f"(version_num VARCHAR({VERSION_COLUMN_LENGTH}) NOT NULL, PRIMARY KEY (version_num))"
        )
    return None


def widen_version_column_ddl(dialect: str) -> str | None:
    """老库放宽已有 version_num 列的 DDL，不支持则返回 None。"""
    if dialect in ("mysql", "mariadb"):
        return f"ALTER TABLE {VERSION_TABLE} MODIFY COLUMN version_num VARCHAR({VERSION_COLUMN_LENGTH}) NOT NULL"
    if dialect == "postgresql":
        return f"ALTER TABLE {VERSION_TABLE} ALTER COLUMN version_num TYPE VARCHAR({VERSION_COLUMN_LENGTH})"
    return None


def ensure_version_table_width(engine) -> bool:
    """确保 alembic 版本表存在且 version_num 列宽足够（幂等）。

    返回 True 表示本次执行了 DDL。sqlite 等不校验长度的方言 no-op。
    失败只告警不抛出：迁移本身的错误仍由 command.upgrade 抛出（fail-fast 语义不变）。
    """
    from sqlalchemy import inspect, text

    dialect = engine.dialect.name
    try:
        inspector = inspect(engine)
        if VERSION_TABLE not in inspector.get_table_names():
            ddl = create_version_table_ddl(dialect)
            if not ddl:
                return False
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info("[migrate] 预建 %s（version_num VARCHAR(%s)）", VERSION_TABLE, VERSION_COLUMN_LENGTH)
            return True

        columns = {c["name"]: c for c in inspector.get_columns(VERSION_TABLE)}
        column = columns.get("version_num")
        if column is None:
            return False
        length = getattr(column.get("type"), "length", None)
        if length is not None and length >= VERSION_COLUMN_LENGTH:
            return False
        ddl = widen_version_column_ddl(dialect)
        if not ddl:
            return False
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info(
            "[migrate] 放宽 %s.version_num 列宽：%s → %s（长 revision id 写入需要）",
            VERSION_TABLE,
            length,
            VERSION_COLUMN_LENGTH,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — 只告警，不吞掉后续 upgrade 的真实错误
        logger.warning("[migrate] 检查/放宽 %s 列宽失败（继续执行迁移）：%s", VERSION_TABLE, exc)
        return False


def _ensure_version_table_width(url: str) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        ensure_version_table_width(engine)
    finally:
        engine.dispose()


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
    _ensure_version_table_width(url)
    logger.info("[migrate] alembic upgrade head (framework store)")
    command.upgrade(cfg, "head")
    logger.info("[migrate] schema up to date")
