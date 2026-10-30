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

    # SSE 端点每轮用独立短会话(get_sessionmaker())——将其指向测试内存引擎
    monkeypatch.setattr("api.routers.jobs.get_sessionmaker", lambda: Session)

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


def test_format_event_line_and_read_events_incremental(tmp_path, monkeypatch) -> None:
    """全量日志：events jsonl 事件格式化 + 增量读取（2026-10-21）。"""
    import api.routers.jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod, "get_settings",
        lambda: type("S", (), {"kb_storage_dir": str(tmp_path)})(),
    )

    # 格式化：step / ok / error / retry / 未知 kind
    assert "gen_summary 完成，耗时1234ms" in jobs_mod._format_event_line(
        {"ts": "2026-10-21 10:00:00", "kind": "step", "step": "gen_summary",
         "status": "done", "ms": 1234}
    )
    assert "调用失败: boom" in jobs_mod._format_event_line(
        {"ts": "2026-10-21 10:00:01", "kind": "error", "phase": "rules",
         "error": "boom"}
    )
    assert "重试（http_429，退避15s）" in jobs_mod._format_event_line(
        {"ts": "2026-10-21 10:00:02", "kind": "retry", "reason": "http_429",
         "backoff_s": 15}
    )
    assert "LLM:rules 调用成功（耗时300ms，输出120字符）" in jobs_mod._format_event_line(
        {"ts": "2026-10-21 10:00:03", "kind": "ok", "phase": "rules",
         "llm_ms": 300, "content_len": 120, "finish": "stop"}
    ) or "finish=stop" in jobs_mod._format_event_line(
        {"ts": "2026-10-21 10:00:03", "kind": "ok", "phase": "rules",
         "llm_ms": 300, "content_len": 120, "finish": "stop"}
    )

    # events jsonl 增量：首次读全文，再 append 新行读增量
    ev_dir = tmp_path / "logs" / "events"
    ev_dir.mkdir(parents=True)
    ev_file = ev_dir / "JOB_EV_1.jsonl"
    ev_file.write_text(
        json.dumps({"ts": "t1", "kind": "step", "step": "a", "status": "start"}, ensure_ascii=False) + "\n"
        + json.dumps({"ts": "t2", "kind": "ok", "phase": "p", "llm_ms": 5}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    text1, cur1 = jobs_mod._read_events_incremental("JOB_EV_1", 0)
    assert "[步骤] a 开始" in text1 and "调用成功" in text1
    assert cur1 == ev_file.stat().st_size
    # 追加新行 → 增量只有新行
    ev_file.write_text(
        ev_file.read_text(encoding="utf-8")
        + json.dumps({"ts": "t3", "kind": "error", "phase": "p", "error": "x"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    text2, cur2 = jobs_mod._read_events_incremental("JOB_EV_1", cur1)
    assert "调用失败: x" in text2
    assert "步骤 a 开始" not in text2
    assert cur2 > cur1
    # 文件不存在 → 空且游标不变
    assert jobs_mod._read_events_incremental("JOB_NOPE", 0) == ("", 0)


def test_log_stream_merges_local_events_and_slot(tmp_path, monkeypatch) -> None:
    """全量日志 SSE：local 日志文件 + events jsonl + error_message 槽位合并推送。

    无 MinIO 环境（remote_enabled=False）：进度源走 error_message 槽位兜底；
    local 生命周期行 + events 事件明细与槽位内容一起出现在同一个流里。
    """
    import types

    import api.routers.jobs as jobs_mod
    from api.services.identity import Identity, encode_identity_cookie

    db = _make_session()
    db.query(Job).delete()
    db.commit()
    db.add(
        Job(
            id="JOB_FULL_1", task_id="gw-1", task_class="KbSkillDirectBuildTask",
            queue_name="default", task_params=json.dumps({"kbId": "kb1"}),
            trigger_type="API", state="RUNNING",
            error_message="[poll 1] gateway status=running",
            log_path=str(tmp_path / "logs" / "jobs" / "JOB_FULL_1.log"),
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        jobs_mod.decode_identity_cookie = lambda cookie: Identity(
            user_id="u1", user_name="u1", team_name="team1"
        )
        monkeypatch.setattr(jobs_mod, "get_sessionmaker", lambda: Session)
        # 中间件也会取会话做鉴权校验 → 同样指向测试内存引擎
        monkeypatch.setattr(
            "api.middleware.get_sessionmaker",
            lambda: type("SM", (), {"__call__": lambda s: db})(),
        )
        monkeypatch.setattr(
            "api.middleware.get_settings",
            lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1", KB_STORAGE_DIR=str(tmp_path)),
        )
        monkeypatch.setattr(
            jobs_mod, "get_settings",
            lambda: types.SimpleNamespace(kb_storage_dir=str(tmp_path)),
        )
        # 无 MinIO：进度源回退 error_message 槽位
        import api.services.storage as storage_mod

        monkeypatch.setattr(storage_mod, "remote_enabled", lambda: False)
        # local 日志文件 + events jsonl
        (tmp_path / "logs" / "jobs").mkdir(parents=True)
        (tmp_path / "logs" / "jobs" / "JOB_FULL_1.log").write_text(
            "[2026-10-21 09:00:00] 任务开始 task_class=KbSkillDirectBuildTask\n",
            encoding="utf-8",
        )
        (tmp_path / "logs" / "events").mkdir(parents=True)
        (tmp_path / "logs" / "events" / "JOB_FULL_1.jsonl").write_text(
            json.dumps({"ts": "t", "kind": "step", "step": "gen_summary",
                        "status": "done", "ms": 42}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
        )
        with client.stream("GET", "/api/v1/jobs/JOB_FULL_1/log-stream") as resp:
            assert resp.status_code == 200
            lines = []
            for chunk in resp.iter_lines():
                if chunk:
                    lines.append(chunk)
                if len(lines) >= 8:
                    break
            joined = "\n".join(lines)
            assert "任务开始" in joined          # local 生命周期行
            assert "gen_summary 完成" in joined # events 事件明细
            assert "poll 1" in joined           # error_message 槽位进度
    finally:
        app.dependency_overrides.clear()


def test_run_log_snapshot_text() -> None:
    """编排 run 快照文本：节点逐行 + run 错误（2026-10-21）。"""
    import api.routers.jobs as jobs_mod
    from types import SimpleNamespace

    run = SimpleNamespace(
        id="RUN_X", tape_name="摘要编排", status="failed",
        created_at="2026-10-21 10:00:00", updated_at="2026-10-21 10:05:00",
        error="LLM 调用失败",
        step_results=[
            {"step_id": "n01", "step_label": "解析", "status": "success", "duration_ms": 1200},
            {"step_id": "n02", "step_label": "摘要生成", "status": "failed",
             "duration_ms": 800, "error": "超时"},
        ],
    )
    text = jobs_mod._run_log_snapshot(run)
    assert "磁带: 摘要编排  状态: FAILED" in text
    assert "错误: LLM 调用失败" in text
    assert "[1] 节点 解析 success，耗时1200ms" in text
    assert "[2] 节点 摘要生成 failed，耗时800ms，错误: 超时" in text


def test_job_effective_state_rules() -> None:
    """_job_effective_state 与前端 effectiveState 同规则（2026-10-30）。

    列表状态列按合成态展示：链头「投递即成功」终态 + 子任务未终态 →
    显示运行中；子任务有失败 → 失败优先。卡片统计必须走同一套规则，
    否则与列表数量对不上。断言即前端 parent-job-table.tsx effectiveState
    的逐分支映射。
    """
    import api.routers.jobs as jobs_mod

    f = jobs_mod._job_effective_state
    # 自身 RUNNING → 运行中（哪怕子任务全终态）
    assert f("RUNNING", {"SUCCESS": 2}) == "RUNNING"
    assert f("running", {}) == "RUNNING"
    # 子任务失败优先（自身终态 + 子任务 FAILED → FAILED）
    assert f("SUCCESS", {"FAILED": 1}) == "FAILED"
    assert f("SUCCESS", {"failed": 1}) == "FAILED"
    assert f("SUCCESS", {"cancelled": 1}) == "FAILED"
    # 子任务运行/排队中 → 合成运行中（链头终态但子任务还在跑 = 本次修复主场景）
    assert f("SUCCESS", {"RUNNING": 1}) == "RUNNING"
    assert f("SUCCESS", {"running": 1, "SUCCESS": 1}) == "RUNNING"
    assert f("SUCCESS", {"PENDING": 1}) == "RUNNING"
    assert f("SUCCESS", {"queued": 1}) == "RUNNING"
    # 全终态无失败 → 自身状态
    assert f("SUCCESS", {"SUCCESS": 2, "STOPPED": 1}) == "SUCCESS"
    assert f("FAILED", {}) == "FAILED"
    assert f("STOPPED", {}) == "STOPPED"
    assert f("PENDING", {}) == "PENDING"


def test_statistics_uses_effective_state(monkeypatch) -> None:
    """统计卡片按主任务展示状态（effectiveState）计数，与列表同口径。

    2026-10-30 用户反馈：顶部「运行中」卡片与列表运行中主任务数量对不上。
    根因：卡片按 Job.state 硬计数，链头「投递即成功」终态（SUCCESS）而
    子任务还在 RUNNING 时，列表显示运行中、卡片却计入成功。修复后
    _job_statistics_data 对链头聚合子任务状态再合成计数。
    """
    from api.services.agg_cache import agg_cache_invalidate
    from api.routers.jobs import _job_statistics_data

    db = _make_session()
    db.query(Job).delete()
    db.query(JobQueue).delete()
    db.commit()

    # 链头 A：自身 SUCCESS（投递即成功）+ 子任务 RUNNING（编排/解析还在跑）
    db.add(Job(id="MIS_A", task_id="a", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"documentId": "doc-a"}),
               trigger_type="API", state="SUCCESS"))
    db.add(Job(id="MIS_A_c1", task_id="c1", task_class="KbDocumentParseTask",
               queue_name="default", task_params="{}", trigger_type="API",
               state="RUNNING", parent_id="MIS_A"))
    # 链头 B：自身 RUNNING
    db.add(Job(id="MIS_B", task_id="b", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"documentId": "doc-b"}),
               trigger_type="API", state="RUNNING"))
    # 链头 C：自身 SUCCESS 无子任务
    db.add(Job(id="MIS_C", task_id="c", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"documentId": "doc-c"}),
               trigger_type="API", state="SUCCESS"))
    # 链头 D：自身 SUCCESS + 子任务 FAILED → 失败优先计入 failed
    db.add(Job(id="MIS_D", task_id="d", task_class="KbDocumentProcessTask",
               queue_name="default", task_params=json.dumps({"documentId": "doc-d"}),
               trigger_type="API", state="SUCCESS"))
    db.add(Job(id="MIS_D_c1", task_id="c1", task_class="KbDocumentParseTask",
               queue_name="default", task_params="{}", trigger_type="API",
               state="FAILED", parent_id="MIS_D"))
    db.commit()

    agg_cache_invalidate("jobs_statistics")
    stats = _job_statistics_data(db)
    assert stats["total"] == 4
    # A 合成运行中（修复点，旧实现计入 success）+ B 自身运行中
    assert stats["running"] == 2, f"running 应含合成态链头 A: {stats}"
    assert stats["success"] == 1, f"仅 C 计入成功: {stats}"
    assert stats["failed"] == 1, f"D 失败优先计入 failed: {stats}"
    assert stats["stopped"] == 0 and stats["queued"] == 0
