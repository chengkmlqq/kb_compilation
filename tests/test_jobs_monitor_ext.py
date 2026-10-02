"""Tests for the job monitor extension endpoints (statistics / queues /
stop / delete) added in the worker-monitor migration (alignment with
data-synth job-monitor layout).
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
from api.models.framework import Base, Job, JobQueue

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


def test_job_statistics_queues_stop_delete(monkeypatch) -> None:
    """statistics / queues / stop / delete 四个扩展端点。"""
    db = _make_session()
    db.query(Job).delete()
    db.query(JobQueue).delete()
    db.commit()

    now = datetime.now(timezone.utc)
    db.add(
        Job(
            id="JOB_S1",
            task_id="doc1",
            task_class="KbDocumentProcessTask",
            queue_name="default",
            task_params=json.dumps({"kbId": "kb1"}),
            trigger_type="API",
            state="SUCCESS",
            start_time=now - timedelta(seconds=5),
            end_time=now,
            duration_ms=5000,
        )
    )
    db.add(
        Job(
            id="JOB_S2",
            task_id="doc2",
            task_class="KbAgentGatewayTask",
            queue_name="default",
            task_params=json.dumps({"input": "构建 wiki"}),
            trigger_type="CRON",
            state="RUNNING",
            start_time=now - timedelta(seconds=60),
        )
    )
    db.add(
        Job(
            id="JOB_S3",
            task_id="doc3",
            task_class="KbDocumentEmbedTask",
            queue_name="embedding",
            task_params=json.dumps({"kbId": "kb1"}),
            trigger_type="API",
            state="FAILED",
        )
    )
    db.add(
        JobQueue(
            id="Q1",
            queue_name="default",
            queue_label="默认队列",
            queue_size=0,
            state="active",
        )
    )
    db.add(
        JobQueue(
            id="Q2",
            queue_name="embedding",
            queue_label="向量化队列",
            queue_size=0,
            state="active",
        )
    )
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        import api.routers.jobs as jobs_mod
        from api.services.identity import Identity, encode_identity_cookie

        real = jobs_mod.decode_identity_cookie

        def fake(cookie: str):
            if cookie == "auth-cookie-123":
                return Identity(user_id="u1", team_name="team1")
            return real(cookie)

        jobs_mod.decode_identity_cookie = fake

        import types

        monkeypatch.setattr(
            "api.middleware.get_sessionmaker",
            lambda: type("SM", (), {"__call__": lambda s: db})(),
        )
        monkeypatch.setattr(
            "api.middleware.get_settings",
            lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
        )
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(
                Identity(user_id="u1", user_name="u1", team_name="team1")
            ),
        )

        # ---- statistics ----
        r = client.get("/api/v1/jobs/statistics")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] == 3
        assert data["running"] == 1
        assert data["success"] == 1
        assert data["failed"] == 1
        assert data["stopped"] == 0
        assert data["queued"] == 0

        # ---- queues（从 modo_job 实际队列名去重）----
        r = client.get("/api/v1/jobs/queues")
        assert r.status_code == 200
        queues = r.json()["data"]
        names = [q["queueName"] for q in queues]
        assert "default" in names
        assert "embedding" in names

        # ---- list with queue filter ----
        r = client.get("/api/v1/jobs", params={"queue_name": "embedding"})
        assert r.json()["data"]["total"] == 1
        assert r.json()["data"]["items"][0]["id"] == "JOB_S3"

        # ---- stop a RUNNING job ----
        r = client.post("/api/v1/jobs/JOB_S2/stop")
        assert r.status_code == 200
        assert r.json()["data"]["state"] == "STOPPED"
        r = client.get("/api/v1/jobs/JOB_S2")
        assert r.json()["data"]["state"] == "STOPPED"

        # stop a non-running job → 400
        r = client.post("/api/v1/jobs/JOB_S1/stop")
        assert r.status_code == 400

        # stop a missing job → 404
        r = client.post("/api/v1/jobs/NOPE/stop")
        assert r.status_code == 404

        # ---- delete ----
        r = client.delete("/api/v1/jobs/JOB_S3")
        assert r.status_code == 200
        assert r.json()["data"]["deleted"] is True
        r = client.get("/api/v1/jobs/JOB_S3")
        assert r.status_code == 404

        # delete missing → 404
        r = client.delete("/api/v1/jobs/NOPE")
        assert r.status_code == 404

        # statistics reflect delete + stop
        r = client.get("/api/v1/jobs/statistics")
        data = r.json()["data"]
        assert data["total"] == 2
        assert data["stopped"] == 1
    finally:
        app.dependency_overrides.clear()
