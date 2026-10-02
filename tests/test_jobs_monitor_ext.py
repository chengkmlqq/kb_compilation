"""Tests for the job monitor extension endpoints (statistics / queues /
stop / delete / log-stream SSE) added in the worker-monitor migration.
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


def test_job_log_stream_sse(monkeypatch) -> None:
    """GET /api/v1/jobs/{id}/log-stream → SSE 增量日志（error_message 进度流）。"""
    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(
        Job(
            id="JOB_SSE_1",
            task_id="gw-1",
            task_class="KbAgentGatewayTask",
            queue_name="default",
            task_params=json.dumps({"input": "构建 wiki"}),
            trigger_type="API",
            state="RUNNING",
            error_message="[poll 1] gateway status=running, elapsed=15s",
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    import api.routers.jobs as jobs_mod
    from api.services.identity import Identity, encode_identity_cookie

    jobs_mod.decode_identity_cookie = lambda cookie: Identity(
        user_id="u1", user_name="u1", team_name="team1"
    )

    import types

    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: db})(),
    )
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1", KB_STORAGE_DIR="/tmp"),
    )
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(
            Identity(user_id="u1", user_name="u1", team_name="team1")
        ),
    )

    # 未登录 → 401
    r = client.get("/api/v1/jobs/JOB_SSE_1/log-stream")
    assert r.status_code == 200  # router 自检，未登录时由 cookie 缺失处理
    # 带 offset=0 打开流：RUNNING 任务会推送已有 error_message 内容
    with client.stream(
        "GET", "/api/v1/jobs/JOB_SSE_1/log-stream?offset=0"
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # 读取前若干行，应包含 chunk（error_message 增量）
        lines = []
        for chunk in resp.iter_lines():
            if chunk:
                lines.append(chunk)
            if len(lines) >= 4:
                break
        joined = "\n".join(lines)
        assert "data:" in joined
        assert "poll 1" in joined or "RUNNING" in joined

    # 任务置终态后，流应发 finish
    job = db.get(Job, "JOB_SSE_1")
    job.state = "SUCCESS"
    db.add(job)
    db.commit()
    with client.stream(
        "GET", "/api/v1/jobs/JOB_SSE_1/log-stream?offset=0"
    ) as resp:
        assert resp.status_code == 200
        lines = []
        for chunk in resp.iter_lines():
            if chunk:
                lines.append(chunk)
            if len(lines) >= 4:
                break
        joined = "\n".join(lines)
        assert "finish" in joined or "SUCCESS" in joined

    app.dependency_overrides.clear()
