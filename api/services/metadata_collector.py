"""元数据采集器（迁移 data-synth src/lib/collectors/* 的 Python 实现）。

与 ds 的差异：
- ds 是 TypeScript（mysql2/pg/oracle… 各一个类）跑在 Next.js server action；
  kb 是 Python，用 SQLAlchemy + 各方言驱动，连接层复用 datagrid._find_entry/_connect。
- ds 的 collector 与「原始数据集元素 / 需求绑定」耦合，kb 无这些概念，本实现
  只做「采集表结构 + 字段」并落 kb_metadata_table / kb_metadata_column。

支持的 ds_type（与 ds 的 COLLECTABLE_DS_TYPE_GROUPS 对齐）：
    mysql / goldendb / postgresql / postgres / pg / kingbasees8 / dm
    oracle / sqlserver / mssql / clickhouse / trino / trinodb / hive
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from api.models.framework import Datasource
from api.models.metadata import MetadataColumn, MetadataTable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 可采集类型（对齐 ds 的 COLLECTABLE_DS_TYPE_GROUPS 展平）
# ---------------------------------------------------------------------------
COLLECTABLE_DS_TYPES: tuple[str, ...] = (
    "mysql",
    "goldendb",
    "postgresql",
    "postgres",
    "pg",
    "kingbasees8",
    "dm",
    "oracle",
    "sqlserver",
    "mssql",
    "clickhouse",
    "trino",
    "trinodb",
    "hive",
)

# 类型归组（前端下拉用，对齐 ds 的分组展示）
DS_TYPE_GROUPS: dict[str, str] = {
    "mysql": "MySQL",
    "goldendb": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "pg": "PostgreSQL",
    "kingbasees8": "PostgreSQL",
    "dm": "PostgreSQL",
    "oracle": "Oracle",
    "sqlserver": "SQL Server",
    "mssql": "SQL Server",
    "clickhouse": "ClickHouse",
    "trino": "Trino",
    "trinodb": "Trino",
    "hive": "Hive",
}


def now_stamp() -> str:
    """统一时间戳格式 'YYYY-MM-DD HH:MM:SS'（与 ds Job/collection_time 一致）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dt(v: Any) -> str | None:
    """DB 里的时间值统一转字符串（datetime/date/str/其他）。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):  # datetime.date（不含 datetime 子类，已在上分支处理）
        return str(v)
    s = str(v)
    return s[:32] if s else None


def _norm_table_type(v: Any) -> str:
    """表类型归一：BASE TABLE/VIEW/TABLE → TABLE|VIEW。"""
    s = str(v or "").upper()
    if "VIEW" in s:
        return "VIEW"
    return "TABLE"


def new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# 表元数据 SQL（各数据库 information_schema / 系统表）
# ---------------------------------------------------------------------------
def _tables_sql(ds_type: str, schema: str) -> str:
    """表列表 SQL。schema 为空时按 ds 各自默认库/模式。"""
    if ds_type in ("mysql", "goldendb"):
        return f"""
            SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name,
                   TABLE_TYPE AS table_type, TABLE_COMMENT AS table_comment,
                   TABLE_ROWS AS row_count,
                   (DATA_LENGTH + INDEX_LENGTH) AS table_size,
                   CREATE_TIME AS create_time, UPDATE_TIME AS update_time
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = {schema!r}
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
    if ds_type in ("postgresql", "postgres", "pg", "kingbasees8", "dm"):
        # reltuples 为估算行数（与 ds 一致取估算值而非 count(*)，避免大表扫全表）
        return f"""
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   CASE c.relkind WHEN 'v' THEN 'VIEW' ELSE 'TABLE' END AS table_type,
                   obj_description(c.oid) AS table_comment,
                   c.reltuples::bigint AS row_count,
                   pg_total_relation_size(c.oid) AS table_size,
                   NULL AS create_time, NULL AS update_time
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname = COALESCE(NULLIF({schema!r}, ''), 'public')
            ORDER BY n.nspname, c.relname
        """
    if ds_type == "oracle":
        return f"""
            SELECT t.OWNER AS schema_name, t.TABLE_NAME AS table_name,
                   t.TABLE_TYPE AS table_type, c.COMMENTS AS table_comment,
                   t.NUM_ROWS AS row_count, t.BLOCKS * 8192 AS table_size,
                   t.CREATED AS create_time, t.LAST_DDL_TIME AS update_time
            FROM ALL_TABLES t
            LEFT JOIN ALL_TAB_COMMENTS c
                   ON c.OWNER = t.OWNER AND c.TABLE_NAME = t.TABLE_NAME
            WHERE t.OWNER = COALESCE(NULLIF({schema!r}, ''), USER)
            ORDER BY t.OWNER, t.TABLE_NAME
        """
    if ds_type in ("sqlserver", "mssql"):
        return f"""
            SELECT s.name AS schema_name, t.name AS table_name,
                   CASE t.is_ms_shipped WHEN 1 THEN 'VIEW' ELSE 'TABLE' END AS table_type,
                   CAST(ep.value AS NVARCHAR(400)) AS table_comment,
                   p.rows AS row_count, 0 AS table_size,
                   t.create_date AS create_time, t.modify_date AS update_time
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            LEFT JOIN sys.partitions p
                   ON p.object_id = t.object_id AND p.index_id IN (0, 1)
            LEFT JOIN sys.extended_properties ep
                   ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE s.name = COALESCE(NULLIF({schema!r}, ''), 'dbo')
            ORDER BY s.name, t.name
        """
    if ds_type == "clickhouse":
        return f"""
            SELECT database AS schema_name, name AS table_name,
                   CASE engine LIKE '%View' THEN 'VIEW' ELSE 'TABLE' END AS table_type,
                   comment AS table_comment,
                   total_rows AS row_count,
                   (total_bytes) AS table_size,
                   metadata_modification_time AS create_time, metadata_modification_time AS update_time
            FROM system.tables
            WHERE database = COALESCE(NULLIF({schema!r}, ''), currentDatabase())
            ORDER BY database, name
        """
    # hive / trino / trinodb：两者都暴露标准 information_schema
    return f"""
        SELECT table_schema AS schema_name, table_name AS table_name,
               table_type AS table_type, NULL AS table_comment,
               NULL AS row_count, NULL AS table_size,
               NULL AS create_time, NULL AS update_time
        FROM information_schema.tables
        WHERE table_schema = COALESCE(NULLIF({schema!r}, ''), 'default')
        ORDER BY table_schema, table_name
    """


def _columns_sql(ds_type: str, schema: str, table: str) -> str:
    """字段列表 SQL（单表）。"""
    if ds_type in ("mysql", "goldendb"):
        return f"""
            SELECT COLUMN_NAME AS column_name, COLUMN_TYPE AS column_type,
                   DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS column_length,
                   NUMERIC_PRECISION AS column_precision, NUMERIC_SCALE AS column_scale,
                   IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default,
                   COLUMN_COMMENT AS column_comment, ORDINAL_POSITION AS ordinal_position,
                   (COLUMN_KEY = 'PRI') AS is_primary_key,
                   (COLUMN_KEY = 'UNI') AS is_unique
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = {schema!r} AND TABLE_NAME = {table!r}
            ORDER BY ORDINAL_POSITION
        """
    if ds_type in ("postgresql", "postgres", "pg", "kingbasees8", "dm"):
        return f"""
            SELECT a.attname AS column_name,
                   format_type(a.atttypid, a.atttypmod) AS column_type,
                   t.typname AS data_type,
                   COALESCE(a.atttypmod, -1) AS column_length,
                   information_schema._pg_char_max_length(a.atttypid, a.atttypmod) AS column_precision,
                   information_schema._pg_numeric_precision(a.atttypid, a.atttypmod) AS column_scale,
                   CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
                   pg_get_expr(ad.adbin, ad.adrelid) AS column_default,
                   col_description(a.attrelid, a.attnum) AS column_comment,
                   a.attnum AS ordinal_position,
                   (a.attnotnull AND i.indisprimary) AS is_primary_key,
                   COALESCE(i.indisunique, FALSE) AS is_unique
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
            LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            LEFT JOIN pg_catalog.pg_index i ON i.indrelid = a.attrelid AND a.attnum = ANY(i.indkey)
            WHERE n.nspname = {schema!r} AND c.relname = {table!r}
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """
    if ds_type == "oracle":
        return f"""
            SELECT c.COLUMN_NAME AS column_name, c.DATA_TYPE AS column_type,
                   c.DATA_TYPE AS data_type, c.DATA_LENGTH AS column_length,
                   c.DATA_PRECISION AS column_precision, c.DATA_SCALE AS column_scale,
                   c.NULLABLE AS is_nullable, c.DATA_DEFAULT AS column_default,
                   m.COMMENTS AS column_comment, c.COLUMN_ID AS ordinal_position,
                   (CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END) AS is_primary_key,
                   0 AS is_unique
            FROM ALL_TAB_COLUMNS c
            LEFT JOIN ALL_COL_COMMENTS m
                   ON m.OWNER = c.OWNER AND m.TABLE_NAME = c.TABLE_NAME
                  AND m.COLUMN_NAME = c.COLUMN_NAME
            LEFT JOIN (
                SELECT cc.OWNER, cc.TABLE_NAME, cc.COLUMN_NAME
                FROM ALL_CONSTRAINTS cons
                JOIN ALL_CONS_COLUMNS cc
                  ON cc.OWNER = cons.OWNER AND cc.CONSTRAINT_NAME = cons.CONSTRAINT_NAME
                WHERE cons.CONSTRAINT_TYPE = 'P'
            ) pk ON pk.OWNER = c.OWNER AND pk.TABLE_NAME = c.TABLE_NAME
                   AND pk.COLUMN_NAME = c.COLUMN_NAME
            WHERE c.OWNER = {schema!r} AND c.TABLE_NAME = {table!r}
            ORDER BY c.COLUMN_ID
        """
    if ds_type in ("sqlserver", "mssql"):
        return f"""
            SELECT c.name AS column_name, ty.name AS column_type,
                   ty.name AS data_type, c.max_length AS column_length,
                   c.precision AS column_precision, c.scale AS column_scale,
                   CASE c.is_nullable WHEN 1 THEN 'YES' ELSE 'NO' END AS is_nullable,
                   dc.definition AS column_default,
                   CAST(ep.value AS NVARCHAR(400)) AS column_comment,
                   c.column_id AS ordinal_position,
                   (CASE WHEN i.is_primary_key = 1 THEN 1 ELSE 0 END) AS is_primary_key,
                   (CASE WHEN i.is_unique = 1 THEN 1 ELSE 0 END) AS is_unique
            FROM sys.columns c
            JOIN sys.tables t ON t.object_id = c.object_id
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.types ty ON ty.user_type_id = c.user_type_id
            LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
            LEFT JOIN sys.index_columns ic
                   ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            LEFT JOIN sys.indexes i
                   ON i.object_id = ic.object_id AND i.index_id = ic.index_id
            LEFT JOIN sys.extended_properties ep
                   ON ep.major_id = c.object_id AND ep.minor_id = c.column_id
                  AND ep.name = 'MS_Description'
            WHERE s.name = {schema!r} AND t.name = {table!r}
            ORDER BY c.column_id
        """
    if ds_type == "clickhouse":
        return f"""
            SELECT name AS column_name, type AS column_type, type AS data_type,
                   NULL AS column_length, NULL AS column_precision, NULL AS column_scale,
                   'YES' AS is_nullable, default_expression AS column_default,
                   comment AS column_comment, position AS ordinal_position,
                   0 AS is_primary_key, 0 AS is_unique
            FROM system.columns
            WHERE database = {schema!r} AND table = {table!r}
            ORDER BY position
        """
    return f"""
        SELECT column_name, data_type, data_type AS column_type,
               character_maximum_length AS column_length,
               numeric_precision AS column_precision, numeric_scale AS column_scale,
               is_nullable, column_default, NULL AS column_comment,
               ordinal_position, 0 AS is_primary_key, 0 AS is_unique
        FROM information_schema.columns
        WHERE table_schema = {schema!r} AND table_name = {table!r}
        ORDER BY ordinal_position
    """


# ---------------------------------------------------------------------------
# 采集执行
# ---------------------------------------------------------------------------
def default_schema_for(ds_type: str, url: str, ds_conf: str | None) -> str:
    """推断默认 schema：ds_conf.dsSchema 优先，其次从 URL 取库名。"""
    try:
        conf = json.loads(ds_conf) if ds_conf else {}
        s = str(conf.get("dsSchema") or "").strip()
        if s:
            return s
    except (TypeError, ValueError):
        pass
    # 从 URL 取库名：mysql://u:p@h:3306/mydb?... / jdbc:postgresql://h/db
    u = url or ""
    if "://" in u:
        tail = u.split("://", 1)[1]
        path = tail.split("?", 1)[0]
        if "/" in path:
            db = path.split("/", 1)[1]
            if db:
                return db
    if ds_type in ("mysql", "goldendb"):
        return ""
    if ds_type in ("postgresql", "postgres", "pg", "kingbasees8", "dm"):
        return "public"
    if ds_type in ("sqlserver", "mssql"):
        return "dbo"
    if ds_type == "oracle":
        return ""
    return "default"


def _run_meta_query(engine, sql: str) -> list[dict]:
    """执行元数据查询并转 dict 列表（跨方言统一行格式）。"""
    from sqlalchemy import text as _text

    with engine.connect() as conn:
        result = conn.execute(_text(sql))
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


def _target_predicate(ds_type: str, targets: list[dict]) -> str | None:
    """构造定向采集的 WHERE 条件（对齐 ds buildCollectionTargetPredicate）。"""
    if not targets:
        return None
    clauses: list[str] = []
    for t in targets:
        schema = t.get("schema_name") or ""
        table = t.get("table_name") or ""
        if not table:
            continue
        parts = []
        if schema and ds_type in ("mysql", "goldendb"):
            parts.append(f"TABLE_SCHEMA = {schema!r}")
        parts.append(f"TABLE_NAME = {table!r}" if ds_type in ("mysql", "goldendb")
                     else f"table_name = {table!r}")
        if parts:
            clauses.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(clauses) if clauses else None


def collect_from_datasource(
    ds: Datasource,
    targets: list[dict] | None = None,
    progress: Any = None,
) -> tuple[list[dict], list[dict]]:
    """连接数据源，采集表 + 字段元数据。

    返回 (tables, columns)；columns 每行含 metadata_table_id（暂用 schema.table 组合键，
    落库时映射为真实 kb_metadata_table.id）。
    """
    from api.services.datagrid import _connect, _find_entry
    from api.db import get_sessionmaker

    ds_type = (ds.ds_type or "").lower()
    if ds_type not in COLLECTABLE_DS_TYPES:
        raise ValueError(f"暂不支持的数据源类型: {ds.ds_type}")

    schema = default_schema_for(ds_type, ds.url or "", ds.ds_conf)
    # 找 entry（含口令解密）；_find_entry 需要 db session（用 admin 身份绕过授权过滤）
    s = get_sessionmaker()()
    try:
        entry = _find_entry(s, ds.name or "", "admin", "ROOT")
    finally:
        s.close()
    if entry is None:
        raise ValueError(f"数据源不存在或口令不可用: {ds.name}")

    engine = _connect(entry)
    tables: list[dict] = []
    columns: list[dict] = []

    t_sql = _tables_sql(ds_type, schema)
    pred = _target_predicate(ds_type, targets or [])
    if pred:
        # 定向采集：把条件 AND 到最外层 WHERE（各 SQL 均以 WHERE 开头）
        t_sql = t_sql.replace("WHERE ", f"WHERE ({pred}) AND ", 1)
    if progress:
        progress(f"采集表列表 (schema={schema})…")
    table_rows = _run_meta_query(engine, t_sql)
    tables = [
        {
            "schema_name": r.get("schema_name") or schema or "",
            "table_name": r.get("table_name"),
            "table_type": _norm_table_type(r.get("table_type")),
            "table_comment": r.get("table_comment"),
            "row_count": int(r["row_count"]) if r.get("row_count") is not None else None,
            "table_size": int(r["table_size"]) if r.get("table_size") is not None else None,
            "create_time": _fmt_dt(r.get("create_time")),
            "update_time": _fmt_dt(r.get("update_time")),
        }
        for r in table_rows
        if r.get("table_name")
    ]

    if progress:
        progress(f"采集字段（{len(tables)} 张表）…")
    for t in tables:
        c_sql = _columns_sql(ds_type, t["schema_name"], t["table_name"])
        try:
            rows = _run_meta_query(engine, c_sql)
        except Exception as e:  # noqa: BLE001 单表失败不中断整体采集
            logger.warning("collect columns failed for %s.%s: %s", t["schema_name"], t["table_name"], e)
            continue
        for r in rows:
            if not r.get("column_name"):
                continue
            columns.append(
                {
                    "table_key": f"{t['schema_name']}.{t['table_name']}",
                    "column_name": r.get("column_name"),
                    "column_type": r.get("column_type"),
                    "data_type": r.get("data_type"),
                    "column_length": int(r["column_length"]) if r.get("column_length") is not None else None,
                    "column_precision": int(r["column_precision"]) if r.get("column_precision") is not None else None,
                    "column_scale": int(r["column_scale"]) if r.get("column_scale") is not None else None,
                    "is_nullable": str(r.get("is_nullable") or "YES")[:3],
                    "column_default": r.get("column_default"),
                    "column_comment": r.get("column_comment"),
                    "ordinal_position": int(r["ordinal_position"]) if r.get("ordinal_position") is not None else None,
                    "is_primary_key": 1 if r.get("is_primary_key") in (1, True, "1", "t", "true") else 0,
                    "is_unique": 1 if r.get("is_unique") in (1, True, "1", "t", "true") else 0,
                }
            )

    # 清理引擎
    try:
        engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    return tables, columns


def run_metadata_collection(
    db: Session,
    datasource_id: str,
    mode: str = "full",
    targets: list[dict] | None = None,
    config_snapshot: dict | None = None,
    progress: Any = None,
) -> dict:
    """采集一个数据源的元数据并落库（事务内 upsert）。返回 summary。"""
    ds = db.execute(select(Datasource).where(Datasource.id == datasource_id)).scalars().first()
    if ds is None:
        raise ValueError(f"数据源不存在: {datasource_id}")
    ds_type = (ds.ds_type or "").lower()
    if ds_type not in COLLECTABLE_DS_TYPES:
        raise ValueError(f"数据源类型不支持元数据采集: {ds.ds_type}")

    # 重新采集 = 全量覆盖：先清旧
    if mode != "incremental" or not targets:
        old_tables = db.execute(
            select(MetadataTable).where(MetadataTable.datasource_id == datasource_id)
        ).scalars().all()
        for t in old_tables:
            db.execute(delete(MetadataColumn).where(MetadataColumn.metadata_table_id == t.id))
        db.execute(delete(MetadataTable).where(MetadataTable.datasource_id == datasource_id))
        db.commit()

    tables, columns = collect_from_datasource(ds, targets=targets, progress=progress)
    if progress:
        progress(f"落库 {len(tables)} 张表 / {len(columns)} 个字段…")

    now = now_stamp()
    cfg_json = json.dumps(config_snapshot, ensure_ascii=False) if config_snapshot else None
    cols_by_key: dict[str, list[dict]] = {}
    for c in columns:
        cols_by_key.setdefault(c["table_key"], []).append(c)

    tables_inserted = 0
    columns_inserted = 0
    for t in tables:
        tid = new_id()
        db.add(
            MetadataTable(
                id=tid,
                datasource_id=datasource_id,
                schema_name=t["schema_name"],
                table_name=t["table_name"],
                table_type=t["table_type"],
                table_comment=t["table_comment"],
                row_count=t["row_count"],
                table_size=t["table_size"],
                create_time=t["create_time"],
                update_time=t["update_time"],
                collection_time=now,
                collection_config=cfg_json,
                state="active",
            )
        )
        tables_inserted += 1
        for c in cols_by_key.get(f"{t['schema_name']}.{t['table_name']}", []):
            db.add(
                MetadataColumn(
                    id=new_id(),
                    metadata_table_id=tid,
                    column_name=c["column_name"],
                    column_type=c["column_type"],
                    data_type=c["data_type"],
                    column_length=c["column_length"],
                    column_precision=c["column_precision"],
                    column_scale=c["column_scale"],
                    is_nullable=c["is_nullable"],
                    column_default=c["column_default"],
                    column_comment=c["column_comment"],
                    ordinal_position=c["ordinal_position"],
                    is_primary_key=c["is_primary_key"],
                    is_unique=c["is_unique"],
                    collection_time=now,
                )
            )
            columns_inserted += 1
    db.commit()

    summary = {
        "datasource_id": datasource_id,
        "datasource_name": ds.name or "",
        "ds_type": ds_type,
        "tableCount": len(tables),
        "columnCount": columns_inserted,
        "tablesInserted": tables_inserted,
        "columnsInserted": columns_inserted,
        "collectionMode": mode,
        "targetsApplied": len(targets or []),
    }
    if progress:
        progress(f"完成：{tables_inserted} 表 / {columns_inserted} 字段")
    return summary


def delete_metadata_by_datasource(db: Session, datasource_id: str) -> dict:
    """级联删除某数据源全部表 + 字段元数据。"""
    tables = db.execute(
        select(MetadataTable).where(MetadataTable.datasource_id == datasource_id)
    ).scalars().all()
    for t in tables:
        db.execute(delete(MetadataColumn).where(MetadataColumn.metadata_table_id == t.id))
    db.execute(delete(MetadataTable).where(MetadataTable.datasource_id == datasource_id))
    db.commit()
    return {"tablesDeleted": len(tables), "success": True}