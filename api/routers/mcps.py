"""Scoped MCP server API routes — /api/v1/mcps.

kb_compilation is the source of truth for MCP configurations (system /
personal / team scopes); the gateway is a stateless executor (2A — task
submission carries the config). Secret header values are masked on read.
Requires the x-next-identity cookie; admin gating for system scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.identity import decode_identity_cookie
from api.services.mcps import (
    create_mcp,
    delete_mcp,
    get_mcp,
    list_mcps,
    mcp_to_dict,
    mcp_visible,
    update_mcp,
)
from api.services.scope import is_admin
from api.services.system_config import test_mcp_server

router = APIRouter(prefix="/mcps", tags=["mcp-registry"])


@dataclass
class Caller:
    user_id: str
    team_name: str
    is_admin: bool


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> Caller:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return Caller(
        user_id=identity.user_id,
        team_name=identity.team_name or "",
        is_admin=is_admin(db, identity.user_id),
    )


class McpPayload(BaseModel):
    name: str = ""
    scope: str = "personal"
    type: str = "streamable_http"
    url: str | None = None
    headers: dict | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict | None = None
    enabled: bool = True


@router.get("")
def list_mcp_route(
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
    scope: str | None = None,
) -> dict:
    items = list_mcps(
        db,
        caller_user_id=caller.user_id,
        caller_team_name=caller.team_name,
        is_sys_admin=caller.is_admin,
        scope=scope,
    )
    return {"success": True, "data": {"items": items, "is_admin": caller.is_admin}}


@router.post("")
def create_mcp_route(
    req: McpPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = create_mcp(
            db,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
            payload=req.model_dump(),
        )
        return {"success": True, "data": {"item": item}}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{mcp_id}")
def detail_mcp_route(
    mcp_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    m = get_mcp(db, mcp_id)
    if not m:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    if not mcp_visible(
        m,
        caller_user_id=caller.user_id,
        caller_team_name=caller.team_name,
        is_sys_admin=caller.is_admin,
    ):
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    return {"success": True, "data": {"item": mcp_to_dict(m)}}


@router.put("/{mcp_id}")
def update_mcp_route(
    mcp_id: str,
    req: McpPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = update_mcp(
            db,
            mcp_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
            payload=req.model_dump(exclude_unset=True),
        )
        return {"success": True, "data": {"item": item}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/{mcp_id}")
def delete_mcp_route(
    mcp_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        delete_mcp(
            db,
            mcp_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"deleted": True}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/test")
def test_mcp_route(payload: dict) -> dict:
    result = test_mcp_server(payload)
    return {"success": True, "data": result}


__all__ = ["router"]