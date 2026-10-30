"""Framework sync service — 从外部平台业务库同步 modo_* 框架表到本库。

用途：把 data_supply_shandong 的用户/团队/角色数据同步进 kb_compilation，
使 shandong 用户在 kb 换签（/api/v1/auth/jwt-exchange）与 RBAC 鉴权自动通过。

鉴权通过的充要条件（kb 侧身份解析 + RBAC）：
1. modo_user 有该 userId 且 state='1'（resolve_active_user_identity）
2. 团队关系（modo_team_member → modo_team），供 scope=team 归属解析
3. 用户有角色且角色已授权目标菜单 route（check_path_permission）
   —— 角色保底：每个同步用户至少挂一个「kb 已授权角色」（normal_user）

设计：
- 源库连接来自数据源管理（modo_datasource，dsType=mysql），复用 datasource
  服务的解密/URL 解析逻辑（find_accessible_datasource_by_id + _parse_jdbc_url）。
- 同步表：modo_team / modo_user / modo_team_member / modo_user_role /
  modo_user_role_rela；admin 用户（user_id='admin'）及其关系行除外。
- 列映射：只同步「源表列 ∩ 本库列」的交集——源库与本库列集存在差异
  （源库有 lastupd/version/acct_type/logo 等列，本库有 mcp_token_hash 等），
  交集映射避免 1054 Unknown column。
- 写入：INSERT ... ON DUPLICATE KEY UPDATE（按主键幂等，重复执行安全）。
- 角色保底：每个同步用户若其 kb 角色集与「已授权角色集」（role_menu_rela
  出现过的 role_id ∪ admin-bypass {plat-mgr, plat_mgr}）无交集，补挂 normal_user。
- 结果落 modo_sync_log（response_data=JSON 统计），status 端点读取展示。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any
from urllib.parse import urlparse

import pymysql
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from api.models.framework import SyncLog
from api.services.datasource import _parse_jdbc_url, find_accessible_datasource_by_id

logger = logging.getLogger(__name__)

SYNC_TYPE = "framework_sync"

# admin-bypass 角色（is_platform_admin / 授权特判）：持有即视为全授权面
ADMIN_BYPASS_ROLES = {"plat-mgr", "plat_mgr"}
# 保底角色：kb 授权面最全（等同 plat-mgr 的 route 授权），保证 RBAC 通过
FALLBACK_ROLE = "normal_user"

# 同步表配置：pk=主键列，exclude=源行过滤（admin 除外）
TABLES: dict[str, dict[str, Any]] = {
    "modo_team": {"pk": "team_id", "exclude": None},
    "modo_user": {"pk": "id", "exclude": "user_id <> 'admin'"},
    "modo_team_member": {"pk": "member_id", "exclude": "user_id <> 'admin'"},
    "modo_user_role": {"pk": "role_id", "exclude": None},
    "modo_user_role_rela": {"pk": "rela_id", "exclude": "user_id <> 'admin'"},
}

# 本库目标列（kb 库真实列名，实测 information_schema；仅含本库存在的列）
TARGET_COLS: dict[str, list[str]] = {
    "modo_team": [
        "team_id", "create_dt", "create_user", "descr", "label",
        "parent_team_id", "parent_team_name", "state", "team_name",
    ],
    "modo_user": [
        "id", "user_id", "user_name", "user_pwd", "email", "phone",
        "create_dt", "state", "default_team",
    ],
    "modo_team_member": [
        "member_id", "create_dt", "create_user", "state", "team_name",
        "user_id", "role_name",
    ],
    "modo_user_role": [
        "role_id", "create_date", "role_descr", "role_name", "role_type", "state",
    ],
    "modo_user_role_rela": ["rela_id", "role_id", "user_id"],
}


def _now() -> dt.datetime:
    return dt.datetime.now()


def _connect_source(ds: Any) -> pymysql.connections.Connection:
    """按数据源配置连源库（mysql）。ds 为 DatasourceEntry（已解密 ds_auth）。"""
    uri = _parse_jdbc_url(ds.url or "", ds.ds_type or "")
    parsed = urlparse(uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 3306
    dbname = parsed.path.lstrip("/") if parsed.path else None
    user = ds.ds_acct or (parsed.username or "root")
    password = ds.ds_auth if ds.ds_auth is not None else (parsed.password or "")
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=dbname,
        connect_timeout=8,
        charset="utf8mb4",
    )


def _source_columns(conn: pymysql.connections.Connection, dbname: str, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(
        "select column_name from information_schema.columns "
        "where table_schema=%s and table_name=%s",
        (dbname, table),
    )
    cols = {row[0] for row in cur.fetchall()}
    cur.close()
    return cols


def _source_rows(
    conn: pymysql.connections.Connection, dbname: str, table: str,
    cols: list[str], exclude: str | None,
) -> list[tuple]:
    col_sql = ", ".join(f"`{c}`" for c in cols)
    sql = f"select {col_sql} from `{table}`"
    if exclude:
        sql += f" where {exclude}"
    cur = conn.cursor()
    cur.execute(sql)
    rows = list(cur.fetchall())
    cur.close()
    return rows


def _upsert_rows(db: Session, table: str, cols: list[str], rows: list[tuple]) -> tuple[int, int]:
    """INSERT ... ON DUPLICATE KEY UPDATE（按主键幂等）。返回 (inserted, updated)。

    参数用 SQLAlchemy text 命名占位符（:col），值传 dict（列名→值）；
    勿用 %s（pymysql 风格，text() 会当字面 % 处理导致 1064）。
    """
    inserted = 0
    updated = 0
    if not rows or not cols:
        return inserted, updated
    pk = TABLES[table]["pk"]
    col_sql = ", ".join(f"`{c}`" for c in cols)
    update_sql = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c != pk)
    if not update_sql:
        # 只有主键列（退化情况防呆）
        for row in rows:
            placeholders = ", ".join(f":{c}" for c in cols)
            result = db.execute(
                text(f"insert ignore into `{table}` ({col_sql}) values ({placeholders})"),
                {cols[i]: row[i] for i in range(len(cols))},
            )
            if result.rowcount == 1:
                inserted += 1
        return inserted, updated
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = text(
        f"insert into `{table}` ({col_sql}) values ({placeholders})"
        f" on duplicate key update {update_sql}"
    )
    for row in rows:
        params = {cols[i]: row[i] for i in range(len(cols))}
        result = db.execute(sql, params)
        # MySQL rowcount: 1→inserted, 2→updated, 0→no change
        if result.rowcount == 1:
            inserted += 1
        elif result.rowcount == 2:
            updated += 1
    return inserted, updated


def _collect_authorized_roles(db: Session) -> set[str]:
    """kb 已授权角色集 = role_menu_rela 出现过的 role_id ∪ admin-bypass。"""
    rows = db.execute(select(text("role_id")).select_from(text("modo_role_menu_rela"))).scalars().all()
    return set(rows or []) | ADMIN_BYPASS_ROLES


def _ensure_fallback_role(db: Session, user_ids: list[str], authorized: set[str]) -> int:
    """角色保底：为无授权角色的同步用户补挂 normal_user。返回补挂数。

    authorized = 已授权角色集（role_menu_rela 出现过的 role_id ∪ admin-bypass）。
    用户若已持有任一授权角色 → 跳过；否则补挂 normal_user（kb 授权面最全）。
    """
    if not user_ids:
        return 0
    existing: dict[str, set[str]] = {}
    in_sql = ", ".join(f"'{u}'" for u in user_ids)
    rows = db.execute(
        text(f"select user_id, role_id from modo_user_role_rela where user_id in ({in_sql})")
    ).fetchall()
    for user_id, role_id in rows:
        existing.setdefault(user_id, set()).add(role_id)
    granted = 0
    for user_id in user_ids:
        if existing.get(user_id, set()) & authorized:
            continue
        db.execute(
            text(
                "insert ignore into modo_user_role_rela (rela_id, role_id, user_id) "
                "values (:rela, :role, :uid)"
            ),
            {"rela": uuid.uuid4().hex, "role": FALLBACK_ROLE, "uid": user_id},
        )
        granted += 1
    return granted


def run_framework_sync(db: Session, ds_id: str, caller_user_id: str = "") -> dict:
    """执行框架表同步。返回统计 dict（同时落 modo_sync_log）。"""
    started = _now()
    log_row = SyncLog(
        id=uuid.uuid4().hex,
        sync_type=SYNC_TYPE,
        is_full_sync="1",
        sync_status="RUNNING",  # 列 NOT NULL 无默认值，先落运行中状态
        trigger_type="manual",
        create_user=caller_user_id or None,
        start_time=started,
        query_start_time=started.strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(log_row)
    db.commit()

    try:
        ds = find_accessible_datasource_by_id(db, ds_id, identity=None)
        if not ds:
            raise ValueError("数据源不存在或不可用")
        if (ds.ds_type or "").lower() not in ("mysql", "goldendb"):
            raise ValueError(f"仅支持 mysql/goldendb 类型数据源，当前: {ds.ds_type}")

        conn = _connect_source(ds)
        try:
            dbname = (urlparse(_parse_jdbc_url(ds.url or "", ds.ds_type or "")).path or "").lstrip("/")
            results: dict[str, dict[str, Any]] = {}
            synced_user_ids: list[str] = []
            total_success = 0
            total_failed = 0

            for table, cfg in TABLES.items():
                src_cols = _source_columns(conn, dbname, table)
                cols = [c for c in TARGET_COLS[table] if c in src_cols]
                if not cols:
                    results[table] = {"source": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0, "error": "无交集列，跳过"}
                    continue
                try:
                    rows = _source_rows(conn, dbname, table, cols, cfg["exclude"])
                except Exception as exc:  # noqa: BLE001 - 单表读取失败不阻断整体
                    total_failed += 1
                    results[table] = {"source": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0, "error": f"读取失败: {exc}"}
                    continue
                skipped = 0
                if table == "modo_user":
                    # 提取 user_id 列（cols 内位置）
                    uid_idx = cols.index("user_id") if "user_id" in cols else None
                    synced_user_ids = (
                        [r[uid_idx] for r in rows if uid_idx is not None and r[uid_idx]]
                        if uid_idx is not None else []
                    )
                try:
                    inserted, updated = _upsert_rows(db, table, cols, rows)
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    total_failed += 1
                    results[table] = {"source": len(rows), "inserted": 0, "updated": 0, "skipped": skipped, "failed": 0, "error": f"写入失败: {exc}"}
                    continue
                db.commit()
                total_success += inserted + updated
                results[table] = {
                    "source": len(rows),
                    "inserted": inserted,
                    "updated": updated,
                    "skipped": skipped,
                    "failed": 0,
                    "error": None,
                }

            # 角色保底：为无授权角色的同步用户补挂 normal_user
            authorized = _collect_authorized_roles(db)
            fallback_added = 0
            if synced_user_ids:
                fallback_added = _ensure_fallback_role(db, synced_user_ids, authorized)
                db.commit()
            results["_fallback"] = {"users": len(synced_user_ids), "normal_user_added": fallback_added}

            ended = _now()
            log_row.sync_status = "SUCCESS"
            log_row.success_count = total_success
            log_row.failed_count = total_failed
            log_row.total_count = total_success + total_failed
            log_row.end_time = ended
            log_row.duration_ms = int((ended - started).total_seconds() * 1000)
            log_row.response_data = json.dumps(
                {"tables": results, "dsId": ds_id, "dsName": ds.name, "started_at": started.isoformat(), "finished_at": ended.isoformat()},
                ensure_ascii=False,
            )
            db.commit()
            return {"success": True, "syncId": log_row.id, "tables": results, "fallback": results.get("_fallback")}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        ended = _now()
        db.rollback()
        log_row.sync_status = "FAILED"
        log_row.failed_count = 1
        log_row.error_message = str(exc)
        log_row.end_time = ended
        log_row.duration_ms = int((ended - started).total_seconds() * 1000)
        db.commit()
        return {"success": False, "error": str(exc)}


def get_last_sync(db: Session) -> dict | None:
    """最近一次框架同步结果（含 response_data 解析）。"""
    row = (
        db.execute(
            select(SyncLog)
            .where(SyncLog.sync_type == SYNC_TYPE)
            .order_by(SyncLog.create_time.desc(), SyncLog.start_time.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if not row:
        return None
    data = {}
    if row.response_data:
        try:
            data = json.loads(row.response_data)
        except (TypeError, json.JSONDecodeError):
            data = {}
    return {
        "syncId": row.id,
        "syncStatus": row.sync_status,
        "errorMessage": row.error_message,
        "startTime": row.start_time.isoformat() if row.start_time else None,
        "endTime": row.end_time.isoformat() if row.end_time else None,
        "durationMs": row.duration_ms,
        "successCount": row.success_count,
        "failedCount": row.failed_count,
        "createUser": row.create_user,
        "responseData": data,
    }


def list_sync_history(db: Session, page: int = 1, page_size: int = 10) -> dict:
    """同步历史列表（分页，新→旧）。"""
    base = select(SyncLog).where(SyncLog.sync_type == SYNC_TYPE)
    total = int(db.execute(select(text("count(*)")).select_from(base.subquery())).scalar() or 0)
    rows = (
        db.execute(base.order_by(SyncLog.create_time.desc(), SyncLog.start_time.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    items = [
        {
            "syncId": r.id,
            "syncStatus": r.sync_status,
            "errorMessage": r.error_message,
            "startTime": r.start_time.isoformat() if r.start_time else None,
            "endTime": r.end_time.isoformat() if r.end_time else None,
            "durationMs": r.duration_ms,
            "successCount": r.success_count,
            "failedCount": r.failed_count,
            "createUser": r.create_user,
        }
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}
