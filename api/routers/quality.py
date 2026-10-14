"""质量评估 API（质量评估页面 v1，2026-10-14）。

页面能力：
- 发起评估：单文档 / 批量文档（调「质量评估-QA」编排 execute，异步）
- 任务列表：kb_quality_run 分页（状态/三维度得分/综合分/问题数）
- 任务详情：kb_quality_run + kb_quality_report 明细（三维度指标/问题/归因）

鉴权：_require_caller（登录态；管理页操作由编排执行链路负责）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import QualityReport, QualityRun
from api.services.identity import decode_identity_cookie
from api.services.orchestration import execute_tape
from api.services.scope import is_admin

logger = logging.getLogger("kb.quality")

router = APIRouter(prefix="/quality", tags=["quality"])

# 「质量评估-QA」种子编排名（seed_framework_db.py 固化）
QUALITY_TAPE_NAME = "质量评估-QA"


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return {"user_id": identity.user_id, "team_name": identity.team_name or "",
            "is_admin": is_admin(db, identity.user_id)}


class QualityLaunchRequest(BaseModel):
    kb_id: str
    doc_ids: list[str] = []
    scope: str = "all"  # single|multi|all


def _tape_id_by_name(db: Session, name: str) -> str:
    from api.models.orchestration import Tape

    t = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
    if not t:
        raise HTTPException(status_code=404, detail=f"编排不存在: {name}（先跑 seed 建种子编排）")
    return t.id


@router.post("/runs")
def launch_quality(req: QualityLaunchRequest, db: Session = Depends(get_db),
                   caller: dict = Depends(_require_caller)) -> dict:
    """发起质量评估（异步）：调「质量评估-QA」编排 execute。

    scope 自动推导：doc_ids 1 个 = single，>1 = multi，空 = all（整库）。
    """
    scope = req.scope
    if not scope or scope not in ("single", "multi", "all"):
        scope = "all" if not req.doc_ids else ("single" if len(req.doc_ids) == 1 else "multi")
    inputs = {
        "kb_id": req.kb_id,
        "doc_ids": ",".join(req.doc_ids),
        "scope": scope,
    }
    try:
        tape_id = _tape_id_by_name(db, QUALITY_TAPE_NAME)
        r = execute_tape(db, tape_id, inputs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    run_id = r.get("run_id") or ""
    # 编排执行记录在 dev 库；kb_quality_run 落框架库。先记一行 queued 占位（编排汇总节点会 UPDATE）
    from uuid import uuid4

    qrun = QualityRun(id=uuid4().hex[:32], kb_id=req.kb_id, scope=scope,
                      doc_ids=req.doc_ids or None, status="queued",
                      created_by=caller.get("user_id") or "")
    db.add(qrun)
    db.commit()
    return {"success": True, "data": {"run_id": run_id, "quality_id": qrun.id,
                                      "scope": scope, "status": "queued"}}


@router.get("/runs")
def list_quality_runs(
    kb_id: str = Query("", max_length=64),
    status: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """质量评估任务列表（kb_quality_run 分页）。"""
    from sqlalchemy import func

    q = select(QualityRun)
    if kb_id:
        q = q.where(QualityRun.kb_id == kb_id)
    if status:
        q = q.where(QualityRun.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    rows = db.execute(
        q.order_by(QualityRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    items = [_run_item(r) for r in rows]
    return {"success": True, "data": {"total": total, "page": page, "page_size": page_size,
                                      "items": items}}


@router.get("/runs/{quality_id}")
def quality_run_detail(quality_id: str, db: Session = Depends(get_db),
                       caller: dict = Depends(_require_caller)) -> dict:
    """质量评估任务详情：run 汇总 + 三维度 report 明细。"""
    run = db.get(QualityRun, quality_id)
    if not run:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    reports = db.execute(
        select(QualityReport).where(QualityReport.run_id == run.id).order_by(QualityReport.dim)
    ).scalars().all()
    dims = {}
    for rp in reports:
        dims[rp.dim] = {"metrics": rp.metrics or {}, "issues": rp.issues or []}
    data = {**_run_item(run), "reports": dims}
    return {"success": True, "data": data}


def _run_item(r: QualityRun) -> dict:
    scores = {}
    for dim, key in (("chunk", "chunk_metrics"), ("wiki", "wiki_metrics"), ("graph", "graph_metrics")):
        m = getattr(r, key) or {}
        scores[dim] = m.get("health_score") if isinstance(m, dict) else None
    return {
        "id": r.id,
        "kb_id": r.kb_id,
        "scope": r.scope,
        "doc_ids": r.doc_ids or [],
        "status": r.status,
        "scores": scores,
        "issues_count": len(r.issues or []),
        "attribution": r.attribution or [],
        "summary": r.summary or "",
        "error": r.error or "",
        "created_by": r.created_by or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
