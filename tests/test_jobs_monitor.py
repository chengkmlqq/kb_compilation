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


def test_delete_job_revokes_running(monkeypatch) -> None:
    """2026-10-05:删除运行中/排队中任务先 revoke celery（避免孤儿页），终态直接删。"""
    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_R", task_id="t", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="RUNNING"))
    db.add(Job(id="JOB_OK", task_id="t2", task_class="KbDocumentProcessTask",
               queue_name="default", state="SUCCESS"))
    db.commit()

    revoked: list[str] = []

    class FakeControl:
        def revoke(self, task_id=None, terminate=None, **kw):
            revoked.append(task_id)

    import worker.celery_app as wca

    monkeypatch.setattr(wca.celery_app, "control", FakeControl())
    try:
        client = _jobs_client(db, monkeypatch)
        r = client.delete("/api/v1/jobs/JOB_R")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["deleted"] is True
        assert revoked == ["JOB_R"], f"期望 revoke 被调, 实际 {revoked}"
        assert db.query(Job).filter(Job.id == "JOB_R").first() is None
        # 终态任务不 revoke
        r = client.delete("/api/v1/jobs/JOB_OK")
        assert r.status_code == 200
        assert revoked == ["JOB_R"]
        r = client.delete("/api/v1/jobs/NOPE")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_delete_document_cancels_related_wiki_tasks(monkeypatch) -> None:
    """2026-10-05:删文档级联取消其 PENDING/RUNNING wiki 任务（假成功修复）。"""
    from api.services import kb_admin

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="WIKI_A", task_id="t", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="RUNNING",
               task_params=json.dumps({"kbId": "kb1", "documentId": "docX"})))
    db.add(Job(id="WIKI_B", task_id="t2", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="PENDING",
               task_params=json.dumps({"kbId": "kb1", "documentId": "docX"})))
    db.add(Job(id="WIKI_OK", task_id="t3", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="SUCCESS",
               task_params=json.dumps({"kbId": "kb1", "documentId": "docX"})))
    db.add(Job(id="WIKI_OTHER", task_id="t4", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="RUNNING",
               task_params=json.dumps({"kbId": "kb1", "documentId": "docY"})))
    db.commit()

    revoked: list[str] = []

    class FakeControl:
        def revoke(self, task_id=None, terminate=None, **kw):
            revoked.append(task_id)

    import worker.celery_app as wca

    monkeypatch.setattr(wca.celery_app, "control", FakeControl())
    try:
        kb_admin._cancel_related_wiki_tasks(db, "docX")
    finally:
        app.dependency_overrides.clear()

    by_id = {j.id: j.state for j in db.query(Job).all()}
    assert by_id["WIKI_A"] == "STOPPED"
    assert by_id["WIKI_B"] == "STOPPED"
    assert by_id["WIKI_OK"] == "SUCCESS"  # 终态不动
    assert by_id["WIKI_OTHER"] == "RUNNING"  # 无关文档不动
    assert sorted(revoked) == ["WIKI_A", "WIKI_B"]


def test_delete_kb_cancels_kb_tasks(monkeypatch) -> None:
    """2026-10-05: 删库级联取消其 DOC/WIKI 未完成任务（删库后任务行残留修复）。"""
    from api.services import kb_admin

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="DOC_K1", task_id="t", task_class="KbDocumentProcessTask",
               queue_name="default", state="PENDING",
               task_params=json.dumps({"kbId": "kb1", "documentId": "d1"})))
    db.add(Job(id="WIKI_K1", task_id="t2", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="RUNNING",
               task_params=json.dumps({"kbId": "kb1", "documentId": "d1"})))
    db.add(Job(id="WIKI_OK", task_id="t3", task_class="KbAgentWikiBuildTask",
               queue_name="agent", state="SUCCESS",
               task_params=json.dumps({"kbId": "kb1", "documentId": "d1"})))
    db.add(Job(id="DOC_K2", task_id="t4", task_class="KbDocumentProcessTask",
               queue_name="default", state="PENDING",
               task_params=json.dumps({"kbId": "kb2", "documentId": "d2"})))
    db.commit()

    revoked: list[str] = []

    class FakeControl:
        def revoke(self, task_id=None, terminate=None, **kw):
            revoked.append(task_id)

    import worker.celery_app as wca

    monkeypatch.setattr(wca.celery_app, "control", FakeControl())
    try:
        kb_admin._cancel_kb_tasks(db, "kb1")
    finally:
        app.dependency_overrides.clear()

    by_id = {j.id: j.state for j in db.query(Job).all()}
    assert by_id["DOC_K1"] == "STOPPED"
    assert by_id["WIKI_K1"] == "STOPPED"
    assert by_id["WIKI_OK"] == "SUCCESS"  # 终态不动
    assert by_id["DOC_K2"] == "PENDING"  # 其他库不动
    assert sorted(revoked) == ["DOC_K1", "WIKI_K1"]


def test_execute_modo_job_respects_business_failure(monkeypatch) -> None:
    """2026-10-05: handler 返回 success=False(业务失败: 文档不存在/格式不支持)
    不抛异常 → 任务必须 FAILED 而非无条件 SUCCESS(假成功 T1/T4 修复)。"""
    import contextlib
    from typing import Iterator

    from worker.tasks import scheduler

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_F", task_id="t", task_class="FakeFailHandler",
               queue_name="default", state="PENDING"))
    db.commit()

    def fake_handler(job_id, params):
        return {"success": False, "error": "document not found"}

    scheduler.TASK_CLASS_REGISTRY["FakeFailHandler"] = fake_handler

    @contextlib.contextmanager
    def fake_scope() -> Iterator[Session]:
        yield db

    orig_scope = scheduler.session_scope
    scheduler.session_scope = fake_scope
    try:
        r = scheduler.execute_modo_job("JOB_F")
    finally:
        scheduler.session_scope = orig_scope
        del scheduler.TASK_CLASS_REGISTRY["FakeFailHandler"]

    job = db.execute(scheduler.select(Job).where(Job.id == "JOB_F")).scalars().first()
    assert job.state == "FAILED", f"假成功: 期望 FAILED 实际 {job.state}"
    assert "document not found" in (job.error_message or "")


def test_execute_modo_job_success_key_not_forced(monkeypatch) -> None:
    """handler 返回 success=True / 无 success 键 → SUCCESS(显式 False 才判失败, 不误伤)。"""
    import contextlib
    from typing import Iterator

    from worker.tasks import scheduler

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(Job(id="JOB_OK2", task_id="t", task_class="FakeOkHandler",
               queue_name="default", state="PENDING"))
    db.commit()

    def fake_ok(job_id, params):
        return {"success": True, "pages": 7}

    scheduler.TASK_CLASS_REGISTRY["FakeOkHandler"] = fake_ok

    @contextlib.contextmanager
    def fake_scope() -> Iterator[Session]:
        yield db

    orig_scope = scheduler.session_scope
    scheduler.session_scope = fake_scope
    try:
        r = scheduler.execute_modo_job("JOB_OK2")
    finally:
        scheduler.session_scope = orig_scope
        del scheduler.TASK_CLASS_REGISTRY["FakeOkHandler"]

    job = db.execute(scheduler.select(Job).where(Job.id == "JOB_OK2")).scalars().first()
    assert job.state == "SUCCESS", f"误伤: 期望 SUCCESS 实际 {job.state}"


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


def test_parent_child_jobs_view(monkeypatch) -> None:
    """父子任务视图（2026-10-12）：/parents 只列父任务+子任务聚合，/children 列出子任务。"""
    db = _make_session()
    db.query(Job).delete()
    db.commit()

    # 父任务（链头解析）
    db.add(Job(id="DOC_doc1", task_id="doc1", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"kbId": "kb1", "documentId": "doc1"}),
               trigger_type="API", state="SUCCESS"))
    # 子任务（wiki/graph 挂 parent_id）
    db.add(Job(id="WIKI_X", task_id="WIKI_X", task_class="KbSkillWikiBuildTask",
               queue_name="default", task_params=json.dumps({"kbId": "kb1", "documentId": "doc1"}),
               trigger_type="API", state="RUNNING", parent_id="DOC_doc1"))
    db.add(Job(id="GRAPH_X", task_id="GRAPH_X", task_class="KbGraphBuildTask",
               queue_name="graph", task_params=json.dumps({"kbId": "kb1", "documentId": "doc1"}),
               trigger_type="API", state="PENDING", parent_id="DOC_doc1"))
    # 独立父任务（无子任务）
    db.add(Job(id="DOC_doc2", task_id="doc2", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"kbId": "kb2", "documentId": "doc2"}),
               trigger_type="API", state="SUCCESS"))
    db.commit()

    # 编排 run（虚拟父任务）
    from api.models.orchestration import TapeRun

    db.add(TapeRun(id="run1", tape_id="tape1", tape_name="编排A", status="running",
                   inputs={"kb_id": "kb1"}, step_results=[
                       {"step_id": "n1", "step_inst": "kb_read_doc", "status": "success"},
                       {"step_id": "n2", "step_inst": "kb_llm_chat", "status": "running"},
                   ]))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    client = _jobs_client(db, monkeypatch)
    try:
        # /parents：3 个 modo_job 父任务 + 1 个编排 run
        r = client.get("/api/v1/jobs/parents")
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        by_id = {it["id"]: it for it in items}
        # DOC_doc1 是父任务（有 2 个子任务）
        assert "DOC_doc1" in by_id
        assert by_id["DOC_doc1"]["child_count"] == 2
        assert by_id["DOC_doc1"]["child_states"].get("RUNNING") == 1
        # 编排 run 是虚拟父任务
        assert "run1" in by_id
        assert by_id["run1"]["task_class"] == "OrchestrationRun"
        assert by_id["run1"]["child_count"] == 2

        # /children：modo_job 父任务 → 2 个子任务
        r = client.get("/api/v1/jobs/DOC_doc1/children")
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["kind"] == "job" and d["total"] == 2

        # /children：编排 run → step_results 节点
        r = client.get("/api/v1/jobs/run1/children")
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["kind"] == "orchestration" and d["total"] == 2

        # 子任务不带 parent_id 的父任务：child_count=0
        assert by_id["DOC_doc2"]["child_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_parents_run_state_filter_mapping_and_mixed_paging(monkeypatch) -> None:
    """编排 run 与任务监控枚举对齐（2026-10-13）：

    - 状态筛选：前端传大写（RUNNING/SUCCESS...），TapeRun.status 是小写
      （running/success...）——必须映射，否则「选状态就看不到编排」。
    - run 与 modo_job 同页混排（时间倒序），翻页后 run 不再消失。
    - 序列化出的 state 统一为大写枚举（前端两表共用一套 STATE_LABEL）。
    """
    from api.models.orchestration import TapeRun

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.query(TapeRun).delete()
    db.commit()

    t0 = datetime(2026, 10, 13, 1, 0, 0, tzinfo=timezone.utc)
    # 2 个 job 父任务 + 2 个编排 run（一 success 一 failed），时间错开验证混排
    db.add(Job(id="JOB_old", task_id="j1", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"kbId": "kb1"}),
               trigger_type="API", state="SUCCESS", create_time=t0))
    db.add(TapeRun(id="run_succ", tape_id="tape1", tape_name="编排A", status="success",
                   created_at=t0 + timedelta(minutes=1), updated_at=t0 + timedelta(minutes=2),
                   step_results=[]))
    db.add(Job(id="JOB_running", task_id="j2", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"kbId": "kb2"}),
               trigger_type="API", state="RUNNING", create_time=t0 + timedelta(minutes=3)))
    db.add(TapeRun(id="run_fail", tape_id="tape2", tape_name="编排B", status="failed",
                   created_at=t0 + timedelta(minutes=4), updated_at=t0 + timedelta(minutes=5),
                   step_results=[]))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    client = _jobs_client(db, monkeypatch)
    try:
        # ── 状态筛选：RUNNING（大写）应同时命中 job 大写 state 与 run 小写 status ──
        r = client.get("/api/v1/jobs/parents", params={"state": "RUNNING"})
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        ids = {it["id"] for it in items}
        assert "JOB_running" in ids, f"job RUNNING 应命中: {ids}"
        assert "run_succ" not in ids and "run_fail" not in ids  # 非 running 的 run 不命中

        # ── 同页混排：时间倒序（run_fail 最新 → 排最前）──
        r = client.get("/api/v1/jobs/parents")
        assert r.status_code == 200
        order = [it["id"] for it in r.json()["data"]["items"]]
        assert order[0] == "run_fail", f"最新 run 应排最前: {order}"

        # 再建一条 running 的 run 验证映射反向也成立（此时它最新，排在首位）
        db.add(TapeRun(id="run_run", tape_id="tape3", tape_name="编排C", status="running",
                       created_at=t0 + timedelta(minutes=6), updated_at=t0 + timedelta(minutes=7),
                       step_results=[]))
        db.commit()
        r = client.get("/api/v1/jobs/parents", params={"state": "RUNNING"})
        items = r.json()["data"]["items"]
        ids = {it["id"] for it in items}
        assert "run_run" in ids, f"RUNNING 筛选应命中 running run: {ids}"

        # ── 序列化 state 大写化：run 输出 RUNNING/SUCCESS/FAILED ──
        r = client.get("/api/v1/jobs/parents")
        assert r.status_code == 200
        by_id = {it["id"]: it for it in r.json()["data"]["items"]}
        assert by_id["run_run"]["state"] == "RUNNING"
        assert by_id["run_succ"]["state"] == "SUCCESS"
        assert by_id["run_fail"]["state"] == "FAILED"
        assert by_id["JOB_running"]["state"] == "RUNNING"

        # ── 全部任务视图（list_jobs）也含编排 run ──
        r = client.get("/api/v1/jobs")
        assert r.status_code == 200
        all_ids = {it["id"] for it in r.json()["data"]["items"]}
        assert "run_run" in all_ids and "run_fail" in all_ids, f"list_jobs 应含 run: {all_ids}"
        # task_class 显式筛选非 OrchestrationRun 时 run 被排除
        r = client.get("/api/v1/jobs", params={"task_class": "KbDocumentProcessTask"})
        all_ids = {it["id"] for it in r.json()["data"]["items"]}
        assert "run_run" not in all_ids

        # ── statistics 并入 run 状态 ──
        r = client.get("/api/v1/jobs/statistics")
        assert r.status_code == 200
        stats = r.json()["data"]
        assert stats["running"] == 2  # JOB_running + run_run
        assert stats["success"] >= 1 and stats["failed"] >= 1

        # ── get_job / stop_job 支持编排 run ──
        r = client.get(f"/api/v1/jobs/{'run_run'}")
        assert r.status_code == 200 and r.json()["data"]["task_class"] == "OrchestrationRun"
        r = client.post(f"/api/v1/jobs/{'run_run'}/stop")
        assert r.status_code == 200 and r.json()["data"]["state"] == "STOPPED"
        from sqlalchemy import select

        run = db.execute(select(TapeRun).where(TapeRun.id == "run_run")).scalars().first()
        assert run.status == "cancelled"
        # 已终态 run 再 stop → 400
        r = client.post(f"/api/v1/jobs/{'run_run'}/stop")
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
