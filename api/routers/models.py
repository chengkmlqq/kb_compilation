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

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.identity import decode_identity_cookie
from api.models.model import KbModel
from api.services.models import (
    DEFAULT_PROVIDER_URLS,
    MODEL_SCOPES,
    MODEL_TYPES,
    PROVIDER_LABELS,
    create_model,
    copy_model,
    delete_model,
    decrypt_secret,
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


@router.get("/export")
def export_models(
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """导出当前用户可见的全部模型配置（含 api_key 明文，供导入迁移）。"""
    from sqlalchemy import and_, or_, select

    clauses = [KbModel.state == "1"]
    if caller.user_id:
        clauses.append(
            or_(
                KbModel.owner_user_id == caller.user_id,
                KbModel.owner_team_name == caller.team_name,
                and_(KbModel.scope == "system", caller.is_admin),
            )
        )
    rows = db.execute(select(KbModel).where(and_(*clauses))).scalars().all()
    items = []
    for m in rows:
        items.append(
            {
                "scope": m.scope,
                "name": m.name,
                "display_name": m.display_name or "",
                "type": m.type,
                "source": m.source or "remote",
                "provider": m.provider or "",
                "description": m.description or "",
                "base_url": m.base_url or "",
                "api_key": decrypt_secret(m.api_key) if m.api_key else "",
                "interface_type": m.interface_type or "openai",
                "dimension": m.dimension,
                "supports_vision": bool(m.supports_vision),
                "custom_headers": m.custom_headers or {},
                "is_default": bool(m.is_default),
                "max_concurrency": m.max_concurrency,
                "thinking_control": m.thinking_control or "",
            }
        )
    return {"success": True, "data": {"models": items, "count": len(items)}}


class ModelImportPayload(BaseModel):
    models: list[ModelPayload] = []
    mode: str = "upsert"  # upsert（按 name+type 更新/创建）| create（仅新建，重名报错）


@router.post("/import")
def import_models(
    req: ModelImportPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """批量导入模型配置（幂等 upsert：同 name+type 更新，否则创建）。"""
    from sqlalchemy import select

    created = 0
    updated = 0
    errors: list[dict] = []
    for i, p in enumerate(req.models):
        try:
            if not (p.name or "").strip() or not (p.type or "").strip():
                raise ValueError("name/type 不能为空")
            existing = (
                db.execute(
                    select(KbModel).where(
                        KbModel.name == p.name.strip(),
                        KbModel.type == p.type.strip(),
                        KbModel.state == "1",
                    )
                )
                .scalars()
                .first()
            )
            if existing:
                if req.mode == "create":
                    raise ValueError(f"已存在: {p.name}({p.type})")
                update_model(
                    db,
                    existing.id,
                    caller_user_id=caller.user_id,
                    caller_team_name=caller.team_name,
                    is_sys_admin=caller.is_admin,
                    payload=p.model_dump(),
                )
                updated += 1
            else:
                create_model(
                    db,
                    caller_user_id=caller.user_id,
                    caller_team_name=caller.team_name,
                    is_sys_admin=caller.is_admin,
                    payload=p.model_dump(),
                )
                created += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不阻塞批量
            errors.append({"index": i, "name": p.name, "error": str(exc)[:120]})
    db.commit()
    if errors:
        logging.getLogger("kb.models.import").warning(
            "model import errors: created=%s updated=%s errors=%s",
            created,
            updated,
            errors,
        )
    return {
        "success": True,
        "data": {"created": created, "updated": updated, "errors": errors},
    }





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


DEBUG_MAX_INPUT_BYTES = 64 * 1024
DEBUG_MAX_FILE_BYTES = 20 * 1024 * 1024
DEBUG_MAX_DOCUMENTS = 100


def _parse_debug_options(raw: str) -> dict:
    """Parse the JSON options string; validate ranges (WeKnora-compatible)."""
    if not raw.strip():
        return {}
    try:
        opts = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"options 不是合法 JSON: {e}") from e
    if not isinstance(opts, dict):
        raise HTTPException(status_code=400, detail="options 必须是 JSON 对象")
    if opts.get("max_tokens") is not None:
        if not 1 <= int(opts["max_tokens"]) <= 8192:
            raise HTTPException(status_code=400, detail="max_tokens 必须在 1-8192 之间")
    if opts.get("temperature") is not None:
        if not 0 <= float(opts["temperature"]) <= 2:
            raise HTTPException(status_code=400, detail="temperature 必须在 0-2 之间")
    if opts.get("top_p") is not None:
        if not 0 < float(opts["top_p"]) <= 1:
            raise HTTPException(status_code=400, detail="top_p 必须在 (0,1] 之间")
    return opts


@router.post("/{model_id}/debug")
def debug_model(
    model_id: str,
    input: str = Form(""),
    options: str = Form(""),
    documents: str = Form(""),
    file: UploadFile | None = File(None),
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """Live probe on a saved model, multipart form (WeKnora DebugModel parity).

    chat/vllm stream a completion (with thinking + generation options),
    embedding returns the vector dimension, rerank really reranks query vs
    documents, asr transcribes an uploaded audio file. Response carries
    ok/elapsed_ms/request preview (secrets redacted)/raw_response/observations.
    """
    started = time.monotonic()
    m = get_model(db, model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    visible = m.owner_user_id == caller.user_id or (
        m.owner_team_name and m.owner_team_name == caller.team_name
    ) or (m.scope == "system" and caller.is_admin)
    if not visible:
        raise HTTPException(status_code=404, detail="模型不存在")

    if len(input.encode("utf-8")) > DEBUG_MAX_INPUT_BYTES:
        raise HTTPException(status_code=400, detail="input 过长")

    opts = _parse_debug_options(options)
    docs: list[str] = []
    if documents.strip():
        try:
            docs = json.loads(documents)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail="documents 必须是 JSON 字符串数组") from e
        if not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
            raise HTTPException(status_code=400, detail="documents 必须是 JSON 字符串数组")
        if len(docs) > DEBUG_MAX_DOCUMENTS:
            raise HTTPException(status_code=400, detail="documents 不能超过 100 条")

    file_bytes = b""
    file_name = ""
    if file is not None:
        file_bytes = file.file.read(DEBUG_MAX_FILE_BYTES + 1)
        if len(file_bytes) > DEBUG_MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail="文件不能超过 20 MB")
        file_name = file.filename or ""

    from api.services.models import decrypt_secret

    base_url = (m.base_url or "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="模型未配置端点地址")
    api_key = decrypt_secret(m.api_key)
    use_model = m.name

    request_preview: dict[str, Any] = {
        "model_id": m.id,
        "model_name": m.name,
        "model_type": m.type,
        "provider": m.provider or "",
        "input": input,
        "options": opts,
    }
    if docs:
        request_preview["documents"] = docs
    if file_name:
        request_preview["file"] = {"name": file_name, "size": len(file_bytes)}
    if m.custom_headers:
        # 只列键名，不泄露值（对齐 WeKnora custom_header_names）
        request_preview["custom_header_names"] = sorted(m.custom_headers.keys())
    observations: dict[str, Any] = {}

    def finish(ok: bool, raw: Any, err: str | None = None) -> dict:
        data: dict[str, Any] = {
            "ok": ok,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "request": request_preview,
            "raw_response": raw,
            "observations": observations,
        }
        if err:
            data["error"] = err
        return {"success": True, "data": data}

    if m.type == "embedding":
        if not input.strip():
            raise HTTPException(status_code=400, detail="请输入要向量化的文本")
        from api.services.embedding import EmbeddingConfig, EmbeddingClient

        client = EmbeddingClient(
            EmbeddingConfig(
                base_url=base_url,
                api_key=api_key or "",
                model=use_model,
                dim=m.dimension or 1024,
                custom_headers=m.custom_headers or None,
            )
        )
        try:
            vec = client.embed_query(input.strip())
        except Exception as e:  # noqa: BLE001
            return finish(False, None, str(e))
        if vec is None:
            return finish(False, None, "向量化失败（端点不可达或模型不存在）")
        observations["dimension"] = len(vec)
        return finish(True, vec)

    if m.type == "rerank":
        # rerank: real POST /rerank (Jina/Cohere/阿里 compatible) with the
        # caller's query + documents; returns the ranked result list.
        if not input.strip() or not docs:
            raise HTTPException(status_code=400, detail="查询文本与候选文档不能为空")
        import httpx

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if m.custom_headers:
            headers.update(m.custom_headers)
        body = {"model": use_model, "query": input.strip(), "documents": docs}
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"{base_url}/rerank", json=body, headers=headers)
            if resp.status_code >= 300:
                return finish(False, None, f"rerank 端点返回 {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            results = data.get("results") or []
            observations["result_count"] = len(results)
            return finish(True, results)
        except Exception as e:  # noqa: BLE001
            return finish(False, None, str(e))

    if m.type == "asr":
        # asr: real transcription of the uploaded audio file.
        if not file_bytes:
            raise HTTPException(status_code=400, detail="请上传音频文件")
        from api.services.asr import ASRClient, ASRConfig

        client = ASRClient(
            ASRConfig(
                base_url=base_url,
                api_key=api_key or "",
                model=use_model,
                custom_headers=m.custom_headers or None,
            )
        )
        try:
            result = client.transcribe(file_bytes, file_name or "audio.bin")
        except Exception as e:  # noqa: BLE001
            return finish(False, None, str(e))
        observations["text_characters"] = len(result.text)
        observations["segment_count"] = len(result.segments)
        return finish(True, {"text": result.text, "segments": result.segments})

    # chat / vllm (openai-compatible chat probe; vllm requires an image file)
    from api.services.chat import ChatClient, ChatConfig, ChatMessage

    messages: list[ChatMessage] = []
    system_prompt = (opts.get("system_prompt") or "").strip()
    if system_prompt:
        messages.append(ChatMessage(role="system", content=system_prompt))
    if m.type == "vllm":
        if not file_bytes:
            raise HTTPException(status_code=400, detail="请上传图片文件")
        import base64

        data_url = "data:image/jpeg;base64," + base64.b64encode(file_bytes).decode("ascii")
        messages.append(ChatMessage(role="user", content=input.strip() or "描述这张图片", images=[data_url]))
    else:
        if not input.strip():
            raise HTTPException(status_code=400, detail="请输入问题")
        messages.append(ChatMessage(role="user", content=input.strip()))

    client = ChatClient(
        ChatConfig(
            base_url=base_url,
            api_key=api_key or "",
            model=use_model,
            custom_headers=m.custom_headers or None,
        )
    )
    thinking_val = opts.get("thinking")
    thinking: bool | None = None
    if isinstance(thinking_val, bool):
        thinking = thinking_val
    observations["stream"] = True
    if thinking is not None:
        observations["requested_thinking"] = thinking
    observations["thinking_control"] = m.thinking_control or "none"
    observations["thinking_parameter_sent"] = thinking is not None and (m.thinking_control or "") != "off"
    try:
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        for ev in client.stream_events(
            messages,
            temperature=float(opts.get("temperature") or 0.7),
            max_tokens=int(opts.get("max_tokens") or 1024),
            top_p=float(opts["top_p"]) if opts.get("top_p") is not None else None,
            thinking=thinking,
        ):
            if ev.get("type") == "thinking":
                reasoning_parts.append(ev.get("text") or "")
            elif ev.get("type") == "delta":
                answer_parts.append(ev.get("text") or "")
    except Exception as e:  # noqa: BLE001
        return finish(False, None, str(e))
    answer = "".join(answer_parts)
    reasoning = "".join(reasoning_parts)
    observations["reasoning_returned"] = bool(reasoning.strip())
    observations["reasoning_characters"] = len(reasoning)
    observations["answer_characters"] = len(answer)
    return finish(True, {"content": answer, "reasoning_content": reasoning})


__all__ = ["router"]