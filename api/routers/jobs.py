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


# 系统自愈周期任务（默认从任务列表隐藏，避免每 5 分钟刷屏）
SYSTEM_TASK_CLASSES: tuple[str, ...] = ("KbOrphanRecoveryTask",)


@router.get("")
def list_jobs(
    _user_id: str = Depends(_require_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    task_class: str | None = Query(None),
    state: str | None = Query(None),
    keyword: str | None = Query(None, description="job id 或 task_id 模糊匹配"),
    queue_name: str | None = Query(None, description="队列名精确匹配"),
    include_system: bool = Query(False, description="包含系统周期任务（默认隐藏，避免 OrphanRecovery 刷屏）"),
    db: Session = Depends(get_db),
) -> dict:
    """任务列表：时间倒序，支持 task_class / state / keyword 过滤 + 分页。

    系统周期任务（如 KbOrphanRecoveryTask 每 5 分钟一条）默认隐藏，
    include_system=true 或显式 task_class 时才展示。只读。
    """
    stmt = select(Job)
    count_stmt = select(func.count()).select_from(Job)

    if task_class:
        stmt = stmt.where(Job.task_class == task_class)
        count_stmt = count_stmt.where(Job.task_class == task_class)
    elif not include_system:
        # 隐藏系统自愈周期任务（孤儿回收等），避免刷屏淹没真实任务
        stmt = stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
        count_stmt = count_stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
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
    """停止任务：运行中（RUNNING）或排队中（PENDING）可停，置为 STOPPED。

    置 STOPPED 后 worker 执行前检查（scheduler.execute_modo_job 的
    "job not executable" 守卫）会拒执行；执行中停掉的任务，handler 跑完后
    终态保护保证不再覆盖成 SUCCESS/FAILED（2026-10-05 修复「取消复活」）。
    """
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state not in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=400, detail=f"任务当前状态 {job.state}，不可停止")
    job.state = "STOPPED"
    job.end_time = func.now()
    db.add(job)
    db.commit()
    return {"success": True, "data": {"id": job_id, "state": "STOPPED"}}


_RETRYABLE_STATES = ("FAILED", "STOPPED")
_AGENT_TASK_CLASSES = ("KbAgentWikiBuildTask", "KbAgentGatewayTask")


@router.post("/{job_id}/retry")
def retry_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """重试任务：FAILED/STOPPED 可重试，重置为 PENDING 并重新入队执行。

    重新投递到任务原队列（job.queue_name，空则按 task_class 推断：
    agent 类任务 → agent 队列，其余 → default），与上传自动链的投递
    契约一致（send_task execute_modo_job + task_id=job_id）。
    """
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state not in _RETRYABLE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"任务当前状态 {job.state}，不可重试（仅 {_RETRYABLE_STATES} 可重试）",
        )
    original_state = job.state
    queue = (job.queue_name or "").strip() or (
        "agent" if (job.task_class or "") in _AGENT_TASK_CLASSES else "default"
    )
    # 先重置为 PENDING（清失败/取消痕迹），再投递；投递失败回滚状态，避免卡死 PENDING
    job.state = "PENDING"
    job.error_message = None
    job.end_time = None
    job.duration_ms = None
    db.commit()
    try:
        from worker.celery_app import celery_app

        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue=queue,
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时回滚，任务保持 FAILED/STOPPED 可再试
        db.refresh(job)
        job.state = original_state
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"重试入队失败，状态已回滚为 {original_state}: {exc}",
        ) from exc
    return {"success": True, "data": {"id": job_id, "state": "PENDING", "queue": queue}}


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """删除任务记录（modo_job 行）。

    2026-10-05 实测: 运行中/排队中任务直接删行，agent 仍会跑完（孤儿页）——
    删除前先 revoke celery（terminate），真正取消执行。
    """
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state in ("PENDING", "RUNNING"):
        try:
            from worker.celery_app import celery_app

            celery_app.control.revoke(job_id, terminate=True)
        except Exception as exc:  # noqa: BLE001 — revoke 失败不阻塞删除
            logger.warning("revoke job %s failed: %s", job_id, exc)
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

    # 摘要（与 worker/agent/trace_store.summarize_spans 同语义，api-server 无 worker 包）
    def _span_name(sp: dict) -> str:
        sd = sp.get("span_data") or {}
        return str(sd.get("name") or ("LLM generation" if sd.get("type") == "generation" else sd.get("type") or "span"))

    def _span_duration_ms(sp: dict) -> int:
        try:
            start = datetime.fromisoformat(str(sp.get("started_at")).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(sp.get("ended_at")).replace("Z", "+00:00"))
            return max(0, int((end - start).total_seconds() * 1000))
        except Exception:  # noqa: BLE001
            return 0

    total_ms = 0.0
    tools: list[str] = []
    llm_calls = 0
    enriched: list[dict] = []
    for sp in spans:
        dur = _span_duration_ms(sp)
        total_ms += dur
        sd = sp.get("span_data") or {}
        stype = str(sd.get("type") or "")
        if stype == "function":
            fn = str(sd.get("name") or "")
            if fn and fn not in tools:
                tools.append(fn)
        elif stype == "generation":
            llm_calls += 1
        # 顶层便捷字段（前端直接读）：name / type / duration_ms
        enriched.append(
            {
                **sp,
                "name": _span_name(sp),
                "type": stype or "span",
                "duration_ms": dur,
            }
        )
    summary = {
        "span_count": len(spans),
        "duration_ms": round(total_ms),
        "llm_calls": llm_calls,
        "tools": tools,
    }
    # 技能 LLM 事件摘要（logs/events/{job_id}.summary.json，worker 聚合落盘）
    events: dict | None = None
    ev_path = os.path.join(get_settings().kb_storage_dir, f"logs/events/{job_id}.summary.json")
    if os.path.isfile(ev_path):
        try:
            with open(ev_path, encoding="utf-8") as f:
                events = json.load(f)
        except (OSError, json.JSONDecodeError):
            events = None
    return {
        "success": True,
        "data": {
            "spans": enriched,
            "has_trace": True,
            "summary": summary,
            "events": events,
        },
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
