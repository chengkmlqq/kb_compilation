"""Tests for the task monitor API (api/routers/jobs.py) and the scheduler
timing fields (start_time / end_time / duration_ms written by
execute_modo_job).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, Job

# in-memory SQLite for the framework store — StaticPool so every session
# shares ONE connection (plain ":memory:" + default pool creates a fresh empty
# DB per connection, so tables created on one connection are invisible to the
# query connection).
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def _make_session() -> Session:
    Base.metadata.create_all(engine)
    db = Session()
    return db


def _auth_cookie() -> None:
    """Patch the jobs router's imported decode_identity_cookie so the
    _require_user_id dependency accepts a fake cookie value."""
    import api.routers.jobs as jobs_mod
    from api.services.identity import Identity

    real = jobs_mod.decode_identity_cookie

    def fake(cookie: str):
        if cookie == "auth-cookie-123":
            return Identity(user_id="u1", team_name="team1")
        return real(cookie)

    jobs_mod.decode_identity_cookie = fake


def test_scheduler_writes_duration_fields(monkeypatch) -> None:
    """execute_modo_job sets start_time/end_time/duration_ms on SUCCESS."""
    from worker.tasks import scheduler

    db = _make_session()
    db.add(
        Job(
            id="JOB_T",
            task_id="doc1",
            task_class="KbDocumentProcessTask",
            queue_name="default",
            task_params=json.dumps({"kbId": "kb1", "documentId": "doc1"}),
            trigger_type="API",
            state="PENDING",
        )
    )
    db.commit()

    # Replace the handler registry with a trivial success handler.
    def fake_handler(job_id: str, task_params: str | None) -> dict:
        return {"success": True, "result": {"pages": 1}}

    scheduler.TASK_CLASS_REGISTRY["KbDocumentProcessTask"] = fake_handler

    # Patch session_scope to use the in-memory session.
    import contextlib
    from typing import Iterator

    @contextlib.contextmanager
    def fake_scope() -> Iterator[Session]:
        yield db

    orig_scope = scheduler.session_scope
    scheduler.session_scope = fake_scope
    try:
        result = scheduler.execute_modo_job("JOB_T")
    finally:
        scheduler.session_scope = orig_scope
        del scheduler.TASK_CLASS_REGISTRY["KbDocumentProcessTask"]

    assert result["success"] is True
    job = db.execute(
        scheduler.select(Job).where(Job.id == "JOB_T")
    ).scalars().first()
    assert job.state == "SUCCESS"
    assert job.start_time is not None
    assert job.end_time is not None
    assert job.duration_ms is not None
    assert job.duration_ms >= 0


def test_jobs_list_and_detail() -> None:
    db = _make_session()
    # 清掉前一个测试（共享 StaticPool engine）残留的 JOB_T
    db.query(Job).delete()
    db.commit()
    now = datetime.now(timezone.utc)
    db.add(
        Job(
            id="JOB_1",
            task_id="doc1",
            task_class="KbDocumentProcessTask",
            queue_name="default",
            task_params=json.dumps({"kbId": "kb1", "documentId": "doc1"}),
            trigger_type="API",
            state="SUCCESS",
            start_time=now - timedelta(seconds=5),
            end_time=now,
            duration_ms=5000,
        )
    )
    db.add(
        Job(
            id="JOB_2",
            task_id="doc2",
            task_class="KbAgentGatewayTask",
            queue_name="default",
            task_params=json.dumps({"input": "构建 wiki"}),
            trigger_type="CRON",
            state="RUNNING",
            start_time=now - timedelta(seconds=60),
            error_message="[poll 3] gateway status=running, elapsed=45s",
        )
    )
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        _auth_cookie()
        client.cookies.set("x-next-identity", "auth-cookie-123")

        # list
        r = client.get("/api/v1/jobs")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 2
        by_id = {it["id"]: it for it in data["items"]}
        assert by_id["JOB_1"]["duration_ms"] == 5000
        assert by_id["JOB_1"]["state"] == "SUCCESS"
        assert by_id["JOB_2"]["params"] == {"input": "构建 wiki"}
        assert "[poll 3]" in by_id["JOB_2"]["error_message"]

        # filter by task_class
        r = client.get(
            "/api/v1/jobs", params={"task_class": "KbAgentGatewayTask"}
        )
        assert r.json()["data"]["total"] == 1

        # filter by state
        r = client.get("/api/v1/jobs", params={"state": "RUNNING"})
        assert r.json()["data"]["total"] == 1

        # detail
        r = client.get("/api/v1/jobs/JOB_2")
        assert r.status_code == 200
        assert r.json()["data"]["task_class"] == "KbAgentGatewayTask"
        assert r.json()["data"]["error_message"].startswith("[poll 3]")

        # 404
        r = client.get("/api/v1/jobs/NOPE")
        assert r.status_code == 404

        # unauthenticated (no cookie; swap in a decoder that always 401s)
        import api.routers.jobs as jobs_mod

        real_decoder = jobs_mod.decode_identity_cookie
        jobs_mod.decode_identity_cookie = lambda cookie: None
        client.cookies.clear()
        r = client.get("/api/v1/jobs")
        jobs_mod.decode_identity_cookie = real_decoder
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()
