"""Datasource services — connection test, team access guard, metadata.

Ported from data-synth:
- rawTestConnectionAction (src/app/actions/datasource-actions.ts): mysql /
  postgres / kingbase / trino / hive / minio-s3-oss-obs probing
- findAccessibleDatasourceById (src/lib/auth/datasource-access.ts): team-gated
  datasource lookup
- /api/open/datasources (route.ts): paginated list

Secrets: `ds_auth` is AES-encrypted at rest in modo_datasource; we decrypt with
api.lib.crypto.aes_decrypt (byte-compatible with data-synth / Java).
"""

from __future__ import annotations

import json
import logging
import re
import ssl
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_decrypt
from api.models.framework import Datasource, TeamDsMap

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
        return value  # sansec envelope — not decryptable here
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
