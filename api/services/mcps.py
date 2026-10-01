"""Scoped MCP server registry service.

kb_compilation is the source of truth for MCP server *configurations*
(system / personal / team scopes); the agent-gateway is a stateless
executor — task submission carries the config the task needs (2A), the
gateway never persists MCP config itself.

Secret header values (api key / token shaped) are AES-encrypted at rest
(api.lib.crypto.aes_encrypt) and masked on read; saving a value starting
with '****' means "keep the existing stored value".
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_decrypt, aes_encrypt
from api.models.mcp_skill import KbMcpServer
from api.services.runtime_config import mask_sensitive_value
from api.services.scope import ResourceRow, can_manage, can_see, validate_scope_request, visible_clauses

SECRET_HEADER_KEYS = ("api", "key", "token", "secret", "auth", "credential")


def _is_secret_header(name: str) -> bool:
    lower = (name or "").lower()
    return any(k in lower for k in SECRET_HEADER_KEYS)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return aes_encrypt(value)


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    plain = aes_decrypt(value)
    return plain if plain else value


def _serialize_headers(headers: dict | None, *, decrypt: bool) -> dict:
    out: dict = {}
    for k, v in (headers or {}).items():
        v = str(v or "")
        out[k] = decrypt_secret(v) if (decrypt and _is_secret_header(k)) else v
    return out


def mcp_to_dict(m: KbMcpServer, *, with_secrets: bool = False) -> dict:
    """API shape. Secret header values are masked; decrypted values only
    flow to the gateway at task-submission time (never via list/detail)."""
    headers = dict(m.headers or {})
    masked = {
        k: (mask_sensitive_value(decrypt_secret(v)) if _is_secret_header(k) else v)
        for k, v in headers.items()
    }
    return {
        "id": m.id,
        "scope": m.scope,
        "name": m.name,
        "type": m.type,
        "url": m.url or "",
        "headers": masked,
        "command": m.command or "",
        "args": m.args or [],
        "env": m.env or {},
        "owner_user_id": m.owner_user_id or "",
        "owner_team_name": m.owner_team_name or "",
        "enabled": bool(m.enabled),
        "state": m.state,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def list_mcps(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    scope: str | None = None,
) -> list[dict]:
    stmt = select(KbMcpServer).where(KbMcpServer.state == "1", *visible_clauses(caller_user_id, caller_team_name, is_sys_admin, KbMcpServer))
    if scope:
        stmt = stmt.where(KbMcpServer.scope == scope)
    rows = db.execute(stmt.order_by(KbMcpServer.scope, KbMcpServer.name)).scalars().all()
    return [mcp_to_dict(m) for m in rows]


def get_mcp(db: Session, mcp_id: str) -> KbMcpServer | None:
    return (
        db.execute(select(KbMcpServer).where(and_(KbMcpServer.id == mcp_id, KbMcpServer.state == "1")))
        .scalars()
        .first()
    )


def create_mcp(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    payload: dict,
) -> dict:
    scope, owner_user_id, owner_team_name = validate_scope_request(
        str(payload.get("scope") or "personal"),
        caller_user_id=caller_user_id,
        caller_team_name=caller_team_name,
        is_sys_admin=is_sys_admin,
    )
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("服务名称不能为空")
    dup = db.execute(
        select(KbMcpServer).where(and_(KbMcpServer.name == name, KbMcpServer.state == "1"))
    ).scalars().first()
    if dup:
        raise ValueError(f"服务名已存在: {name}")

    headers = payload.get("headers") or {}
    m = KbMcpServer(
        id=uuid.uuid4().hex[:36],
        scope=scope,
        name=name,
        type=str(payload.get("type") or "streamable_http").strip(),
        url=str(payload.get("url") or "").strip() or None,
        headers=_serialize_headers(headers, decrypt=False),
        command=str(payload.get("command") or "").strip() or None,
        args=payload.get("args") or None,
        env=payload.get("env") or None,
        owner_user_id=owner_user_id or None,
        owner_team_name=owner_team_name or None,
        enabled=bool(payload.get("enabled", True)),
        state="1",
    )
    db.add(m)
    db.commit()
    return mcp_to_dict(m)


def update_mcp(
    db: Session,
    mcp_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    payload: dict,
) -> dict:
    m = get_mcp(db, mcp_id)
    if not m:
        raise KeyError("MCP 服务不存在")
    if not can_manage(ResourceRow.from_obj(m), caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权修改该服务")

    if "name" in payload:
        v = str(payload.get("name") or "").strip()
        if not v:
            raise ValueError("服务名称不能为空")
        m.name = v
    if "type" in payload:
        m.type = str(payload.get("type") or "streamable_http").strip()
    if "url" in payload:
        m.url = str(payload.get("url") or "").strip() or None
    if "command" in payload:
        m.command = str(payload.get("command") or "").strip() or None
    if "args" in payload:
        m.args = payload.get("args") or None
    if "env" in payload:
        m.env = payload.get("env") or None
    if "enabled" in payload:
        m.enabled = bool(payload.get("enabled", True))
    if "headers" in payload:
        incoming = payload.get("headers") or {}
        current = _serialize_headers(m.headers, decrypt=True)
        merged = dict(current)
        for k, v in incoming.items():
            v = str(v or "")
            if v.startswith("****") and _is_secret_header(k):
                continue  # keep existing
            merged[k] = v
        m.headers = _serialize_headers(merged, decrypt=False)
    db.commit()
    return mcp_to_dict(m)


def delete_mcp(
    db: Session,
    mcp_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> None:
    m = get_mcp(db, mcp_id)
    if not m:
        raise KeyError("MCP 服务不存在")
    if not can_manage(ResourceRow.from_obj(m), caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权删除该服务")
    m.state = "0"
    db.commit()


def resolve_task_mcp_servers(
    db: Session,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> list[dict]:
    """Runtime payload for gateway task submission (2A): every ENABLED
    visible MCP server with DECRYPTED secret headers (the executor needs
    real credentials; this result never reaches list/detail responses)."""
    stmt = select(KbMcpServer).where(
        KbMcpServer.state == "1",
        KbMcpServer.enabled.is_(True),
        *visible_clauses(caller_user_id, caller_team_name, is_sys_admin, KbMcpServer),
    )
    rows = db.execute(stmt.order_by(KbMcpServer.scope, KbMcpServer.name)).scalars().all()
    out = []
    for m in rows:
        item: dict = {"name": m.name, "type": m.type or "streamable_http"}
        if m.url:
            item["url"] = m.url
        if m.command:
            item["command"] = m.command
        if m.args:
            item["args"] = m.args
        headers = _serialize_headers(m.headers, decrypt=True)
        if headers:
            item["headers"] = headers
        if m.env:
            item["env"] = m.env
        out.append(item)
    return out


def mcp_visible(
    m: KbMcpServer,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> bool:
    return can_see(ResourceRow.from_obj(m), caller_user_id, caller_team_name, is_sys_admin)


__all__ = [
    "list_mcps",
    "get_mcp",
    "create_mcp",
    "update_mcp",
    "delete_mcp",
    "resolve_task_mcp_servers",
    "mcp_to_dict",
    "mcp_visible",
]
