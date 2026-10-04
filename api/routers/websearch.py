"""Scoped WebSearch provider API routes — /api/v1/websearch.

kb_compilation 是联网搜索提供方配置的唯一数据源（personal / team / system
三级归属）；执行器 search 按 provider_type 调对应搜索 API。
API Key 读取时脱敏；保存以 '****' 开头的值表示保持原值。
Requires the x-next-identity cookie; admin gating for system scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.identity import decode_identity_cookie
from api.services.scope import is_admin
from api.services.websearch import (
    create_websearch_provider,
    delete_websearch_provider,
    get_websearch_provider,
    list_websearch_providers,
    search,
    update_websearch_provider,
    websearch_provider_visible,
    websearch_to_dict,
)

router = APIRouter(prefix="/websearch", tags=["websearch-registry"])


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


class WebSearchProviderPayload(BaseModel):
    name: str = ""
    scope: str = "personal"
    provider_type: str = "generic"
    api_key: str = ""
    base_url: str | None = None
    description: str | None = None
    extra_config: dict | None = None
    enabled: bool = True


class SearchPayload(BaseModel):
    query: str = ""
    max_results: int = 5


@router.get("")
def list_websearch_route(
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
    scope: str | None = None,
) -> dict:
    items = list_websearch_providers(
        db,
        caller_user_id=caller.user_id,
        caller_team_name=caller.team_name,
        is_sys_admin=caller.is_admin,
        scope=scope,
    )
    return {"success": True, "data": {"items": items, "is_admin": caller.is_admin}}


@router.post("")
def create_websearch_route(
    req: WebSearchProviderPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = create_websearch_provider(
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


@router.get("/{provider_id}")
def detail_websearch_route(
    provider_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    p = get_websearch_provider(db, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="搜索提供方不存在")
    if not websearch_provider_visible(
        p,
        caller_user_id=caller.user_id,
        caller_team_name=caller.team_name,
        is_sys_admin=caller.is_admin,
    ):
        raise HTTPException(status_code=404, detail="搜索提供方不存在")
    return {"success": True, "data": {"item": websearch_to_dict(p)}}


@router.put("/{provider_id}")
def update_websearch_route(
    provider_id: str,
    req: WebSearchProviderPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = update_websearch_provider(
            db,
            provider_id,
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


@router.delete("/{provider_id}")
def delete_websearch_route(
    provider_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        delete_websearch_provider(
            db,
            provider_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"deleted": True}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/{provider_id}/search")
def search_websearch_route(
    provider_id: str,
    req: SearchPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        results = search(
            db,
            provider_id,
            req.query,
            req.max_results,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"results": results}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


__all__ = ["router"]
