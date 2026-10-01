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
        task_class = job.task_class
        task_params = job.task_params
        job.state = "RUNNING"

    handler = TASK_CLASS_REGISTRY.get(task_class)
    if handler is None:
        with session_scope() as db:
            job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
            if job:
                job.state = "FAILED"
                job.error_message = f"no handler registered for {task_class}"
        return {"success": False, "error": f"no handler registered for {task_class}"}

    try:
        result = handler(normalized, task_params)
    except Exception as e:  # noqa: BLE001
        logger.exception("execute_modo_job failed: job_id=%s", normalized)
        with session_scope() as db:
            job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
            if job:
                job.state = "FAILED"
                job.error_message = str(e)
        return {"success": False, "error": str(e)}

    with session_scope() as db:
        job = db.execute(select(Job).where(Job.id == normalized)).scalars().first()
        if job:
            job.state = "SUCCESS"
    return {"success": True, "result": result}


# task_class -> callable(job_id, task_params) registered during migration.
TASK_CLASS_REGISTRY: dict = {}
