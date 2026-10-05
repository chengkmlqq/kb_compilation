"""Task monitor API — modo_job 列表/详情，供前端「任务监控」页使用。

任务（modo_job）由 scheduler 创建（CRON 触发）或上传/API 触发，worker 执行时
更新 state / start_time / end_time / duration_ms / error_message（agent-gateway
任务在轮询期间把进度写入 error_message 槽位）。本路由只读查询，不做任何
调度/取消操作。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_db, get_sessionmaker
from api.config import get_settings
from api.models.framework import Job, JobQueue
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
    queue_name: str | None = Query(None, description="队列名精确匹配"),
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
    if queue_name:
        stmt = stmt.where(Job.queue_name == queue_name)
        count_stmt = count_stmt.where(Job.queue_name == queue_name)

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


@router.get("/statistics")
def job_statistics(
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """任务统计：按 state 分组计数（对齐 data-synth JobStatistics）。"""
    rows = db.execute(
        select(Job.state, func.count()).group_by(Job.state)
    ).all()
    counts: dict[str, int] = {}
    for state, count in rows:
        counts[str(state or "")] = int(count)

    return {
        "success": True,
        "data": {
            "total": sum(counts.values()),
            "running": counts.get("RUNNING", 0),
            "success": counts.get("SUCCESS", 0),
            "failed": counts.get("FAILED", 0),
            "stopped": counts.get("STOPPED", 0),
            "queued": counts.get("PENDING", 0),
        },
    }


@router.get("/queues")
def job_queues(
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """队列列表：modo_job 实际出现的 queue_name 去重（供筛选下拉）。"""
    rows = db.execute(
        select(Job.queue_name)
        .where(Job.queue_name.is_not(None), Job.queue_name != "")
        .distinct()
    ).all()
    items = [
        {"queueName": str(name), "queueLabel": None}
        for (name,) in rows
        if str(name or "").strip()
    ]
    items.sort(key=lambda q: q["queueName"].lower())
    return {"success": True, "data": items}


@router.post("/{job_id}/stop")
def stop_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """停止任务：仅运行中（RUNNING）可停，置为 STOPPED。"""
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state != "RUNNING":
        raise HTTPException(status_code=400, detail=f"任务当前状态 {job.state}，不可停止")
    job.state = "STOPPED"
    job.end_time = func.now()
    db.add(job)
    db.commit()
    return {"success": True, "data": {"id": job_id, "state": "STOPPED"}}


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """删除任务记录（modo_job 行）。"""
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    db.delete(job)
    db.commit()
    return {"success": True, "data": {"id": job_id, "deleted": True}}


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


@router.get("/{job_id}/trace")
def get_job_trace(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
) -> dict:
    """Agent 任务 span 明细：读 logs/traces/{job_id}.json（worker 落盘）。"""
    path = os.path.join(get_settings().kb_storage_dir, f"logs/traces/{job_id}.json")
    if not os.path.isfile(path):
        return {"success": True, "data": {"spans": [], "has_trace": False}}
    try:
        with open(path, encoding="utf-8") as f:
            spans = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"trace 读取失败: {exc}")
    from worker.agent.trace_store import summarize_spans  # type: ignore[import-not-found]

    return {
        "success": True,
        "data": {"spans": spans, "has_trace": True, "summary": summarize_spans(spans)},
    }


# ---------------------------------------------------------------------------
# 实时日志流（SSE）——对齐 data-synth job-monitor/log-stream
# ---------------------------------------------------------------------------

# 增量日志快照优先级：MinIO 日志对象（worker append_job_log 实时追加）>
# error_message 槽位（兼容无 MinIO 环境）
async def _read_incremental(
    db: Session,
    job: Job,
    offset_bytes: int,
) -> dict:
    content = ""
    source = "none"

    # 1) 本地日志文件 logs/jobs/<job_id>.log（scheduler execute_modo_job 写入，
    #    Job.log_path 落库；ff91de8 起支持）
    if job.log_path:
        abs_path = job.log_path if os.path.isabs(job.log_path) else os.path.join(
            get_settings().kb_storage_dir, job.log_path
        )
        if os.path.exists(abs_path):
            try:
                size = os.path.getsize(abs_path)
                if size > offset_bytes:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(offset_bytes)
                        content = fh.read()
                    source = "local"
                    offset_bytes = size
            except OSError:
                pass

    # 2) MinIO 日志对象 logs/<job_id>.log（worker log_sink.append_job_log 追加写入）
    if not content:
        try:
            from api.services import storage as storage_svc

            if storage_svc.remote_enabled():
                minio_log = (
                    f"minio://{storage_svc.get_settings().MINIO_BUCKET or 'kb-compilation'}"
                    f"/logs/{job.id}.log"
                )
                size = storage_svc.stat_size(minio_log)
                if size is not None and size > offset_bytes:
                    data = storage_svc.get_bytes(minio_log)
                    text = data.decode("utf-8", errors="replace")
                    content = text[offset_bytes:]
                    source = "minio"
                    offset_bytes = size
                elif size is not None and size == offset_bytes:
                    source = "minio"
        except Exception:  # noqa: BLE001 — 日志读取失败回退 error_message
            pass

    # 3) error_message 槽位（实时进度：agent-gateway 每轮 poll 覆盖写入）
    if not content and job.error_message:
        err = str(job.error_message)
        if len(err) > offset_bytes:
            content = err[offset_bytes:]
            source = "error_message"
            offset_bytes = len(err)
        elif len(err) == offset_bytes:
            source = "error_message"

    return {"content": content, "next_offset": offset_bytes, "source": source}


@router.get("/{job_id}/log-stream")
async def job_log_stream(
    request: Request,
    job_id: str,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    offset: int = Query(0, ge=0),
):
    """SSE 实时日志流：增量推送 error_message 进度 / log_path 文件内容。

    语义对齐 data-synth /api/job-monitor/log-stream：
    - data: {"type":"chunk","payload":{content,offset,state,source,ts}}
    - data: {"type":"warning"|"error"|"finish", ...}
    终态（SUCCESS/FAILED/STOPPED）后自动 close；1s 轮询；30s 空闲超时。

    **连接不持有长 DB session**：每轮查询用独立短会话（get_sessionmaker()()）
    轮末即还——SSE 流可能持续数秒到数十秒，若用 Depends(get_db) 的 session，
    并发日志流会占满 QueuePool(size 10) 导致整个 api-server 的 DB 接口
    TimeoutError 挂起（实测：EventSource 重连风暴 + 多流并发 → 全部接口 30s 超时）。
    """
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")

    TERMINAL_STATES = {"SUCCESS", "FAILED", "STOPPED"}
    POLL_INTERVAL_S = 1.0
    MAX_IDLE_LOOPS = 30
    TERMINAL_GRACE_LOOPS = 5

    async def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        offset_bytes = max(0, offset)
        idle_loops = 0
        terminal_grace = 0
        last_warning = ""
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 独立短会话：每轮查询即开即关，SSE 流期间不占连接池
                with get_sessionmaker()() as sdb:
                    job = sdb.execute(
                        select(Job).where(Job.id == job_id)
                    ).scalars().first()
                    if not job:
                        yield await _sse({"type": "finish", "payload": {
                            "state": "NOT_FOUND", "reason": "作业不存在",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }})
                        break

                    state = str(job.state or "").upper()
                    read = await _read_incremental(sdb, job, offset_bytes)

                if read["content"]:
                    offset_bytes = read["next_offset"]
                    idle_loops = 0
                    terminal_grace = 0
                    yield await _sse({"type": "chunk", "payload": {
                        "content": read["content"], "offset": offset_bytes,
                        "state": state, "source": read["source"],
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }})
                else:
                    idle_loops += 1

                if state in TERMINAL_STATES:
                    if not read["content"]:
                        terminal_grace += 1
                    if terminal_grace >= 1:
                        yield await _sse({"type": "finish", "payload": {
                            "state": state, "offset": offset_bytes,
                            "source": read["source"],
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }})
                        break

                if idle_loops >= MAX_IDLE_LOOPS:
                    yield await _sse({"type": "finish", "payload": {
                        "state": state or "TIMEOUT", "offset": offset_bytes,
                        "reason": "SSE idle timeout",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }})
                    break

                await asyncio.sleep(POLL_INTERVAL_S)
        except Exception as exc:  # noqa: BLE001
            yield await _sse({"type": "error", "payload": {
                "message": str(exc),
                "ts": datetime.now(timezone.utc).isoformat(),
            }})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
