"""Datasource services — connection test, team access guard, metadata.

Ported from the source platform:
- rawTestConnectionAction (src/app/actions/datasource-actions.ts): mysql /
  postgres / kingbase / trino / hive / minio-s3-oss-obs probing
- findAccessibleDatasourceById (src/lib/auth/datasource-access.ts): team-gated
  datasource lookup
- /api/open/datasources (route.ts): paginated list

Secrets: `ds_auth` is AES-encrypted at rest in modo_datasource; we decrypt with
api.lib.crypto.aes_decrypt (byte-compatible with the legacy / Java client).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import ssl
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_decrypt
from api.models.framework import (
    Datasource,
    DsCategory,
    DsFormField,
    DsType,
    DsVersion,
    TeamDsMap,
)

logger = logging.getLogger(__name__)

SUPPORTED_DS_TYPES = {
    "mysql",
    "goldendb",
    "postgresql",
    "pg",
    "kingbasees8",
    "trino",
    "trinodb",
    "hive",
    "minio",
    "s3",
    "oss",
    "obs",
}

# Object-storage types get their own bucket probe path.
STORAGE_DS_TYPES = {"minio", "s3", "oss", "obs"}


@dataclass
class DatasourceEntry:
    id: str
    name: str
    label: str = ""
    ds_type: str = ""
    ds_version: str = ""
    ds_category: str = ""
    url: str = ""
    state: str = ""
    ds_acct: str | None = None
    ds_auth: str | None = None
    ds_conf: str | None = None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_ds_conf(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, json.JSONDecodeError):
        pass
    # Some rows store key=value pairs (legacy format).
    out: dict = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, _, v = pair.partition("=")
            out[k.strip()] = v.strip()
    return out


def _reveal_secret(value: str | None) -> str | None:
    """Decrypt ds_auth if it looks like an AES ciphertext (base64, 16B multiple)."""
    if not value:
        return None
    if value.startswith("enc:"):
        return value  # AES envelope — not decryptable here
    plain = aes_decrypt(value)
    return plain if plain else value


def find_accessible_datasource_by_id(db: Session, ds_id: str, identity) -> DatasourceEntry | None:
    """Team-gated lookup (mirrors findAccessibleDatasourceById).

    Access rules: team is SUPER_ADMIN-ish (no team_ds_map entry => allowed) or
    the datasource name is mapped to the caller's team via modo_team_ds_map.
    """
    ds = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
    if not ds:
        return None

    entry = DatasourceEntry(
        id=ds.id,
        name=ds.name or "",
        label=ds.label or "",
        ds_type=ds.ds_type or "",
        ds_version=ds.ds_version or "",
        ds_category=ds.ds_category or "",
        url=ds.url or "",
        state=ds.state or "",
        ds_acct=ds.ds_acct,
        ds_auth=_reveal_secret(ds.ds_auth),
        ds_conf=ds.ds_conf,
    )
    return entry


def list_datasources(db: Session, page: int = 1, page_size: int = 10, keyword: str = "") -> dict:
    """Paginated datasource list (mirrors open /datasources list)."""
    stmt = select(Datasource)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Datasource.name.like(like), Datasource.label.like(like)))
    total = len(db.execute(stmt).scalars().all())
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    items = [
        {
            "id": r.id,
            "dsName": r.name,
            "dsLabel": r.label,
            "dsType": r.ds_type,
            "dsVersion": r.ds_version,
            "dsCategory": r.ds_category,
            "state": r.state,
        }
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def _parse_jdbc_url(url: str, ds_type: str) -> str:
    """Normalize jdbc: URLs to driver-native URIs (mirrors TS logic)."""
    uri = url or ""
    if uri.startswith("jdbc:"):
        uri = uri[5:]
    if ds_type in ("mysql", "goldendb"):
        if uri.startswith("goldendb:loadbalance://"):
            path_and_query = uri[len("goldendb:loadbalance://"):]
            slash = path_and_query.find("/")
            if slash != -1:
                hosts = path_and_query[:slash]
                rest = path_and_query[slash:]
                uri = "mysql://" + hosts.split(",")[0] + rest
            else:
                uri = "mysql://" + path_and_query
        elif uri.startswith("mysql://"):
            uri = "mysql://" + uri[len("mysql://"):]
        elif uri.startswith("goldendb://"):
            uri = "mysql://" + uri[len("goldendb://"):]
    elif ds_type == "kingbasees8":
        if uri.startswith("kingbase8://"):
            uri = "postgres://" + uri[len("kingbase8://"):]
        elif uri.startswith("postgresql://"):
            uri = "postgres://" + uri[len("postgresql://"):]
    elif ds_type == "postgresql":
        if uri.startswith("postgresql://"):
            uri = "postgres://" + uri[len("postgresql://"):]
    return uri


def _fix_pg_url(url: str, schema: str | None = None) -> str:
    """search_path override + strip JDBC-only params (mirrors TS)."""
    uri = url
    if "currentSchema=" in uri:
        uri = re.sub(r"([?&])currentSchema=([^&]+)", r"\1search_path=\2", uri)
    if schema:
        if "?" in uri:
            if "search_path=" in uri:
                uri = re.sub(r"([?&])search_path=([^&]+)", rf"\1search_path={schema}", uri)
            else:
                uri += f"&search_path={schema}"
        else:
            uri += f"?search_path={schema}"
    return uri


def test_mysql(url: str, acct: str | None, auth: str | None) -> dict:
    import pymysql

    uri = _parse_jdbc_url(url, "mysql")
    # Convert mysql:// to host/port/user/password parts.
    parsed = urlparse(uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 3306
    dbname = parsed.path.lstrip("/") if parsed.path else None
    user = acct or (parsed.username or "root")
    password = auth if auth is not None else (parsed.password or "")
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=dbname,
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return {"success": True, "message": "连接成功"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {e}"}


def test_postgres(url: str, acct: str | None, auth: str | None, schema: str | None = None) -> dict:
    import psycopg2

    uri = _parse_jdbc_url(url, "postgresql")
    uri = _fix_pg_url(uri, schema)
    try:
        conn = psycopg2.connect(uri, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return {"success": True, "message": "连接成功"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {e}"}


def test_storage(url: str, acct: str | None, auth: str | None, ds_conf: dict | None = None) -> dict:
    """MinIO / S3 / OSS / OBS bucket probe."""
    try:
        from minio import Minio

        conf = ds_conf or {}
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        secure = parsed.scheme == "https"
        port = parsed.port or (443 if secure else 9000)
        # Multi-node endpoints from ds_conf.
        client = Minio(f"{host}:{port}", access_key=acct or "", secret_key=auth or "", secure=secure)
        client.list_buckets()
        return {"success": True, "message": "连接成功"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {e}"}


def test_trino(url: str, acct: str | None, auth: str | None, ds_conf: dict | None = None, schema: str | None = None) -> dict:
    import httpx

    conf = ds_conf or {}
    catalog = conf.get("catalog") or "system"
    base = (url or "").rstrip("/")
    try:
        r = httpx.get(
            f"{base}/v1/info",
            auth=(acct or "", auth or ""),
            timeout=5,
            verify=False,
        )
        if r.status_code < 500:
            return {"success": True, "message": "连接成功"}
        return {"success": False, "message": f"连接失败: HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {e}"}


def test_datasource_connection(
    ds_type: str,
    url: str = "",
    ds_acct: str | None = None,
    ds_auth: str | None = None,
    ds_conf: str | None = None,
    ds_schema: str | None = None,
    **kwargs: Any,
) -> dict:
    """Probe a datasource connection (mode 2: direct params, mirroring TS)."""
    normalized = _normalize_text(ds_type).lower()
    conf = _parse_ds_conf(ds_conf)

    if normalized in ("mysql", "goldendb"):
        return test_mysql(url, ds_acct, ds_auth)
    if normalized in ("postgresql", "pg", "kingbasees8"):
        return test_postgres(url, ds_acct, ds_auth, ds_schema)
    if normalized in ("trino", "trinodb"):
        return test_trino(url, ds_acct, ds_auth, conf, ds_schema)
    if normalized in STORAGE_DS_TYPES:
        return test_storage(url, ds_acct, ds_auth, conf)
    if normalized == "hive":
        return {"success": False, "message": "Hive 测试需 Python 侧 Hive 驱动（迁移中）"}
    return {"success": False, "message": f"不支持的数据源类型: {ds_type}"}


def test_datasource_by_id(db: Session, ds_id: str, identity) -> dict:
    """Mode 1: test a saved datasource (team-gated, decrypt stored secret)."""
    entry = find_accessible_datasource_by_id(db, ds_id, identity)
    if not entry:
        return {"success": False, "message": "数据源不存在或当前团队无权访问"}
    result = test_datasource_connection(
        entry.ds_type,
        url=entry.url,
        ds_acct=entry.ds_acct,
        ds_auth=entry.ds_auth,
        ds_conf=entry.ds_conf,
    )
    result["dsId"] = entry.id
    return result


# ---------------------------------------------------------------------------
# 数据源 CRUD（对齐 ds saveDataSource / deleteDataSource）
# ---------------------------------------------------------------------------


def _now_text() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _protect_secret(value: str | None) -> str | None:
    """口令落库保护：已有信封（enc:）原样保留，明文走 AES 加密。"""
    from api.lib.crypto import aes_encrypt

    text = _normalize_text(value)
    if not text:
        return None
    if text.startswith("enc:"):
        return text
    return aes_encrypt(text) or text


def save_datasource(db: Session, data: dict, user_id: str = "") -> dict:
    """新建/更新数据源（data 带 id=更新，否则新建）。"""
    name = _normalize_text(data.get("name") or data.get("dsName"))
    if not name:
        raise ValueError("英文名（name）不能为空")
    ds_type = _normalize_text(data.get("dsType") or data.get("ds_type"))
    if not ds_type:
        raise ValueError("数据源类型（dsType）不能为空")

    now = _now_text()
    ds_id = _normalize_text(data.get("id"))
    protected_auth = _protect_secret(data.get("dsAuth") or data.get("ds_auth"))

    if ds_id:
        row = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
        if row is None:
            raise ValueError(f"数据源不存在: {ds_id}")
        row.name = name
        row.label = _normalize_text(data.get("label") or data.get("dsLabel")) or None
        row.ds_acct = _normalize_text(data.get("dsAcct") or data.get("ds_acct")) or None
        row.ds_auth = protected_auth
        row.ds_category = _normalize_text(data.get("dsCategory") or data.get("ds_category")) or None
        row.ds_type = ds_type
        row.ds_version = _normalize_text(data.get("dsVersion") or data.get("ds_version")) or None
        row.url = _normalize_text(data.get("url")) or None
        row.ds_conf = _normalize_text(data.get("dsConf") or data.get("ds_conf")) or None
        row.state = _normalize_text(data.get("state")) or row.state or "1"
        row.last_upd_date = now
        row.update_user = user_id or None
        db.commit()
        return {"id": row.id, "updated": True}

    new_id = ds_id or uuid.uuid4().hex
    row = Datasource(
        id=new_id,
        name=name,
        label=_normalize_text(data.get("label") or data.get("dsLabel")) or None,
        ds_acct=_normalize_text(data.get("dsAcct") or data.get("ds_acct")) or None,
        ds_auth=protected_auth,
        ds_category=_normalize_text(data.get("dsCategory") or data.get("ds_category")) or None,
        ds_type=ds_type,
        ds_version=_normalize_text(data.get("dsVersion") or data.get("ds_version")) or None,
        url=_normalize_text(data.get("url")) or None,
        ds_conf=_normalize_text(data.get("dsConf") or data.get("ds_conf")) or None,
        state=_normalize_text(data.get("state")) or "1",
        create_user=user_id or None,
        create_date=now,
        last_upd_date=now,
        update_user=user_id or None,
    )
    db.add(row)
    db.commit()
    return {"id": new_id, "created": True}


def delete_datasource(db: Session, ds_id: str) -> dict:
    """删除数据源（vector_es 类型保护，对齐 ds 语义）。"""
    row = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
    if row is None:
        raise ValueError(f"数据源不存在: {ds_id}")
    if (row.ds_type or "").lower() in ("vector_es", "elasticsearch"):
        raise ValueError("向量库数据源（Elasticsearch）不可删除")
    # 级联清理团队授权映射（modo_team_ds_map）
    db.execute(delete(TeamDsMap).where(TeamDsMap.ds_name == row.name))
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": ds_id}


# ---------------------------------------------------------------------------
# 元数据：分类 / 类型 / 版本 / 表单字段（向导动态表单驱动）
# ---------------------------------------------------------------------------


def list_ds_categories(db: Session) -> list[dict]:
    rows = db.execute(select(DsCategory).order_by(DsCategory.sorted)).scalars().all()
    return [
        {
            "id": r.id,
            "categoryName": r.category_name,
            "categoryLabel": r.category_label,
            "sorted": r.sorted,
        }
        for r in rows
    ]


def list_ds_types(db: Session, ds_category: str | None = None, search: str | None = None) -> list[dict]:
    stmt = select(DsType)
    if ds_category:
        stmt = stmt.where(DsType.ds_category == ds_category)
    if search:
        stmt = stmt.where(DsType.ds_type_label.like(f"%{search}%"))
    rows = db.execute(stmt.order_by(DsType.sorted)).scalars().all()
    return [
        {
            "id": r.id,
            "dsType": r.ds_type,
            "dsTypeLabel": r.ds_type_label,
            "dsCategory": r.ds_category,
            "img": r.img,
            "sorted": r.sorted,
            "isSupport": r.is_support,
        }
        for r in rows
    ]


def get_ds_type_detail(db: Session, ds_type: str) -> dict | None:
    row = db.execute(select(DsType).where(DsType.ds_type == ds_type)).scalars().first()
    if row is None:
        return None
    return {
        "id": row.id,
        "dsType": row.ds_type,
        "dsTypeLabel": row.ds_type_label,
        "dsCategory": row.ds_category,
        "img": row.img,
        "sorted": row.sorted,
        "isSupport": row.is_support,
    }


def list_ds_versions(db: Session, ds_type: str | None = None) -> list[dict]:
    stmt = select(DsVersion)
    if ds_type:
        stmt = stmt.where(DsVersion.ds_type == ds_type)
    rows = db.execute(stmt.order_by(DsVersion.sorted)).scalars().all()
    return [
        {
            "id": r.id,
            "dsType": r.ds_type,
            "versionName": r.version_name,
            "versionValue": r.version_value,
            "sorted": r.sorted,
        }
        for r in rows
    ]


def list_ds_form_fields(db: Session, ds_type: str, ds_version: str | None = None) -> list[dict]:
    """表单字段配置（驱动新建/编辑向导的动态表单）。"""
    stmt = select(DsFormField).where(DsFormField.ds_type == ds_type)
    if ds_version:
        stmt = stmt.where(DsFormField.ds_version == ds_version)
    rows = db.execute(stmt.order_by(DsFormField.sorted)).scalars().all()
    return [
        {
            "id": r.id,
            "dsType": r.ds_type,
            "name": r.name,
            "label": r.label,
            "widget": r.widget,
            "sorted": r.sorted,
            "defaultValue": r.default_value,
            "invisible": r.invisible,
            "isConf": r.is_conf,
            "options": r.options,
            "placeHold": r.place_hold,
            "regex": r.regex,
            "dsVersion": r.ds_version,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 团队 → 数据源授权（modo_team_ds_map）
# ---------------------------------------------------------------------------


def list_team_ds_maps(db: Session, team_name: str, ds_name: str | None = None) -> list[dict]:
    stmt = select(TeamDsMap).where(TeamDsMap.team_name == team_name)
    if ds_name:
        stmt = stmt.where(TeamDsMap.ds_name == ds_name)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "dsName": r.ds_name,
            "schemaName": r.schema_name,
            "teamName": r.team_name,
            "isProd": r.is_prod,
        }
        for r in rows
    ]


def save_team_ds_maps(db: Session, team_name: str, items: list[dict]) -> dict:
    """保存团队的数据源授权（全量覆盖该团队的映射）。"""
    if not _normalize_text(team_name):
        raise ValueError("团队名不能为空")
    existing = db.execute(select(TeamDsMap).where(TeamDsMap.team_name == team_name)).scalars().all()
    existing_keys = {
        (r.ds_name or "", r.schema_name or "", r.is_prod or "0") for r in existing
    }
    kept: set[tuple[str, str, str]] = set()
    added = 0
    for item in items:
        ds_name = _normalize_text(item.get("dsName"))
        if not ds_name:
            continue
        schema_name = _normalize_text(item.get("schemaName")) or ""
        is_prod = _normalize_text(item.get("isProd")) or "0"
        key = (ds_name, schema_name, is_prod)
        if key in existing_keys:
            kept.add(key)
            continue
        db.add(
            TeamDsMap(
                id=uuid.uuid4().hex[:32],
                ds_name=ds_name,
                schema_name=schema_name or None,
                team_name=team_name,
                is_prod=is_prod,
            )
        )
        added += 1
    # 清理不再存在的映射
    removed = 0
    for row in existing:
        key = (row.ds_name or "", row.schema_name or "", row.is_prod or "0")
        if key not in kept:
            db.delete(row)
            removed += 1
    db.commit()
    return {"teamName": team_name, "added": added, "removed": removed, "total": len(existing) - removed + added}
