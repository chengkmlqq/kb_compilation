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


class VerifyAllRequest(BaseModel):
    """批量核对过滤条件（全部可选；不传 = 核对当前用户可见的全部知识库）。"""
    kb_ids: list[str] | None = None


@router.post("/kbs/chunks/verify-all")
def verify_all_chunks(
    req: VerifyAllRequest | None = None,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """整体健康检查：一次核对可见知识库切片 ↔ 向量库，返回逐库报告 + 汇总。

    与单库 verify 同实现（chunk_verify.verify_kb），只是按可见性批量遍历；
    不传 kb_ids 时核对全部可见库（admin 可见全部 system+own+team）。
    """
    from sqlalchemy import select

    from api.models.knowledge import KbDatasource
    from api.services.chunk_verify import verify_kb
    from api.services.kb_admin import kb_visible_clauses

    stmt = select(KbDatasource.id, KbDatasource.name).where(KbDatasource.state == "1")
    clauses = kb_visible_clauses(caller.user_id, caller.team_name, caller.is_admin)
    if clauses:
        stmt = stmt.where(clauses[0])
    if req and req.kb_ids:
        stmt = stmt.where(KbDatasource.id.in_(req.kb_ids))
    kb_rows = db.execute(stmt).all()

    reports = []
    for kid, kb_name in kb_rows:
        try:
            report = verify_kb(kid)
        except Exception as e:  # noqa: BLE001 — 单库失败不阻断整体检查
            report = {
                "kb_id": kid,
                "doc_chunks": 0,
                "vector_chunks": 0,
                "missing_in_vector": 0,
                "orphan_vectors": 0,
                "missing_samples": [],
                "orphan_samples": [],
                "ok": False,
                "error": str(e),
            }
        report["kb_name"] = kb_name or ""
        reports.append(report)
    ok_count = sum(1 for r in reports if r.get("ok"))
    return {
        "success": True,
        "data": {
            "total": len(reports),
            "ok": ok_count,
            "unhealthy": len(reports) - ok_count,
            "items": reports,
        },
    }


@router.post("/kbs/{kb_id}/chunks/verify")
def verify_chunks(kb_id: str, caller: Caller = Depends(_require_caller)) -> dict:
    """立即核对：MySQL 切片 ↔ 向量库（通用实现，不依赖 pgvector/ES）。"""
    from api.services.chunk_verify import verify_kb

    return {"success": True, "data": verify_kb(kb_id)}


@router.post("/kbs/{kb_id}/chunks/verify/fix")
def fix_chunks(kb_id: str, caller: Caller = Depends(_require_caller)) -> dict:
    """投递切片核对修复任务（清孤儿向量 + 重嵌入缺失切片，worker 异步执行）。"""
    import json
    import uuid

    from api.db import get_sessionmaker
    from api.models.framework import Job

    job_id = f"VERIFY_{uuid.uuid4().hex}"
    db = get_sessionmaker()()
    try:
        db.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class="KbChunkVerifyTask",
                queue_name="default",
                task_params=json.dumps({"kb_id": kb_id, "action": "fix"}, ensure_ascii=False),
                trigger_type="API",
                state="PENDING",
            )
        )
        db.commit()
    finally:
        db.close()

    from worker.celery_app import celery_app

    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job",
        args=[job_id],
        task_id=job_id,
        queue="default",
    )
    return {"success": True, "data": {"job_id": job_id, "action": "fix"}}