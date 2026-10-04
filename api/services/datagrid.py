"""数据查询（DataGrid）服务 —— 迁移 data-synth 的 datagrid 功能。

能力（对齐 ds datagrid-actions + python-api-client）：
  - list_datagrid_datasources  ：数据源列表（dsType 过滤 + 团队授权）
  - execute_sql                ：连接数据源执行 SQL，返回 columns + rows
  - get_tables / get_columns / get_table_ddl / get_table_info
  - get_views / get_functions / get_procedures / get_sequences

连接层复用 api.services.datasource 的 URL 解析与口令解密
（_parse_jdbc_url / _reveal_secret / DatasourceEntry）。
支持 mysql / postgres（含 kingbasees8、dm 走 pg 兼容）/ trino（有 driver 时）。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from api.models.framework import Datasource, TeamDsMap
from api.services.datasource import (
    DatasourceEntry,
    _parse_jdbc_url,
    _parse_ds_conf,
    _reveal_secret,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _reveal_datagrid_secret(value: str | None) -> str | None:
    """数据查询口令解密：明文 / AES(enc:v1) 两种形态。

    SANSSEC1 国密信封不再支持（HSM 不可达，解密逻辑已移除），返回 None
    表示口令不可用 —— 调用方应给出明确提示，而不是拿信封原文当密码撞库。
    """
    if not value:
        return None
    if value.startswith("SANSSEC1|"):
        return None
    return _reveal_secret(value)


# ds 数据查询支持的数据源类型
SUPPORTED_DS_TYPES = (
    "mysql",
    "postgresql",
    "postgres",
    "dm",
    "oracle",
    "elasticsearch",
    "kingbasees8",
    "hive",
    "trino",
    "trinodb",
)

MAX_ROWS = 500  # 单次查询最大返回行数（防大表拖垮）
CONNECT_TIMEOUT_S = 8


def list_datagrid_datasources(db: Session, user_id: str, team_name: str = "") -> list[dict]:
    """当前用户可见的数据源列表（对齐 ds findDsList）。"""
    # 团队授权：admin/ROOT 全量；其他用户仅 modo_team_ds_map（按 team_name）内授权
    rows = db.execute(
        select(Datasource).where(
            Datasource.ds_type.in_(SUPPORTED_DS_TYPES),
            or_(Datasource.state.is_(None), Datasource.state == "1"),
        )
    ).scalars().all()
    allowed_names: set[str] | None = None
    if user_id not in ("admin", "ROOT", "") and team_name not in ("ROOT", "", "默认团队"):
        mapped = db.execute(
            select(TeamDsMap.ds_name).where(TeamDsMap.team_name == team_name)
        ).scalars().all()
        allowed_names = set(mapped)
    out: list[dict] = []
    for i, ds in enumerate(sorted(rows, key=lambda x: (x.name or ""))):
        if allowed_names is not None and ds.name not in allowed_names:
            continue
        conf = _parse_ds_conf(ds.ds_conf)
        out.append(
            {
                "dsName": ds.name,
                "dsLabel": ds.label or ds.name,
                "dsType": ds.ds_type,
                "dsId": ds.id,
                "dsCategory": ds.ds_category,
                "schema": conf.get("dsSchema", ""),
                "teamDsList": [
                    {
                        "dsCategory": ds.ds_category,
                        "dsCount": i + 1,
                        "dsId": ds.id,
                        "dsProfile": "dev",
                        "dsProfileLabel": "开发",
                        "dsType": ds.ds_type,
                        "dsUserId": None,
                        "schema": conf.get("dsSchema", ""),
                    }
                ],
            }
        )
    return out


def _find_entry(db: Session, ds_name: str, user_id: str = "", team_name: str = "") -> DatasourceEntry | None:
    """按 dsName 查找数据源并解密口令（带团队授权校验）。"""
    if not ds_name:
        return None
    ds = db.execute(
        select(Datasource).where(Datasource.name == ds_name)
    ).scalars().first()
    if not ds:
        return None
    # SANSSEC1 国密信封数据源：不再支持解密，哨兵标记由 _connect 拦截给出明确提示
    # （不拿密文当密码撞库，也不在 _find_entry 抛异常以免 10 个调用点都要 try）
    _SANSEC_UNSUPPORTED = "SANSSEC1|UNSUPPORTED|"
    if (ds.ds_auth or "").startswith("SANSSEC1|"):
        return DatasourceEntry(
            id=ds.id,
            name=ds.name or "",
            label=ds.label or "",
            ds_type=ds.ds_type or "",
            ds_version=ds.ds_version or "",
            ds_category=ds.ds_category or "",
            url=ds.url or "",
            state=ds.state or "",
            ds_acct=ds.ds_acct,
            ds_auth=_SANSEC_UNSUPPORTED,
            ds_conf=ds.ds_conf,
        )
    if user_id not in ("admin", "ROOT", "") and team_name not in ("ROOT", "", "默认团队"):
        mapped = db.execute(
            select(TeamDsMap.ds_name).where(
                TeamDsMap.team_name == team_name, TeamDsMap.ds_name == ds_name
            )
        ).scalars().first()
        if not mapped:
            return None
    return DatasourceEntry(
            id=ds.id,
            name=ds.name or "",
            label=ds.label or "",
            ds_type=ds.ds_type or "",
            ds_version=ds.ds_version or "",
            ds_category=ds.ds_category or "",
            url=ds.url or "",
            state=ds.state or "",
            ds_acct=ds.ds_acct,
            # 口令两种形态（对齐 ds revealStoredSecret）：
            #   明文 / AES(enc:v1) → _reveal_secret
            ds_auth=_reveal_datagrid_secret(ds.ds_auth),
            ds_conf=ds.ds_conf,
        )


def _build_uri(entry: DatasourceEntry) -> str:
    """构造带凭据的 SQLAlchemy URI（mysql / postgres 系；sqlite 原样用于测试）。"""
    uri = _parse_jdbc_url(entry.url or "", entry.ds_type)
    if uri.startswith("sqlite://"):
        return uri
    acct = entry.ds_acct or ""
    auth = entry.ds_auth or ""
    if uri.startswith("mysql://"):
        prefix = "mysql+pymysql://"
    else:
        prefix = "postgresql+psycopg2://"
    # _parse_jdbc_url 已把 jdbc:xxx 转成 driver://，但可能不含凭据 → 补 acct/auth
    body = uri
    if "://" in body:
        scheme, _, rest = body.partition("://")
        # 保留原 host/port/db，凭据用 acct/auth
        host_port_db = rest.split("@")[-1] if "@" in rest else rest
        from urllib.parse import quote_plus

        cred = f"{quote_plus(acct)}:{quote_plus(auth)}@" if acct else ""
        body = f"{scheme}://{cred}{host_port_db}"
    # body 形如 mysql://user:***@host/db?useUnicode=true&...
    # 丢弃全部 JDBC query 参数：pymysql/psycopg2 不认 useUnicode / zeroDateTimeBehavior /
    # allowMultiQueries / useSSL 等（那是 MySQLdb 的参数），透传会 Connection.__init__ 报错；
    # 走 URI query 传 charset 也会与 connect_args 冲突，统一由 _connect 用 connect_args 指定。
    head = body.split("?", 1)[0]
    return prefix + head.split("://", 1)[1]


def _connect(entry: DatasourceEntry) -> Engine:
    # SANSSEC1 国密信封哨兵：明确拒绝，不发起连接
    if (entry.ds_auth or "").startswith("SANSSEC1|"):
        raise ValueError(
            f"数据源 {entry.name or entry.id} 口令为国密信封(SANSSEC1)格式，"
            "已不支持该口令类型，无法连接（请在数据源管理中改用明文或 AES 口令）"
        )
    uri = _build_uri(entry)
    if uri.startswith("sqlite://"):
        # sqlite 驱动不认 connect_timeout（测试/轻量场景）
        return create_engine(uri)
    connect_args: dict[str, Any] = {"connect_timeout": CONNECT_TIMEOUT_S}
    if uri.startswith("mysql"):
        # 编码走 connect_args（JDBC 的 characterEncoding 已在 _build_uri 丢弃）
        connect_args["charset"] = "utf8mb4"
    return create_engine(uri, pool_pre_ping=True, connect_args=connect_args)


def _rows_to_dicts(cursor) -> tuple[list[str], list[dict]]:
    """把结果游标转成 columns + rows（dict 列表，最大 MAX_ROWS）。"""
    cols = list(cursor.keys()) if hasattr(cursor, "keys") else []
    rows: list[dict] = []
    for i, row in enumerate(cursor):
        if i >= MAX_ROWS:
            break
        if isinstance(row, dict):
            rows.append(dict(row))
        else:
            vals = list(row)
            rows.append(dict(zip(cols, vals)) if cols else {"_row": vals})
    return cols, rows


def execute_sql(db: Session, ds_name: str, sql: str, user_id: str = "", team_name: str = "") -> dict:
    """执行 SQL（任意语句；select 返回 columns+rows，其余返回 rowcount）。"""
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    if not sql or not sql.strip():
        return {"success": False, "msg": "SQL 不能为空"}
    try:
        engine = _connect(entry)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "msg": f"执行失败: {e}"}
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql(sql)
            try:
                cols, rows = _rows_to_dicts(result)
                result_type = "select"
            except Exception:
                conn.commit()
                return {
                    "success": True,
                    "resultType": "execute",
                    "rowcount": getattr(result, "rowcount", None),
                    "columns": [],
                    "rows": [],
                    "msg": "执行成功",
                }
        return {
            "success": True,
            "resultType": result_type,
            "columns": cols,
            "rows": rows,
            "rowcount": len(rows),
            "truncated": len(rows) >= MAX_ROWS,
            "msg": "查询成功",
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("datagrid execute_sql failed: %s", e)
        return {"success": False, "msg": f"执行失败: {e}"}
    finally:
        engine.dispose()


def _query_meta(db: Session, ds_name: str, query: str, user_id: str = "", params: dict | None = None, team_name: str = "") -> dict:
    """通用元数据查询（information_schema）。"""
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    try:
        engine = _connect(entry)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "msg": f"查询失败: {e}"}
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            cols, rows = _rows_to_dicts(result)
        return {"success": True, "columns": cols, "rows": rows, "total": len(rows)}
    except Exception as e:  # noqa: BLE001
        logger.warning("datagrid meta query failed: %s", e)
        return {"success": False, "msg": f"查询失败: {e}"}
    finally:
        engine.dispose()


def get_tables(db: Session, ds_name: str, schema: str = "", search: str = "", limit: int = 10000, user_id: str = "", team_name: str = "") -> dict:
    """表列表（mysql: information_schema.tables；pg 系: pg_catalog）。"""
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = """
            SELECT table_name AS name, table_comment AS comment, table_type AS type
            FROM information_schema.tables
            WHERE table_schema = COALESCE(:schema, DATABASE())
            AND (:search = '' OR table_name LIKE CONCAT('%', :search, '%'))
            ORDER BY table_name
        """
        params = {"schema": schema or None, "search": search}
    else:
        sql = """
            SELECT tablename AS name, obj_description(c.oid) AS comment, 'BASE TABLE' AS type
            FROM pg_catalog.pg_tables t
            JOIN pg_catalog.pg_class c ON c.relname = t.tablename
            WHERE t.schemaname = COALESCE(:schema, 'public')
            AND (:search = '' OR t.tablename LIKE '%' || :search || '%')
            ORDER BY t.tablename
        """
        params = {"schema": schema or None, "search": search}
    return _query_meta(db, ds_name, sql, user_id, params, team_name)


def get_columns(db: Session, ds_name: str, table: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = """
            SELECT column_name AS name, column_type AS data_type, is_nullable,
                   column_default AS default_value, column_comment AS comment
            FROM information_schema.columns
            WHERE table_schema = COALESCE(:schema, DATABASE()) AND table_name = :table
            ORDER BY ordinal_position
        """
        params = {"schema": schema or None, "table": table}
    else:
        sql = """
            SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS data_type,
                   NOT a.attnotnull AS is_nullable, pg_get_expr(d.adbin, d.adrelid) AS default_value,
                   col_description(a.attrelid, a.attnum) AS comment
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = (:schema || '.' || :table)::regclass AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """
        params = {"schema": schema or "public", "table": table}
    return _query_meta(db, ds_name, sql, user_id, params, team_name)


def get_table_ddl(db: Session, ds_name: str, table: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = f"SHOW CREATE TABLE `{schema or ''}`.`{table}`" if schema else f"SHOW CREATE TABLE `{table}`"
        # SHOW 语句不能参数化
        try:
            engine = _connect(entry)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "msg": f"获取DDL失败: {e}"}
        try:
            with engine.connect() as conn:
                result = conn.exec_driver_sql(sql)
                row = result.fetchone()
                ddl = (row[1] if row and len(row) > 1 else (row[0] if row else "")) or ""
            return {"success": True, "ddl": ddl}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "msg": f"获取DDL失败: {e}"}
        finally:
            engine.dispose()
    # pg 系：pg_get_viewdef / pg_get_tabledef 没有直接函数，用 pg_dump 风格拼接简化：
    sql = """
        SELECT 'CREATE TABLE ' || c.relname || ' (\n' ||
               string_agg('  ' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ',\n') ||
               '\n);' AS ddl
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = :schema AND c.relname = :table AND a.attnum > 0 AND NOT a.attisdropped
        GROUP BY c.relname
    """
    return _query_meta(db, ds_name, sql, user_id, {"schema": schema or "public", "table": table})


def get_table_info(db: Session, ds_name: str, table: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = """
            SELECT table_name AS name, engine, table_rows AS row_count,
                   table_comment AS comment, create_time AS created_at
            FROM information_schema.tables
            WHERE table_schema = COALESCE(:schema, DATABASE()) AND table_name = :table
        """
        params = {"schema": schema or None, "table": table}
    else:
        sql = """
            SELECT c.relname AS name, c.reltuples::bigint AS row_count,
                   obj_description(c.oid) AS comment
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND c.relname = :table
        """
        params = {"schema": schema or "public", "table": table}
    return _query_meta(db, ds_name, sql, user_id, params, team_name)


def get_views(db: Session, ds_name: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = """
            SELECT table_name AS name, table_comment AS comment
            FROM information_schema.tables
            WHERE table_schema = COALESCE(:schema, DATABASE()) AND table_type = 'VIEW'
            ORDER BY table_name
        """
        params = {"schema": schema or None}
    else:
        sql = """
            SELECT viewname AS name
            FROM pg_catalog.pg_views
            WHERE schemaname = :schema
            ORDER BY viewname
        """
        params = {"schema": schema or "public"}
    return _query_meta(db, ds_name, sql, user_id, params, team_name)


def get_functions(db: Session, ds_name: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = """
            SELECT routine_name AS name, routine_type AS type
            FROM information_schema.routines
            WHERE routine_schema = COALESCE(:schema, DATABASE())
            ORDER BY routine_name
        """
        params = {"schema": schema or None}
    else:
        sql = """
            SELECT p.proname AS name, pg_get_function_identity_arguments(p.oid) AS signature
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = :schema
            ORDER BY p.proname
        """
        params = {"schema": schema or "public"}
    return _query_meta(db, ds_name, sql, user_id, params, team_name)


def get_procedures(db: Session, ds_name: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        sql = """
            SELECT routine_name AS name
            FROM information_schema.routines
            WHERE routine_schema = COALESCE(:schema, DATABASE()) AND routine_type = 'PROCEDURE'
            ORDER BY routine_name
        """
        params = {"schema": schema or None}
    else:
        sql = """
            SELECT p.proname AS name
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = :schema AND p.prokind = 'p'
            ORDER BY p.proname
        """
        params = {"schema": schema or "public"}
    return _query_meta(db, ds_name, sql, user_id, params, team_name)


def get_sequences(db: Session, ds_name: str, schema: str = "", user_id: str = "", team_name: str = "") -> dict:
    entry = _find_entry(db, ds_name, user_id, team_name)
    if entry is None:
        return {"success": False, "msg": f"数据源不存在或未授权: {ds_name}"}
    is_mysql = entry.ds_type in ("mysql", "dm")
    if is_mysql:
        # MySQL 无独立 sequence 对象，返回空
        return {"success": True, "columns": ["name"], "rows": [], "total": 0}
    sql = """
        SELECT sequence_name AS name
        FROM information_schema.sequences
        WHERE sequence_schema = :schema
        ORDER BY sequence_name
    """
    return _query_meta(db, ds_name, sql, user_id, {"schema": schema or "public"})
