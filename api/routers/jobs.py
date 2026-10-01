"""Task monitor API — modo_job 列表/详情，供前端「任务监控」页使用。

任务（modo_job）由 scheduler 创建（CRON 触发）或上传/API 触发，worker 执行时
更新 state / start_time / end_time / duration_ms / error_message（agent-gateway
任务在轮询期间把进度写入 error_message 槽位）。本路由只读查询，不做任何
调度/取消操作。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.framework import Job
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _job_to_dict(job: Job) -> dict:
    """Serialize one modo_job row for the monitor page."""
    params: dict = {}
    try:
        params = json.loads(job.task_params or "{}")
    except (TypeError, json.JSONDecodeError):
        params = {"_raw": job.task_params}
    return {
        "id": job.id,
        "task_id": job.task_id,
        "task_class": job.task_class,
        "queue_name": job.queue_name,
        "trigger_type": job.trigger_type,
        "state": job.state,
        "start_time": job.start_time.isoformat() if job.start_time else None,
        "end_time": job.end_time.isoformat() if job.end_time else None,
        "duration_ms": job.duration_ms,
        "error_message": job.error_message,
        "log_path": job.log_path,
        "create_time": job.create_time.isoformat() if job.create_time else None,
        "params": params,
    }


@router.get("")
def list_jobs(
    _user_id: str = Depends(_require_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    task_class: str | None = Query(None),
    state: str | None = Query(None),
    keyword: str | None = Query(None, description="job id 或 task_id 模糊匹配"),
    db: Session = Depends(get_db),
) -> dict:
    """任务列表：时间倒序，支持 task_class / state / keyword 过滤 + 分页。

    返回最近 N 条（默认 20），总数供前端分页。只读。
    """
    stmt = select(Job)
    count_stmt = select(func.count()).select_from(Job)

    if task_class:
        stmt = stmt.where(Job.task_class == task_class)
        count_stmt = count_stmt.where(Job.task_class == task_class)
    if state:
        stmt = stmt.where(Job.state == state)
        count_stmt = count_stmt.where(Job.state == state)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Job.id.like(like) | Job.task_id.like(like))
        count_stmt = count_stmt.where(Job.id.like(like) | Job.task_id.like(like))

    total = db.execute(count_stmt).scalar() or 0
    rows = (
        db.execute(
            stmt.order_by(Job.create_time.desc(), Job.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return {
        "success": True,
        "data": {
            "items": [_job_to_dict(j) for j in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/{job_id}")
def get_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """单任务详情（含解析后的 task_params / error_message 全文）。"""
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    return {"success": True, "data": _job_to_dict(job)}
