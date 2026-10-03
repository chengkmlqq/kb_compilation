"""Chunking configuration + preview + parser engine discovery API routes.

Mirrors the WeKnora chunking settings surface:
- GET/PUT /api/v1/kbs/{kb_id}/chunking-config   KB-level split config
- POST /api/v1/kbs/chunk-preview               run the chunker for the debug UI
- GET  /api/v1/parsers/engines                  engine list + availability

The preview endpoint is intentionally unauthenticated for text (it runs no DB
writes and no embedding) but still requires a logged-in cookie to match the
rest of the platform surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.chunking import approx_token_count, ChunkConfig
from api.services.chunking_orch import split_parent_child, split_with_diagnostics
from api.services.identity import decode_identity_cookie
from api.services.kb_chunking import defaults_dict, load_chunking, save_chunking
from api.services.parser_registry import list_engines

router = APIRouter(tags=["chunking"])


@dataclass
class Caller:
    user_id: str
    team_name: str
    is_admin: bool


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> Caller:
    from api.services.scope import is_admin

    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return Caller(
        user_id=identity.user_id,
        team_name=identity.team_name or "",
        is_admin=is_admin(db, identity.user_id),
    )


class ChunkingConfigRequest(BaseModel):
    chunk_size: int | None = Field(default=None, ge=64, le=32768)
    chunk_overlap: int | None = Field(default=None, ge=0, le=4096)
    separators: list[str] | None = None
    strategy: str | None = None
    token_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    languages: list[str] | None = None
    enable_parent_child: bool | None = None
    parent_chunk_size: int | None = Field(default=None, ge=512, le=8192)
    child_chunk_size: int | None = Field(default=None, ge=64, le=2048)
    parser_engine_rules: list[dict] | None = None
    table_metadata_instructions: str | None = None


class ChunkPreviewRequest(BaseModel):
    text: str = Field(..., max_length=400_000)
    config: dict | None = None
    kb_id: str | None = None


# --------------------------------------------------------------------------- #
# KB chunking config
# --------------------------------------------------------------------------- #


@router.get("/kbs/{kb_id}/chunking-config")
def get_chunking_config(
    kb_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": {"chunking": load_chunking(db, kb_id), "defaults": defaults_dict()}}


@router.put("/kbs/{kb_id}/chunking-config")
def put_chunking_config(
    kb_id: str,
    body: ChunkingConfigRequest,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    patch = body.model_dump(exclude_none=True)
    try:
        merged = save_chunking(db, kb_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "data": {"chunking": merged}}


# --------------------------------------------------------------------------- #
# preview / engines
# --------------------------------------------------------------------------- #


@router.post("/kbs/chunk-preview")
def chunk_preview(
    body: ChunkPreviewRequest,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    raw = dict(body.config or {})
    if body.kb_id:
        base = load_chunking(db, body.kb_id)
        base.update({k: v for k, v in raw.items() if v is not None})
        raw = base
    cfg = ChunkConfig.from_dict(raw)

    if cfg.enable_parent_child:
        children, parents = split_parent_child(body.text, cfg)
        lang = cfg.lang_for(body.text)
        return {
            "success": True,
            "data": {
                "chunks": [
                    {
                        "seq": c.seq,
                        "content": c.content,
                        "chars": len(c.content),
                        "tokens": approx_token_count(c.content, lang),
                        "is_parent": False,
                        "parent_seq": c.parent_seq,
                    }
                    for c in children
                ],
                "parents": [
                    {
                        "seq": p.seq,
                        "content": p.content,
                        "chars": len(p.content),
                        "tokens": approx_token_count(p.content, lang),
                    }
                    for p in parents
                ],
                "diagnostics": {
                    "selected_tier": "parent_child",
                    "tier_chain": ["parent_child"],
                    "rejected": [],
                    "profile": None,
                },
            },
        }

    chunks, diag = split_with_diagnostics(body.text, cfg)
    lang = cfg.lang_for(body.text)
    return {
        "success": True,
        "data": {
            "chunks": [
                {
                    "seq": c.seq,
                    "content": c.content,
                    "chars": len(c.content),
                    "tokens": approx_token_count(c.content, lang),
                    "is_parent": False,
                    "parent_seq": c.parent_seq,
                }
                for c in chunks
            ],
            "parents": [],
            "diagnostics": diag.to_dict(),
        },
    }


@router.get("/parsers/engines")
def get_parser_engines(caller: Caller = Depends(_require_caller)) -> dict:
    return {"success": True, "data": list_engines()}