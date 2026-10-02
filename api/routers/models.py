"""Scoped model registry API routes — /api/v1/models.

Backs the frontend 模型配置 page. Business scoping:
- personal: owner only
- team:     the caller's team members
- system:   administrators only

Endpoints:
- GET    /models                     list visible models (?type=&scope=)
- POST   /models                     create
- GET    /models/providers           provider presets (+default URLs)
- GET    /models/{id}                detail
- PUT    /models/{id}                update
- DELETE /models/{id}                delete (soft)
- POST   /models/{id}/default        set as default for its scope+type
- POST   /models/test                probe an arbitrary endpoint
- POST   /models/{id}/debug          run a live chat/embedding probe
Requires the x-next-identity cookie; admin gating for system scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.identity import decode_identity_cookie
from api.services.models import (
    DEFAULT_PROVIDER_URLS,
    MODEL_SCOPES,
    MODEL_TYPES,
    PROVIDER_LABELS,
    create_model,
    copy_model,
    delete_model,
    get_model,
    is_admin,
    list_visible_models,
    set_default,
    update_model,
)
from api.services.system_config import test_model_endpoint

router = APIRouter(prefix="/models", tags=["model-registry"])


# ---------------------------------------------------------------------------
# Identity dependency (user_id + team_name + admin flag)
# ---------------------------------------------------------------------------


@dataclass
class Caller:
    user_id: str
    team_name: str
    team_id: str
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
        team_id=identity.team_id or "",
        is_admin=is_admin(db, identity.user_id),
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ModelPayload(BaseModel):
    scope: str = "personal"
    name: str = ""
    display_name: str | None = None
    type: str = "chat"
    source: str = "remote"
    provider: str | None = None
    description: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    interface_type: str | None = "openai"
    dimension: int | None = None
    supports_vision: bool = False
    custom_headers: dict | None = None
    owner_team_name: str | None = None
    is_default: bool = False
    max_concurrency: int | None = None
    thinking_control: str | None = None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/providers")
def providers() -> dict:
    """Provider presets: label + default URL per model type (mirrors
    WeKnora GET /models/providers)."""
    items = []
    seen: set[str] = set()
    for (mtype, provider) in sorted(DEFAULT_PROVIDER_URLS):
        if provider in seen:
            continue
        seen.add(provider)
        items.append(
            {
                "value": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "modelTypes": [t for (t, p) in DEFAULT_PROVIDER_URLS if p == provider],
                "defaultUrls": {
                    t: url for (t, p), url in DEFAULT_PROVIDER_URLS.items() if p == provider
                },
            }
        )
    return {"success": True, "data": {"items": items}}


@router.get("")
def list_models(
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
    type: str | None = None,
    scope: str | None = None,
) -> dict:
    if type and type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的模型类型: {type}")
    if scope and scope not in MODEL_SCOPES:
        raise HTTPException(status_code=400, detail=f"无效的模型级别: {scope}")
    items = list_visible_models(
        db,
        caller.user_id,
        caller.team_name,
        caller.is_admin,
        model_type=type,
        scope=scope,
    )
    return {"success": True, "data": {"items": items, "is_admin": caller.is_admin}}


@router.post("")
def create_model_route(
    req: ModelPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = create_model(
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


@router.get("/{model_id}")
def model_detail(
    model_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    m = get_model(db, model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    # visibility: personal owner-only / team member-only / system admin-only
    visible = m.owner_user_id == caller.user_id or (
        m.owner_team_name and m.owner_team_name == caller.team_name
    ) or (m.scope == "system" and caller.is_admin)
    if not visible:
        raise HTTPException(status_code=404, detail="模型不存在")
    from api.services.models import model_to_dict

    return {"success": True, "data": {"item": model_to_dict(m)}}


@router.put("/{model_id}")
def update_model_route(
    model_id: str,
    req: ModelPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = update_model(
            db,
            model_id,
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


@router.delete("/{model_id}")
def delete_model_route(
    model_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        delete_model(
            db,
            model_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"deleted": True}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/{model_id}/default")
def set_default_route(
    model_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = set_default(
            db,
            model_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"item": item}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/{model_id}/copy")
def copy_model_route(
    model_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """复制模型：名称自动加 -copy 后缀（重名则递增计数）。"""
    try:
        item = copy_model(
            db,
            model_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"item": item}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Test / debug
# ---------------------------------------------------------------------------


class TestEndpointPayload(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@router.post("/test")
def test_endpoint(payload: TestEndpointPayload) -> dict:
    base_url = payload.base_url.strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="缺少 base_url")
    if not payload.model.strip():
        raise HTTPException(status_code=400, detail="缺少 model")
    result = test_model_endpoint(base_url, payload.api_key.strip(), payload.model.strip())
    return {"success": result["ok"], "data": result}


class DebugPayload(BaseModel):
    input: str = ""
    model: str | None = None  # caller can override the model id mid-probe


@router.post("/{model_id}/debug")
def debug_model(
    model_id: str,
    req: DebugPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """Live probe on a saved model: chat streams a short completion,
    embedding returns the vector dimension. Mirrors WeKnora DebugModel's
    per-type behaviour without the full cross-provider options surface."""
    m = get_model(db, model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    visible = m.owner_user_id == caller.user_id or (
        m.owner_team_name and m.owner_team_name == caller.team_name
    ) or (m.scope == "system" and caller.is_admin)
    if not visible:
        raise HTTPException(status_code=404, detail="模型不存在")

    from api.services.models import decrypt_secret

    base_url = (m.base_url or "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="模型未配置端点地址")
    api_key = decrypt_secret(m.api_key)
    use_model = req.model or m.name

    if m.type == "embedding":
        from api.services.embedding import EmbeddingConfig, EmbeddingClient

        client = EmbeddingClient(
            EmbeddingConfig(
                base_url=base_url,
                api_key=api_key or "",
                model=use_model,
                dim=m.dimension or 1024,
            )
        )
        vec = client.embed_query(req.input.strip() or "ping")
        if vec is None:
            return {"success": False, "data": {"ok": False, "error": "向量化失败（端点不可达或模型不存在）"}}
        return {
            "success": True,
            "data": {"ok": True, "kind": "embedding", "dimension": len(vec)},
        }

    if m.type == "rerank":
        # rerank: probe via POST /rerank (Jina/Cohere/阿里 compatible)
        import json

        import httpx

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": use_model,
            "query": req.input.strip() or "ping",
            "documents": ["ping"],
        }
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"{base_url}/rerank", json=body, headers=headers)
            if resp.status_code >= 300:
                return {
                    "success": False,
                    "data": {"ok": False, "error": f"rerank 端点返回 {resp.status_code}"},
                }
            data = resp.json()
            results = data.get("results") or []
            return {
                "success": True,
                "data": {"ok": True, "kind": "rerank", "text": f"命中 {len(results)} 条重排结果"},
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "data": {"ok": False, "error": str(e)}}

    if m.type == "asr":
        # asr: probe via GET /models (openai-compatible); a 200 with model
        # list means the endpoint is reachable.
        import httpx

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{base_url}/models", headers=headers)
            if resp.status_code >= 300:
                return {
                    "success": False,
                    "data": {"ok": False, "error": f"ASR 端点返回 {resp.status_code}"},
                }
            data = resp.json()
            ids = [m.get("id") for m in (data.get("data") or [])]
            hit = use_model in ids if ids else True
            return {
                "success": True,
                "data": {"ok": True, "kind": "asr", "text": f"端点可达，模型{'在' if hit else '不在'}列表"},
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "data": {"ok": False, "error": str(e)}}

    # chat / vllm (openai-compatible chat probe)
    from api.services.chat import ChatClient, ChatConfig, ChatMessage

    client = ChatClient(
        ChatConfig(base_url=base_url, api_key=api_key or "", model=use_model)
    )
    messages = [ChatMessage(role="user", content=req.input.strip() or "ping")]
    try:
        text = client.chat(messages, max_tokens=256)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "data": {"ok": False, "error": str(e)}}
    return {
        "success": True,
        "data": {"ok": True, "kind": "chat", "text": (text or "")[:2000]},
    }


__all__ = ["router"]