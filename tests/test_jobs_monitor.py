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
    """Patch the jobs router's imported decode_identity_cookie AND provide a
    real, middleware-decodable identity cookie so the fail-closed RBAC guard
    (no identity on a controlled path → 401) lets the request through.

    The shared cookie is a real encode (same AES secret both sides use), so the
    middleware decodes it; the router-level patch only adds the fake-able
    variant for jobs that construct cookies differently.
    """
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


def test_jobs_list_and_detail(monkeypatch) -> None:
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
        # Real middleware-decodable identity cookie (fail-closed guard requires it)
        from api.services.identity import Identity, encode_identity_cookie

        # The fail-closed RBAC middleware reaches get_settings()/get_sessionmaker()
        # once a decodable identity is present — route it to the in-memory DB and
        # mark u1 as platform admin so the guard bypasses (no role/menu rows here).
        import types

        monkeypatch.setattr(
            "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
        )
        monkeypatch.setattr(
            "api.middleware.get_settings",
            lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
        )
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
        )

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


def _jobs_client(db, monkeypatch) -> TestClient:
    """复用 middleware 打点 + 身份 cookie 的 TestClient（u1 为平台 admin）。"""
    import types

    from api.services.identity import Identity, encode_identity_cookie

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
    )
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    _auth_cookie()
    return client


def test_stop_allows_pending_and_running(monkeypatch) -> None:
    """2026-10-05:stop 语义扩展为 PENDING/RUNNING 可停（排队中取消），终态仍 400。"""
    from api.routers import jobs as jobs_mod

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_P", task_id="t", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="PENDING"))
    db.add(Job(id="JOB_R", task_id="t2", task_class="KbDocumentProcessTask",
               queue_name="default", state="RUNNING"))
    db.add(Job(id="JOB_S", task_id="t3", task_class="KbDocumentProcessTask",
               queue_name="default", state="SUCCESS"))
    db.commit()
    try:
        client = _jobs_client(db, monkeypatch)
        r = client.post("/api/v1/jobs/JOB_P/stop")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["state"] == "STOPPED"
        r = client.post("/api/v1/jobs/JOB_R/stop")
        assert r.status_code == 200
        r = client.post("/api/v1/jobs/JOB_S/stop")
        assert r.status_code == 400  # 终态不可停
        r = client.post("/api/v1/jobs/NOPE/stop")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_retry_stopped_job(monkeypatch) -> None:
    """2026-10-05:新增 retry——STOPPED/FAILED 重置 PENDING 并重新入队（agent 队列）。"""
    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_ST", task_id="t", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="STOPPED", error_message="旧错误"))
    db.commit()

    sent: list[dict] = []

    def fake_send_task(name, args=None, task_id=None, queue=None, **kw):
        sent.append({"name": name, "args": args, "task_id": task_id, "queue": queue})

    import worker.celery_app as wca

    monkeypatch.setattr(wca.celery_app, "send_task", fake_send_task)
    try:
        client = _jobs_client(db, monkeypatch)
        r = client.post("/api/v1/jobs/JOB_ST/retry")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["state"] == "PENDING"
        assert data["queue"] == "agent"
        job = db.query(Job).filter(Job.id == "JOB_ST").first()
        assert job.state == "PENDING"
        assert job.error_message is None
        assert sent and sent[0]["name"] == "worker.tasks.scheduler.execute_modo_job"
        assert sent[0]["queue"] == "agent"
        assert sent[0]["task_id"] == "JOB_ST"
    finally:
        app.dependency_overrides.clear()


def test_retry_rejects_unretryable(monkeypatch) -> None:
    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_R", task_id="t", task_class="KbDocumentProcessTask",
               queue_name="default", state="RUNNING"))
    db.add(Job(id="JOB_OK", task_id="t2", task_class="KbDocumentProcessTask",
               queue_name="default", state="SUCCESS"))
    db.commit()
    try:
        client = _jobs_client(db, monkeypatch)
        r = client.post("/api/v1/jobs/JOB_R/retry")
        assert r.status_code == 400
        r = client.post("/api/v1/jobs/JOB_OK/retry")
        assert r.status_code == 400
        r = client.post("/api/v1/jobs/NOPE/retry")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_execute_modo_job_keeps_stopped(monkeypatch) -> None:
    """终态保护：执行中任务被 stop 后，handler 跑完不得覆盖成 SUCCESS（取消复活修复）。"""
    from worker.tasks import scheduler

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_C", task_id="t", task_class="KbDocumentProcessTask",
               queue_name="default", task_params="{}", state="PENDING"))
    db.commit()

    def fake_handler(job_id: str, task_params: str | None) -> dict:
        # 模拟 handler 执行中途用户 stop（改 DB 状态为 STOPPED）
        job = db.execute(
            scheduler.select(Job).where(Job.id == job_id)
        ).scalars().first()
        job.state = "STOPPED"
        db.commit()
        return {"success": True, "result": {"pages": 3}}

    scheduler.TASK_CLASS_REGISTRY["KbDocumentProcessTask"] = fake_handler
    import contextlib
    from typing import Iterator

    @contextlib.contextmanager
    def fake_scope() -> Iterator[Session]:
        yield db

    orig_scope = scheduler.session_scope
    scheduler.session_scope = fake_scope
    try:
        result = scheduler.execute_modo_job("JOB_C")
    finally:
        scheduler.session_scope = orig_scope
        del scheduler.TASK_CLASS_REGISTRY["KbDocumentProcessTask"]

    job = db.execute(
        scheduler.select(Job).where(Job.id == "JOB_C")
    ).scalars().first()
    assert result["success"] is True
    assert job.state == "STOPPED", f"取消复活: 期望 STOPPED 实际 {job.state}"
    assert job.error_message is None


def test_post_kb_scope_permission_403(monkeypatch) -> None:
    """普通用户建库归属=system 应 403 而非 500（2026-10-05 实测 500 修复）。"""
    from api.services import scope as scope_mod

    db = _make_session()
    import types

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
    )
    monkeypatch.setattr(scope_mod, "is_admin", lambda db, uid: False)  # u1 非管理员
    from api.services.identity import Identity, encode_identity_cookie

    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    import api.routers.kbs as kbs_mod

    monkeypatch.setattr(kbs_mod, "decode_identity_cookie", lambda c: Identity(user_id="u1"))
    try:
        r = client.post(
            "/api/v1/kbs",
            json={"name": "x", "scope": "system",
                  "indexing_strategy": {"wiki_enabled": True}},
        )
        assert r.status_code == 403, f"期望 403 实际 {r.status_code}: {r.text}"
        # 归属=personal 普通用户可建（validate 通过后 create_kb 因无表失败属测试环境，
        # 这里只验证不再因权限 403/500 卡住权限层——用 monkeypatch 掉 create_kb 后应 200）
        import api.routers.kbs as kbs2_mod

        monkeypatch.setattr(kbs2_mod, "create_kb", lambda _db, **kw: type("KB", (), {"id": "kb1", "name": "x", "scope": "personal"})())
        r = client.post(
            "/api/v1/kbs",
            json={"name": "x", "scope": "personal",
                  "indexing_strategy": {"wiki_enabled": True}},
        )
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
