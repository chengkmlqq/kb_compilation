"""Cron scanner — beat task that polls modo_cron_task and dispatches due jobs.

Ported from the source platform's job scheduler (scan_cron_tasks),
retaining its key correctness properties:

1. Initialization: tasks with empty next_fire_time get their next fire time
   computed once and written back.
2. Expression-change recalibration: if the stored next_fire_time disagrees with
   what the current cron expression yields, it is recalibrated (so editing a
   cron expression does not strand a task in the far future).
3. Optimistic-lock dispatch: the UPDATE that advances next_fire_time is
   guarded by `WHERE next_fire_time = :old`, so a multi-instance beat never
   double-fires a task.
4. Each fired task gets a modo_job row (trigger_type=CRON) before enqueueing,
   giving the job monitor page a record even if the worker never runs it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import session_scope
from api.models.framework import CronTask, Job
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

LOCAL_TIMEZONE = ZoneInfo(settings.CELERY_TIMEZONE)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _now_local_naive() -> dt.datetime:
    return dt.datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)


def _to_next_fire_time_text(value: dt.datetime) -> str:
    """Serialize as UTC ISO-8601 with trailing Z (matches the source DB)."""
    normalized = value.astimezone(dt.timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_next_fire_time(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        raw = _normalize_text(value)
        if not raw:
            return None
        normalized = raw.replace(" ", "T").replace("Z", "+00:00")
        parsed = None
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = dt.datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(LOCAL_TIMEZONE)


def _generate_job_id(task_class: str) -> str:
    """Readable job id prefix, mirroring the source platform's job-id map."""
    prefix_map = {
        "MetadataCollectionTask": "METADATA",
        "IdentitySyncTask": "IDENTITY_SYNC",
        "FileCleanupTask": "FILE_CLEANUP",
    }
    prefix = prefix_map.get(task_class, "JOB")
    return f"{prefix}_{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}"


def _enqueue_job(job_id: str, queue_name: str | None) -> str:
    queue = _normalize_text(queue_name) or "default"
    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job",
        args=[job_id],
        task_id=job_id,
        queue=queue,
    )
    return queue


def _claim_next_fire(db: Session, task_id: str, old_text: str, new_text: str) -> bool:
    """Advance next_fire_time under optimistic lock; True if we won the race."""
    result = db.execute(
        update(CronTask)
        .where(CronTask.id == task_id, CronTask.next_fire_time == old_text)
        .values(next_fire_time=new_text)
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0) > 0


@celery_app.task(name="worker.tasks.scheduler.scan_cron_tasks", bind=False)
def scan_cron_tasks() -> dict:
    """Poll modo_cron_task, fire due jobs, advance schedules."""
    now = dt.datetime.now(LOCAL_TIMEZONE)
    initialized = 0
    triggered = 0
    created_jobs = 0

    with session_scope() as db:
        try:
            cron_rows = (
                db.execute(
                    select(
                        CronTask.id,
                        CronTask.cron_expression,
                        CronTask.next_fire_time,
                        CronTask.task_class,
                        CronTask.fire_params,
                        CronTask.queue_name,
                    ).where(CronTask.state == "1")
                )
                .mappings()
                .all()
            )
        except Exception:  # table not seeded yet (fresh DB, beat starts before seed)
            db.rollback()
            return {"success": True, "initialized": 0, "triggered": 0, "createdJobs": 0, "skipped": "cron_task table missing"}

    for row in cron_rows:
        cron_task_id = _normalize_text(row.get("id"))
        cron_expression = _normalize_text(row.get("cron_expression"))
        task_class = _normalize_text(row.get("task_class"))
        queue_name = _normalize_text(row.get("queue_name")) or "default"
        fire_params = row.get("fire_params")

        if not cron_task_id or not cron_expression or not task_class:
            continue

        old_next_fire_time_text = _normalize_text(row.get("next_fire_time"))
        old_next_fire_time = (
            _parse_next_fire_time(old_next_fire_time_text) if old_next_fire_time_text else None
        )

        # --- Case 1: uninitialized -> compute + store next fire time ---
        if old_next_fire_time is None:
            try:
                next_run = croniter(cron_expression, now).get_next(dt.datetime)
                next_fire_time = _to_next_fire_time_text(next_run)
            except Exception:
                logger.exception(
                    "scan_cron_tasks failed to parse cron expr: task_id=%s expr=%s",
                    cron_task_id,
                    cron_expression,
                )
                continue
            with session_scope() as db:
                result = db.execute(
                    update(CronTask)
                    .where(
                        CronTask.id == cron_task_id,
                        (CronTask.next_fire_time.is_(None)) | (CronTask.next_fire_time == ""),
                    )
                    .values(next_fire_time=next_fire_time)
                )
            if int(getattr(result, "rowcount", 0) or 0) > 0:
                initialized += 1
            continue

        # --- Case 2: scheduled in the future; recalibrate if expr changed ---
        if old_next_fire_time > now:
            try:
                expected_next_run = croniter(cron_expression, now).get_next(dt.datetime)
            except Exception:
                logger.exception(
                    "scan_cron_tasks failed to parse cron expr: task_id=%s expr=%s",
                    cron_task_id,
                    cron_expression,
                )
                continue
            if expected_next_run.replace(microsecond=0) != old_next_fire_time.replace(microsecond=0):
                next_fire_time = _to_next_fire_time_text(expected_next_run)
                with session_scope() as db:
                    db.execute(
                        update(CronTask)
                        .where(
                            CronTask.id == cron_task_id,
                            CronTask.next_fire_time == old_next_fire_time_text,
                        )
                        .values(next_fire_time=next_fire_time)
                    )
                logger.info(
                    "scan_cron_tasks recalibrated next_fire_time: task_id=%s old=%s new=%s",
                    cron_task_id,
                    old_next_fire_time_text,
                    next_fire_time,
                )
            continue

        # --- Case 3: due -> claim (optimistic lock) + insert job + enqueue ---
        try:
            next_run = croniter(cron_expression, now).get_next(dt.datetime)
            next_fire_time = _to_next_fire_time_text(next_run)
        except Exception:
            logger.exception(
                "scan_cron_tasks failed to compute next run: task_id=%s expr=%s",
                cron_task_id,
                cron_expression,
            )
            continue

        with session_scope() as db:
            locked = _claim_next_fire(db, cron_task_id, old_next_fire_time_text, next_fire_time)
        if not locked:
            # Another beat instance already claimed it.
            continue
        triggered += 1

        job_id = _generate_job_id(task_class)
        with session_scope() as db:
            db.add(
                Job(
                    id=job_id,
                    task_id=cron_task_id,
                    task_class=task_class,
                    queue_name=queue_name,
                    task_params=fire_params,
                    trigger_type="CRON",
                    state="PENDING",
                )
            )
        created_jobs += 1

        try:
            _enqueue_job(job_id, queue_name)
        except Exception:
            logger.exception("scan_cron_tasks failed to enqueue job: job_id=%s", job_id)

    return {
        "success": True,
        "initialized": initialized,
        "triggered": triggered,
        "createdJobs": created_jobs,
    }


def _update_duration(db: Session, job: Job) -> None:
    """Compute and persist duration_ms from start/end timestamps.

    MySQL DATETIME columns round-trip as NAIVE datetimes, while we write UTC
    aware values — so strip tzinfo before subtracting (assume both came from
    the same wall-clock source).
    """
    if job.start_time and job.end_time:
        start = job.start_time.replace(tzinfo=None)
        end = job.end_time.replace(tzinfo=None)
        delta = end - start
        job.duration_ms = int(delta.total_seconds() * 1000)


def _job_log_path(job_id: str) -> str:
    """任务日志相对路径（jobs API 会拼 kb_storage_dir；相对方便移植）。"""
    return f"logs/jobs/{job_id}.log"


def _append_job_log(job_id: str, line: str) -> None:
    """追加一行任务日志到 log_path（失败不抛——日志是尽力而为）。"""
    try:
        from pathlib import Path

        settings = get_settings()
        path = Path(settings.kb_storage_dir) / _job_log_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {line}\n")
    except Exception:  # noqa: BLE001 — 日志写入失败不影响任务本身
        pass


@celery_app.task(name="worker.tasks.scheduler.recover_orphan_running_jobs", bind=False)
def recover_orphan_running_jobs() -> dict:
    """周期回收孤儿 RUNNING 任务（beat 每 5 分钟）。"""
    from worker.tasks.orphan_recovery import recover_orphan_running_jobs as _recover

    return _recover()


@celery_app.task(name="worker.tasks.scheduler.execute_modo_job", bind=False, acks_late=True)
def execute_modo_job(job_id: str) -> dict:
    """Execute a modo_job by dispatching to the registered task_class handler.

    The task_class -> callable mapping is populated by the WeKnora migration
    phase (doc parse / embed / wiki / graph tasks register here).
    """
    normalized = _normalize_text(job_id)
    if not normalized:
        return {"success": False, "error": "job_id is required"}

    with session_scope() as db:
        job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
        if not job:
            return {"success": False, "error": f"job not found: {normalized}"}
        if job.state not in ("PENDING", "RUNNING", None, ""):
            return {"success": False, "error": f"job not executable: state={job.state}"}
        # RUNNING 重入守卫（2026-10-07）：同一 job 的重复投递（如批量重投未去重）
        # 会起两个实例并行构建同一文档——页面竞态 + LLM 双倍消耗。RUNNING 且活跃
        # （start_time 距今 <30min）则拒绝重入；孤儿恢复阈值 60min，重投时已超 30min
        # 不受影响（RUNNING 重入续跑语义保留）。
        if job.state == "RUNNING" and job.start_time:
            try:
                age_min = (
                    dt.datetime.now(dt.timezone.utc) - job.start_time
                ).total_seconds() / 60
                if age_min < 30:
                    return {
                        "success": False,
                        "error": f"job already running ({round(age_min)}min)，拒绝重复执行",
                    }
            except TypeError:  # naive/aware 混用时跳过守卫
                pass
        task_class = job.task_class
        task_params = job.task_params
        job.state = "RUNNING"
        # 每次执行刷新 start_time（2026-10-07）：此前 if None 才更新——孤儿恢复
        # 重投时保留历史 start_time → 重投后仍被孤儿恢复判定孤儿（>60min）→
        # 每 5 分钟循环重投 + 双实例。无条件刷新 = 重投重新计时，循环打破。
        job.start_time = dt.datetime.now(dt.timezone.utc)
        # 状态立即落库（2026-10-07：此前 RUNNING 从未 commit，任务页/监控
        # 一直显示 PENDING 直至收尾——状态漂移）
        db.commit()
        # 记录日志文件路径（jobs 页日志抽屉读取）
        job.log_path = _job_log_path(normalized)
        _append_job_log(normalized, f"任务开始 task_class={task_class} params={str(task_params)[:160]}")

    handler = TASK_CLASS_REGISTRY.get(task_class)
    if handler is None:
        with session_scope() as db:
            job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
            # 终态保护:用户已 STOPPED 的任务保持取消态,不覆盖成 FAILED
            if job and job.state != "STOPPED":
                job.state = "FAILED"
                job.error_message = f"no handler registered for {task_class}"
                job.end_time = dt.datetime.now(dt.timezone.utc)
                _update_duration(db, job)
        return {"success": False, "error": f"no handler registered for {task_class}"}

    try:
        result = handler(normalized, task_params)
    except Exception as e:  # noqa: BLE001
        logger.exception("execute_modo_job failed: job_id=%s", normalized)
        _append_job_log(normalized, f"任务失败: {e}")
        with session_scope() as db:
            job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
            # 终态保护:用户已 STOPPED 的任务保持取消态,不覆盖成 FAILED
            if job and job.state != "STOPPED":
                job.state = "FAILED"
                job.error_message = str(e)
                job.end_time = dt.datetime.now(dt.timezone.utc)
                _update_duration(db, job)
        return {"success": False, "error": str(e)}

    with session_scope() as db:
        job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
        # 终态保护:用户中途 STOPPED 的任务保持取消态,不得「复活」成 SUCCESS
        if job and job.state != "STOPPED":
            # 2026-10-05 实测: handler 业务失败(返回 success=False, 如文档不存在/
            # 格式不支持)不抛异常 → 任务被无条件标 SUCCESS(假成功, T1/T4 复现)。
            # 按显式 success 标志落终态: False → FAILED(记 error), 否则 SUCCESS。
            biz_failed = isinstance(result, dict) and result.get("success") is False
            if biz_failed:
                job.state = "FAILED"
                job.error_message = str(
                    result.get("error")
                    or result.get("parse_error")
                    or "handler returned success=False"
                )
                job.end_time = dt.datetime.now(dt.timezone.utc)
                _update_duration(db, job)
                _append_job_log(normalized, f"任务失败(业务): {job.error_message}")
            else:
                job.state = "SUCCESS"
                job.end_time = dt.datetime.now(dt.timezone.utc)
                _update_duration(db, job)
                summary = json.dumps(result, ensure_ascii=False)[:300]
                _append_job_log(
                    normalized,
                    f"任务成功 耗时={result.get('duration_ms') if isinstance(result, dict) else ''} 结果={summary}",
                )
        elif job:
            _append_job_log(normalized, "任务已被取消(STOPPED),结果丢弃")
    return {"success": True, "result": result}


# task_class -> callable(job_id, task_params) registered during migration.
TASK_CLASS_REGISTRY: dict = {}
