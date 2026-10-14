"""Task monitor API — modo_job 列表/详情，供前端「任务监控」页使用。

任务（modo_job）由 scheduler 创建（CRON 触发）或上传/API 触发，worker 执行时
更新 state / start_time / end_time / duration_ms / error_message（agent-gateway
任务在轮询期间把进度写入 error_message 槽位）。本路由只读查询，不做任何
调度/取消操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_db, get_sessionmaker
from api.config import get_settings
from api.models.framework import Job, JobQueue
from api.services.identity import decode_identity_cookie

if TYPE_CHECKING:  # 编排模型运行期仍在函数内局部 import（避免循环依赖）
    from api.models.orchestration import TapeRun

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


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
        # 2026-10-12 父子任务：parent_id（NULL=父任务）+ 子任务计数
        "parent_id": job.parent_id,
    }


def _parent_job_to_dict(job: Job, child_count: int, child_states: dict) -> dict:
    """父任务序列化：子任务聚合（数量 + 各状态计数）附加在父任务行上。"""
    d = _job_to_dict(job)
    d["child_count"] = child_count
    d["child_states"] = child_states
    return d


# ── 2026-10-12 父子任务视图 ─────────────────────────────────────────
# 父任务 = 文档处理链头（modo_job.parent_id IS NULL 的 API 触发任务）+
# 编排执行（kb_tape_run，虚拟父任务，子任务 = step_results）。
def _child_agg(db: Session, parent_ids: list[str]) -> dict[str, dict]:
    """按父任务 id 聚合子任务：{parent_id: {"count": N, "states": {...}}}"""
    if not parent_ids:
        return {}
    rows = db.execute(
        select(Job.parent_id, Job.state, func.count())
        .where(Job.parent_id.in_(parent_ids))
        .group_by(Job.parent_id, Job.state)
    ).all()
    agg: dict[str, dict] = {}
    for pid, state, cnt in rows:
        a = agg.setdefault(str(pid), {"count": 0, "states": {}})
        a["count"] += int(cnt)
        a["states"][str(state or "")] = int(cnt)
    return agg


@router.get("/parents")
def list_parent_jobs(
    _user_id: str = Depends(_require_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = Query(None),
    keyword: str | None = Query(None, description="job id / 文档 id / 编排名模糊匹配"),
    include_system: bool = Query(False, description="包含系统周期任务"),
    db: Session = Depends(get_db),
) -> dict:
    """父任务列表（2026-10-12 任务监控首屏，2026-10-13 合并分页重写）：

    两种父任务统一展示，且同页混排（时间倒序）：
      1. modo_job 链头（parent_id IS NULL 且非系统周期任务）
      2. 编排执行 kb_tape_run（虚拟父任务，子任务 = step_results 节点）

    每个父任务带 child_count（子任务数）与 child_states（各状态聚合），
    供前端首屏显示「进度 X/Y 完成」。

    分页实现（2026-10-13）：kb_tape_run 含大 JSON 列（step_results/bindings
    实测单行可达 ~940KB），SQL 全列 ORDER BY 会 filesort 爆 sort_buffer
    （1038 Out of sort memory，见 dc63463）。因此窄列全量载入
    （只取 id + 时间戳，不触碰大列）+ 应用层合并排序 + 切片 + 按 id 回表：
    既不爆 sort_buffer，也让编排 run 真正混入时间线（此前 run 只在 page==1
    出现，翻页即消失）。状态筛选经 _job_state_to_run 归一化（前端筛选值是
    modo_job 大写枚举，TapeRun.status 是小写——不映射则「选状态就看不到编排」）。
    """
    from api.models.orchestration import TapeRun

    # ── 窄列全量载入（不触碰大 JSON 列 → 无 filesort 宽行风险）──
    job_stmt = select(Job.id, Job.create_time).where(Job.parent_id.is_(None))
    if not include_system:
        job_stmt = job_stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
    if state:
        job_stmt = job_stmt.where(Job.state == state)
    if keyword:
        like = f"%{keyword}%"
        job_stmt = job_stmt.where(Job.id.like(like) | Job.task_id.like(like))
    job_rows = db.execute(job_stmt).all()  # [(id, create_time), ...]
    job_total = len(job_rows)

    run_stmt = select(TapeRun.id, TapeRun.created_at)
    run_state = _job_state_to_run(state) if state else None
    if run_state:
        run_stmt = run_stmt.where(TapeRun.status == run_state)
    if keyword:
        like = f"%{keyword}%"
        run_stmt = run_stmt.where(TapeRun.id.like(like) | TapeRun.tape_name.like(like))
    run_rows = db.execute(run_stmt).all()  # [(id, created_at), ...]

    # 2026-10-14：被链头任务承接的编排 run 不作为顶层父任务行。
    # 上传链/摘要触发（SUMMARY_RUN_*/ORCH_RUN_* job）会建一条 KbOrchestrationRunTask
    # job 并把 parent_id 指向链头（如 DOC_xxx）——该 run 已作为链头任务的子任务
    # 展示；若再按虚拟父任务渲染成顶层行，任务监控页就会出现「文档处理 + 摘要任务」
    # 平级两条（用户报告「外边那个摘要任务」）。因此顶层列表排除这些 run：
    # 存在 parent_id 非空且 task_params.runId 指向该 run 的 job 时，run 不进顶层。
    attached_run_ids: set[str] = set()
    if run_rows:
        attached = db.execute(
            select(Job.task_params, Job.parent_id)
            .where(Job.parent_id.is_not(None), Job.task_params.like("%runId%"))
        ).all()
        for tp, _pid in attached:
            try:
                rid = str((json.loads(tp or "{}") or {}).get("runId") or "")
                if rid:
                    attached_run_ids.add(rid)
            except (TypeError, json.JSONDecodeError):
                continue
    if attached_run_ids:
        run_rows = [r for r in run_rows if r[0] not in attached_run_ids]
    run_total = len(run_rows)

    # ── 应用层合并排序：时间倒序（job.create_time / run.created_at），同刻按 id 倒序 ──
    merged: list[tuple[str, object | None, str]] = [
        (rid, create_time, "job") for rid, create_time in job_rows
    ]
    merged += [(rid, created_at, "run") for rid, created_at in run_rows]
    merged.sort(key=lambda t: (t[1] or datetime.min, t[0]), reverse=True)

    start = (page - 1) * page_size
    page_merged = merged[start : start + page_size]

    # ── 按 id 回表取全行 ──
    job_ids = [rid for rid, _ts, kind in page_merged if kind == "job"]
    run_ids = [rid for rid, _ts, kind in page_merged if kind == "run"]
    jobs = (
        db.execute(select(Job).where(Job.id.in_(job_ids))).scalars().all() if job_ids else []
    )
    runs = (
        db.execute(select(TapeRun).where(TapeRun.id.in_(run_ids))).scalars().all()
        if run_ids
        else []
    )
    jobs_by_id = {j.id: j for j in jobs}
    runs_by_id = {r.id: r for r in runs}

    agg = _child_agg(db, job_ids)
    items: list[dict] = []
    for rid, _ts, kind in page_merged:
        if kind == "job":
            j = jobs_by_id.get(rid)
            if not j:
                continue
            items.append(
                _parent_job_to_dict(
                    j,
                    child_count=agg.get(j.id, {}).get("count", 0),
                    child_states=agg.get(j.id, {}).get("states", {}),
                )
            )
        else:
            r = runs_by_id.get(rid)
            if r:
                items.append(_run_to_job_dict(r))

    return {
        "success": True,
        "data": {
            "items": items,
            "total": job_total + run_total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/{job_id}/children")
def list_job_children(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """父任务的子任务列表（2026-10-12）：

    - modo_job 父任务 → 其 parent_id 指向它的所有子任务
    - 编排 run（虚拟父任务）→ 其 step_results 节点执行记录
    """
    from api.models.orchestration import TapeRun

    # 先查是否编排 run（虚拟父任务）
    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        return {
            "success": True,
            "data": {
                "kind": "orchestration",
                "items": (run.step_results or []),
                "total": len(run.step_results or []),
            },
        }
    # modo_job 父任务
    parent = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not parent:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    children = (
        db.execute(select(Job).where(Job.parent_id == job_id).order_by(Job.create_time.asc()))
        .scalars()
        .all()
    )
    # 2026-10-14：子任务是 KbOrchestrationRunTask（摘要/构建编排触发 job）时，
    # 把 task_params.runId 对应的 kb_tape_run 的 step_results 一并附加——任务监控页
    # 展开链头（如 DOC_xxx）能看到摘要任务内部的编排节点，而不只是一行 job。
    items: list[dict] = []
    for c in children:
        d = _job_to_dict(c)
        if c.task_class == "KbOrchestrationRunTask":
            try:
                rid = str((json.loads(c.task_params or "{}") or {}).get("runId") or "")
                if rid:
                    run = db.execute(select(TapeRun).where(TapeRun.id == rid)).scalars().first()
                    if run:
                        d["run_id"] = run.id
                        d["step_results"] = run.step_results or []
                        d["run_tape_name"] = run.tape_name
            except (TypeError, json.JSONDecodeError):
                pass
        items.append(d)
    return {
        "success": True,
        "data": {
            "kind": "job",
            "items": items,
            "total": len(items),
        },
    }


# 系统自愈周期任务（默认从任务列表隐藏，避免每 5 分钟刷屏）
SYSTEM_TASK_CLASSES: tuple[str, ...] = ("KbOrphanRecoveryTask",)

# ── 编排 run（kb_tape_run）与任务监控枚举对齐 ──────────────────────────
# 任务监控页的状态枚举沿用 modo_job 的大写形态（PENDING/RUNNING/SUCCESS/
# FAILED/STOPPED），而 TapeRun.status 是小写（queued/running/success/failed/
# cancelled）。筛选/统计/序列化统一经此映射，避免「选了状态就看不到编排」。
_RUN_STATE_TO_JOB = {
    "queued": "PENDING",
    "running": "RUNNING",
    "success": "SUCCESS",
    "failed": "FAILED",
    "cancelled": "STOPPED",
}
_JOB_STATE_TO_RUN = {v: k for k, v in _RUN_STATE_TO_JOB.items()}


def _run_state_to_job(state: str | None) -> str:
    """TapeRun.status（小写）→ 任务监控大写枚举；未知值原样返回。"""
    return _RUN_STATE_TO_JOB.get((state or "").lower(), state or "PENDING")


def _job_state_to_run(state: str | None) -> str | None:
    """任务监控筛选值（大写）→ TapeRun.status（小写）；非编排状态返回 None。"""
    return _JOB_STATE_TO_RUN.get((state or "").upper())


def _run_to_job_dict(run: TapeRun) -> dict:
    """编排 run → 任务监控 JobItem 同构序列化（虚拟父任务，state 统一大写）。"""
    steps = run.step_results or []
    states: dict[str, int] = {}
    for s in steps:
        st = str(s.get("status") or "")
        states[st] = states.get(st, 0) + 1
    return {
        "id": run.id,
        "task_id": run.id,
        "task_class": "OrchestrationRun",  # 虚拟任务类（前端按此渲染「编排执行」）
        "queue_name": "orch",
        "trigger_type": "API",
        "state": _run_state_to_job(run.status),
        "start_time": (run.created_at.isoformat() if run.created_at else None),
        "end_time": (run.updated_at.isoformat() if run.updated_at else None),
        "duration_ms": None,
        "error_message": run.error,
        "log_path": None,
        "create_time": (run.created_at.isoformat() if run.created_at else None),
        "params": {"tape_id": run.tape_id, "tape_name": run.tape_name, "inputs": run.inputs or {}},
        "parent_id": None,
        "child_count": len(steps),
        "child_states": states,
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
    include_system: bool = Query(False, description="包含系统周期任务（默认隐藏，避免 OrphanRecovery 刷屏）"),
    db: Session = Depends(get_db),
) -> dict:
    """任务列表：时间倒序，支持 task_class / state / keyword / queue 过滤 + 分页。

    2026-10-13：并入编排 run（OrchestrationRun 虚拟行），与 modo_job 同页
    混排（时间倒序）。窄列全量载入 + 应用层合并排序切片 + 回表，避免
    kb_tape_run 大 JSON 列 filesort 爆 sort_buffer（1038）。

    系统周期任务（如 KbOrphanRecoveryTask 每 5 分钟一条）默认隐藏，
    include_system=true 或显式 task_class 时才展示。只读。
    """
    from api.models.orchestration import TapeRun

    # ── 窄列全量载入 ──
    job_stmt = select(Job.id, Job.create_time)
    if task_class:
        job_stmt = job_stmt.where(Job.task_class == task_class)
    elif not include_system:
        job_stmt = job_stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
    if state:
        job_stmt = job_stmt.where(Job.state == state)
    if keyword:
        like = f"%{keyword}%"
        job_stmt = job_stmt.where(Job.id.like(like) | Job.task_id.like(like))
    if queue_name:
        job_stmt = job_stmt.where(Job.queue_name == queue_name)
    job_rows = db.execute(job_stmt).all()
    job_total = len(job_rows)

    run_rows: list = []
    # 编排 run 的任务类恒为 OrchestrationRun、队列恒为 orch：显式筛选
    # 其他值（task_class/queue_name）时 run 不参与。
    if not (task_class and task_class != "OrchestrationRun") and not (
        queue_name and queue_name != "orch"
    ):
        run_stmt = select(TapeRun.id, TapeRun.created_at)
        run_state = _job_state_to_run(state) if state else None
        if run_state:
            run_stmt = run_stmt.where(TapeRun.status == run_state)
        if keyword:
            like = f"%{keyword}%"
            run_stmt = run_stmt.where(TapeRun.id.like(like) | TapeRun.tape_name.like(like))
        run_rows = list(db.execute(run_stmt).all())
    run_total = len(run_rows)

    # ── 应用层合并排序 + 切片 + 回表 ──
    merged: list[tuple[str, object | None, str]] = [
        (rid, create_time, "job") for rid, create_time in job_rows
    ]
    merged += [(rid, created_at, "run") for rid, created_at in run_rows]
    merged.sort(key=lambda t: (t[1] or datetime.min, t[0]), reverse=True)

    start = (page - 1) * page_size
    page_merged = merged[start : start + page_size]

    job_ids = [rid for rid, _ts, kind in page_merged if kind == "job"]
    run_ids = [rid for rid, _ts, kind in page_merged if kind == "run"]
    jobs = (
        db.execute(select(Job).where(Job.id.in_(job_ids))).scalars().all() if job_ids else []
    )
    runs = (
        db.execute(select(TapeRun).where(TapeRun.id.in_(run_ids))).scalars().all()
        if run_ids
        else []
    )
    jobs_by_id = {j.id: j for j in jobs}
    runs_by_id = {r.id: r for r in runs}

    items: list[dict] = []
    for rid, _ts, kind in page_merged:
        if kind == "job":
            j = jobs_by_id.get(rid)
            if j:
                items.append(_job_to_dict(j))
        else:
            r = runs_by_id.get(rid)
            if r:
                items.append(_run_to_job_dict(r))

    return {
        "success": True,
        "data": {
            "items": items,
            "total": job_total + run_total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/statistics")
def job_statistics(
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """任务统计：按 state 分组计数（对齐 data-synth JobStatistics）。

    2026-10-13：并入编排 run（kb_tape_run）状态——run 状态小写枚举经
    _run_state_to_job 映射为大写后合入对应计数，统计卡与列表口径一致。
    """
    from api.models.orchestration import TapeRun

    rows = db.execute(
        select(Job.state, func.count()).group_by(Job.state)
    ).all()
    counts: dict[str, int] = {}
    for state, count in rows:
        counts[str(state or "")] = int(count)

    run_rows = db.execute(
        select(TapeRun.status, func.count()).group_by(TapeRun.status)
    ).all()
    for status, count in run_rows:
        key = _run_state_to_job(status)
        counts[key] = counts.get(key, 0) + int(count)

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

    2026-10-13：编排 run（OrchestrationRun）也支持停止——run 在
    queued/running 时置 cancelled，引擎 advance 的终态守卫
    （status not in (queued, running) 即不再推进）保证不再执行后续节点。
    """
    from api.models.orchestration import TapeRun

    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        if run.status not in ("queued", "running"):
            raise HTTPException(
                status_code=400, detail=f"编排 run 当前状态 {run.status}，不可停止"
            )
        run.status = "cancelled"
        run.error = (run.error or "") + " [任务监控页手动停止]"
        db.add(run)
        db.commit()
        return {
            "success": True,
            "data": {"id": job_id, "state": "STOPPED", "kind": "orchestration"},
        }

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
    """删除任务记录（modo_job 行；2026-10-13 起支持编排 run 记录）。

    2026-10-05 实测: 运行中/排队中任务直接删行，agent 仍会跑完（孤儿页）——
    删除前先 revoke celery（terminate），真正取消执行。
    """
    from api.models.orchestration import TapeRun

    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        db.delete(run)
        db.commit()
        return {"success": True, "data": {"id": job_id, "deleted": True, "kind": "orchestration"}}

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
    """单任务详情（含解析后的 task_params / error_message 全文）。

    2026-10-13：编排 run（OrchestrationRun）同样可查——JobLogDrawer
    打开编排行的「日志」走的就是本端点，此前 404。
    """
    from api.models.orchestration import TapeRun

    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        return {"success": True, "data": _run_to_job_dict(run)}

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
        # 直跑任务无 agent span，但技能 LLM 事件摘要存在（steps/tree 执行轨迹）
        ev_path = os.path.join(get_settings().kb_storage_dir, f"logs/events/{job_id}.summary.json")
        events: dict | None = None
        if os.path.isfile(ev_path):
            try:
                with open(ev_path, encoding="utf-8") as f:
                    events = json.load(f)
            except (OSError, json.JSONDecodeError):
                events = None
        return {
            "success": True,
            "data": {
                "spans": [],
                "has_trace": False,
                "trace_kind": "direct" if events else None,
                "summary": {"span_count": 0, "duration_ms": 0, "llm_calls": 0, "tools": []},
                "events": events,
            },
        }
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
            "trace_kind": "agent",
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
