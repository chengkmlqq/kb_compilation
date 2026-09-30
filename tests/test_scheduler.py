"""Cron scanner tests — initialization, recalibration, due-fire, optimistic lock.

Uses an in-memory SQLite session_scope monkeypatch so scan_cron_tasks can run
without a live DB. Enqueue is stubbed to avoid needing a real broker.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import CronTask, Job
import worker.tasks.scheduler as scheduler


@pytest.fixture()
def sched_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def fake_session_scope():
        db = Session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(scheduler, "session_scope", fake_session_scope)
    enqueued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        scheduler, "_enqueue_job", lambda job_id, queue: enqueued.append((job_id, queue)) or queue
    )
    yield Session, enqueued


def _add(Session, **kwargs) -> CronTask:
    db = Session()
    task = CronTask(**kwargs)
    db.add(task)
    db.commit()
    db.close()
    return task


def test_initializes_empty_next_fire_time(sched_db) -> None:
    Session, _ = sched_db
    _add(Session, id="c1", name="t1", cron_expression="*/5 * * * *", task_class="X", state="1")
    result = scheduler.scan_cron_tasks()
    assert result["initialized"] == 1
    assert result["triggered"] == 0
    db = Session()
    task = db.execute(select(CronTask).where(CronTask.id == "c1")).scalar_one()
    assert task.next_fire_time  # was written
    db.close()


def test_fires_due_task_and_creates_job(sched_db) -> None:
    Session, enqueued = sched_db
    # next_fire_time in the past → due
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _add(
        Session,
        id="c2",
        name="t2",
        cron_expression="*/5 * * * *",
        next_fire_time=past,
        task_class="MetadataCollectionTask",
        state="1",
        queue_name="high",
    )
    result = scheduler.scan_cron_tasks()
    assert result["triggered"] == 1
    assert result["createdJobs"] == 1
    db = Session()
    job = db.execute(select(Job)).scalars().first()
    assert job is not None
    assert job.trigger_type == "CRON"
    assert job.state == "PENDING"
    assert job.queue_name == "high"
    # next_fire_time advanced into the future
    task = db.execute(select(CronTask).where(CronTask.id == "c2")).scalar_one()
    assert task.next_fire_time > past
    db.close()
    assert len(enqueued) == 1


def test_skips_future_task_but_recalibrates_on_expr_change(sched_db) -> None:
    Session, _ = sched_db
    # Stored next fire far in future, but expression implies soon → recalibrate
    far = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _add(
        Session,
        id="c3",
        name="t3",
        cron_expression="*/5 * * * *",
        next_fire_time=far,
        task_class="X",
        state="1",
    )
    result = scheduler.scan_cron_tasks()
    assert result["triggered"] == 0
    db = Session()
    task = db.execute(select(CronTask).where(CronTask.id == "c3")).scalar_one()
    assert task.next_fire_time != far  # recalibrated
    db.close()


def test_ignores_disabled_task(sched_db) -> None:
    Session, _ = sched_db
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _add(
        Session,
        id="c4",
        name="t4",
        cron_expression="*/5 * * * *",
        next_fire_time=past,
        task_class="X",
        state="0",  # disabled
    )
    result = scheduler.scan_cron_tasks()
    assert result["triggered"] == 0
    assert result["createdJobs"] == 0


def test_second_scan_does_not_double_fire(sched_db) -> None:
    """After firing, next_fire_time is in the future → no second fire."""
    Session, enqueued = sched_db
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _add(
        Session,
        id="c5",
        name="t5",
        cron_expression="*/5 * * * *",
        next_fire_time=past,
        task_class="X",
        state="1",
    )
    first = scheduler.scan_cron_tasks()
    second = scheduler.scan_cron_tasks()
    assert first["triggered"] == 1
    assert second["triggered"] == 0
    assert len(enqueued) == 1


def test_next_fire_time_serialization_roundtrip() -> None:
    """Stored Z-format timestamps parse back to the same instant."""
    when = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    text = scheduler._to_next_fire_time_text(when)
    assert text == "2026-01-02T03:04:05Z"
    parsed = scheduler._parse_next_fire_time(text)
    assert parsed is not None
    assert parsed.astimezone(dt.timezone.utc) == when
