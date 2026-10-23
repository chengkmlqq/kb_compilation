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


#: 框架种子 SQL（scripts/framework_seed.sql，由 scripts/export_framework_seed.py
#: 从 data_supply 导出：用户/角色/菜单/团队/数据源类型等配置型数据；
#: 不含运行时数据（modo_job/oper_log/sys_file/seq 等））。
SEED_FILE = ROOT / "scripts" / "framework_seed.sql"
#: 判空用表：modo_user 有行 = 框架种子已灌（空库才播，避免覆盖现有数据）
SEED_PROBE_TABLE = "modo_user"


def ensure_schema_and_seed(url: str) -> None:
    """空库自举：建全量 ORM 表 + 灌框架种子（幂等）。

    2026-10-21：新增 data_supply_ontology 等「空库」部署场景后，原启动
    流程只跑 Alembic 增量迁移，而 0001_baseline 是空锚点（不建表）、
    27 张框架表（modo_*）也无任何建表入口 → 空库启动后缺表 500。
    这里补齐：
      1. Base.metadata.create_all 建全部 ORM 表（已存在则跳过）
      2. 框架表为空时导入 scripts/framework_seed.sql（INSERT IGNORE 幂等）

    失败只告警不抛出：建表/播种失败不阻断启动（后续 Alembic 仍会跑，
    真实缺表由业务查询暴露），避免因权限不足导致整站无法启动。
    """
    from sqlalchemy import create_engine, inspect, text

    try:
        engine = create_engine(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[migrate] 建表引擎创建失败（跳过建表）：%s", exc)
        return
    try:
        # 1) 建全量 ORM 表（幂等）
        try:
            import api.models.chat_session  # noqa: F401
            import api.models.framework  # noqa: F401
            import api.models.knowledge  # noqa: F401
            import api.models.mcp_skill  # noqa: F401
            import api.models.metadata  # noqa: F401
            import api.models.model  # noqa: F401
            import api.models.ontology  # noqa: F401
            import api.models.orchestration  # noqa: F401
            import api.models.websearch  # noqa: F401
            from api.db import Base

            Base.metadata.create_all(engine)
            logger.info("[migrate] ORM 表已就绪（%d 张）", len(Base.metadata.tables))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[migrate] 自动建表失败（继续执行迁移）：%s", exc)

        # 2) 框架种子（仅空表时播）
        try:
            inspector = inspect(engine)
            if SEED_PROBE_TABLE not in inspector.get_table_names():
                logger.info("[migrate] 框架表 %s 不存在，跳过播种", SEED_PROBE_TABLE)
                return
            with engine.connect() as conn:
                has_rows = bool(
                    conn.execute(text(f"SELECT 1 FROM `{SEED_PROBE_TABLE}` LIMIT 1")).first()
                )
            if has_rows:
                logger.info("[migrate] 框架种子已存在（%s 非空），跳过播种", SEED_PROBE_TABLE)
                return
            if not SEED_FILE.exists():
                logger.warning("[migrate] 种子文件不存在：%s（跳过播种）", SEED_FILE)
                return
            sql_text = SEED_FILE.read_text(encoding="utf-8")
            with engine.begin() as conn:
                for stmt in _split_sql_statements(sql_text):
                    try:
                        conn.execute(text(stmt))
                    except Exception as exc:  # noqa: BLE001 — 单条失败不阻断其余
                        logger.warning("[migrate] 种子语句失败（跳过该条）：%s", exc)
            logger.info("[migrate] 框架种子已导入（%s）", SEED_FILE.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[migrate] 框架播种失败（继续执行迁移）：%s", exc)
    finally:
        engine.dispose()


def _split_sql_statements(sql_text: str) -> list[str]:
    """按分号切分 SQL（跳过空行与注释行）。种子文件由本仓脚本生成，
    值为转义后的字符串字面量，不含分号，无需复杂解析。"""
    out: list[str] = []
    for raw in sql_text.split(";"):
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        out.append(line)
    return out


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
    # 密码可能含 URL 编码的 %：configparser 插值前先转义为 %%（get 时还原）
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # 2026-10-21 空库自举：先建全量 ORM 表 + 框架种子，再 Alembic 增量
    # （0001_baseline 是空锚点、增量迁移带 _table_exists 守卫，空库/老库都幂等）。
    ensure_schema_and_seed(url)
    _ensure_version_table_width(url)
    logger.info("[migrate] alembic upgrade head (framework store)")
    command.upgrade(cfg, "head")
    logger.info("[migrate] schema up to date")
