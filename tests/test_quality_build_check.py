"""构建任务完成检查 API 测试（2026-10-22）。

覆盖（复用 test_quality_dim_runs.py 的 in-memory fixture 模式）：
- 解析任务：job SUCCESS + 文档 FAILED → 假成功；文档 READY + 切片 → 真实完成
- ORCH job 假成功：job SUCCESS + run failed → 假成功（状态一致性）
- run 内部假成功：run success + 节点 failed → 假成功
- 构建 run 产物缺失：run success + 无产物键 + 无 wiki 页 → 假成功
- 构建 run 有产物键 → 真实完成
- 摘要 run 产物未落库 → 假成功；summary_status=READY → 真实完成
- 僵尸 run：running + updated_at 超 6h → 疑似卡死
- 独立 run（无承接 job）纳入列表；双表同名去重
- ORCH job 关联 run 不存在 → 无法判定
- detail 端点返回节点明细

注意：SQLite 测试库没有 _dev 表 / dev 表路由（install_dev_table_router 未挂），
prod/dev 两轮 env_scope 查到同一物理表——正好覆盖双表去重逻辑。
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.framework import Job
from api.models.knowledge import KbDocument, KbDatasource, WikiPage
from api.models.orchestration import TapeRun
from api.services.identity import Identity, encode_identity_cookie


def _settings_ns(admins: str):
    ns = types.SimpleNamespace()
    setattr(ns, "AUTH" + "_ADMIN_USERS", admins)
    return ns


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: db})(),
    )
    monkeypatch.setattr("api.middleware.get_settings", lambda: _settings_ns(""))
    monkeypatch.setattr("api.config.get_settings", lambda: _settings_ns(""))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    yield db, client
    app.dependency_overrides.pop(get_db, None)


def _add_kb(db, kb_id: str = "kb1", name: str = "测试库") -> None:
    db.add(KbDatasource(id=kb_id, name=name, state="1"))
    db.commit()


def _add_doc(db, doc_id: str, kb_id: str = "kb1", parse_state: str = "READY",
             chunk_count: int = 3, summary_status: str = "", summary: str = "") -> None:
    db.add(KbDocument(id=doc_id, kb_id=kb_id, file_name=f"{doc_id}.md",
                      parse_state=parse_state, chunk_count=chunk_count,
                      summary_status=summary_status, summary=summary))
    db.commit()


def _add_job(db, job_id: str, task_class: str, state: str, params: dict | None = None,
             kb_id: str = "", doc_id: str = "") -> Job:
    import json

    j = Job(id=job_id, task_id=job_id, task_class=task_class, queue_name="default",
            task_params=json.dumps(params or {}, ensure_ascii=False),
            trigger_type="API", state=state, kb_id=kb_id, doc_id=doc_id)
    db.add(j)
    db.commit()
    return j


def _add_run(db, run_id: str, tape_name: str, status: str = "success",
             inputs: dict | None = None, bindings: dict | None = None,
             steps: list | None = None, created_at: datetime | None = None,
             updated_at: datetime | None = None) -> TapeRun:
    r = TapeRun(id=run_id, tape_id="tape1", tape_name=tape_name, status=status,
                inputs=inputs or {}, bindings=bindings or {}, step_results=steps or [])
    if created_at:
        r.created_at = created_at
    if updated_at:
        r.updated_at = updated_at
    db.add(r)
    db.commit()
    return r


def _add_wiki_page(db, kb_id: str = "kb1", doc_id: str = "") -> None:
    db.add(WikiPage(id=f"p{len(db.query(WikiPage).all())}", kb_id=kb_id, slug=f"s-{doc_id or len(db.query(WikiPage).all())}",
                    title="页", source_refs=[doc_id] if doc_id else [], status="active"))
    db.commit()


def _list_items(client, **kw) -> list[dict]:
    r = client.get("/api/v1/quality/build-check/tasks", params=kw)
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


# ── 解析任务 ────────────────────────────────────────────────

def test_parse_job_fake_success_doc_failed(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", parse_state="FAILED")
    _add_job(db, "DOC_d1", "KbDocumentProcessTask", "SUCCESS",
             {"kbId": "kb1", "documentId": "d1"}, kb_id="kb1", doc_id="d1")
    items = _list_items(client)
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "doc_parse"
    assert it["result"] == "fake_success"
    assert any(c["key"] == "consistency" and c["ok"] is False for c in it["checks"])


def test_parse_job_ok_doc_ready(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", parse_state="READY", chunk_count=5)
    _add_job(db, "DOC_d1", "KbDocumentProcessTask", "SUCCESS",
             {"kbId": "kb1", "documentId": "d1"}, kb_id="kb1", doc_id="d1")
    it = _list_items(client)[0]
    assert it["result"] == "ok"


def test_parse_job_real_failed(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", parse_state="FAILED")
    _add_job(db, "DOC_d1", "KbDocumentProcessTask", "FAILED",
             {"kbId": "kb1", "documentId": "d1"}, kb_id="kb1", doc_id="d1")
    it = _list_items(client)[0]
    assert it["result"] == "failed"


# ── ORCH job ↔ run 状态一致性 ───────────────────────────────

def test_orch_job_success_run_failed_fake_success(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1")
    _add_run(db, "run1", "wiki构建-农业", status="failed",
             inputs={"kb_id": "kb1", "kid": "d1"})
    _add_job(db, "ORCH_RUN_x", "KbOrchestrationRunTask", "SUCCESS",
             {"runId": "run1", "env": "prod"}, kb_id="kb1", doc_id="d1")
    it = _list_items(client)[0]
    assert it["kind"] == "build"
    assert it["display_state"] == "SUCCESS"
    assert it["real_state"] == "failed"
    assert it["result"] == "fake_success"


def test_orch_job_failed_run_failed_real_failed(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="failed",
             inputs={"kb_id": "kb1", "kid": "d1"})
    _add_job(db, "ORCH_RUN_x", "KbOrchestrationRunTask", "FAILED",
             {"runId": "run1", "env": "prod"}, kb_id="kb1", doc_id="d1")
    it = _list_items(client)[0]
    assert it["result"] == "failed"


# ── run 内部节点假成功 ───────────────────────────────────────

def test_run_success_but_node_failed_fake_success(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={"kw_created": 3},
             steps=[{"step_id": "n1", "status": "success"},
                    {"step_id": "n2", "status": "failed", "error": "LLM 429"}])
    it = _list_items(client)[0]
    assert it["result"] == "fake_success"
    assert any(c["key"] == "internal" and c["ok"] is False for c in it["checks"])


def test_run_success_all_skipped_fake_success(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={},
             steps=[{"step_id": "n1", "status": "skipped"}])
    it = _list_items(client)[0]
    assert it["result"] == "fake_success"


# ── 构建产物验证 ─────────────────────────────────────────────

def test_build_run_ok_with_artifact_keys(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={"kw_created": 5, "rule_created": 2},
             steps=[{"step_id": "n1", "status": "success"}])
    it = _list_items(client)[0]
    assert it["result"] == "ok"


def test_build_run_missing_artifact_fake_success(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1")
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={},
             steps=[{"step_id": "n1", "status": "success"}])
    it = _list_items(client)[0]
    assert it["result"] == "fake_success"
    assert any(c["key"] == "artifact" and c["ok"] is False for c in it["checks"])


def test_build_run_wiki_page_fallback_ok(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={},
             steps=[{"step_id": "n1", "status": "success"}])
    _add_wiki_page(db, kb_id="kb1", doc_id="d1")
    it = _list_items(client)[0]
    assert it["result"] == "ok"


# ── 摘要产物验证 ─────────────────────────────────────────────

def test_summary_run_not_written_fake_success(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", summary_status="")
    _add_run(db, "run1", "摘要-Stuff（全文直塞）", status="success",
             inputs={"kb_id": "kb1", "document_id": "d1", "purpose": "summary"},
             bindings={},
             steps=[{"step_id": "n1", "status": "success"}])
    it = _list_items(client)[0]
    assert it["kind"] == "summary"
    assert it["result"] == "fake_success"


def test_summary_run_ok(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", summary_status="READY", summary="摘要内容")
    _add_run(db, "run1", "摘要-Stuff（全文直塞）", status="success",
             inputs={"kb_id": "kb1", "document_id": "d1", "purpose": "summary"},
             bindings={},
             steps=[{"step_id": "n1", "status": "success"}])
    it = _list_items(client)[0]
    assert it["result"] == "ok"


# ── 僵尸 / 独立 run / 双表去重 / unknown ────────────────────

def test_stale_run(env) -> None:
    db, client = env
    _add_kb(db)
    old = datetime.now(timezone.utc) - timedelta(hours=8)
    _add_run(db, "run1", "wiki构建-农业", status="running",
             inputs={"kb_id": "kb1", "kid": "d1"},
             steps=[], updated_at=old)
    it = _list_items(client)[0]
    assert it["result"] == "stale"
    assert any(c["key"] == "zombie" and c["ok"] is False for c in it["checks"])


def test_fresh_running_run(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="running",
             inputs={"kb_id": "kb1", "kid": "d1"}, steps=[])
    it = _list_items(client)[0]
    assert it["result"] == "running"


def test_independent_run_included(env) -> None:
    """无承接 job 的独立构建 run（手动执行）也纳入列表。"""
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={"biz_created": 1},
             steps=[{"step_id": "n1", "status": "success"}])
    it = _list_items(client)[0]
    assert it["task_class"] == "OrchestrationRun"
    assert it["result"] == "ok"


def test_non_build_run_excluded(env) -> None:
    """非构建链 run（质量评估等）不纳入。"""
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "质量评估-QA", status="success",
             inputs={"kb_id": "kb1"}, bindings={})
    assert _list_items(client) == []


def test_unknown_run_missing(env) -> None:
    """ORCH 承接 job 但 run 不存在 → 无法判定（不 404）。"""
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1")
    _add_job(db, "ORCH_RUN_x", "KbOrchestrationRunTask", "SUCCESS",
             {"runId": "gone_run", "env": "prod"}, kb_id="kb1", doc_id="d1")
    it = _list_items(client)[0]
    assert it["kind"] == "unknown"
    assert it["result"] == "unknown"


# ── 筛选 / overview / detail ────────────────────────────────

def test_filter_kind_and_result(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", parse_state="FAILED")
    _add_job(db, "DOC_d1", "KbDocumentProcessTask", "SUCCESS",
             {"kbId": "kb1", "documentId": "d1"}, kb_id="kb1", doc_id="d1")
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={"kw_created": 1},
             steps=[{"step_id": "n1", "status": "success"}])
    # kind 筛选
    items = _list_items(client, kind="build")
    assert len(items) == 1 and items[0]["kind"] == "build"
    # result 筛选
    items = _list_items(client, result="fake_success")
    assert len(items) == 1 and items[0]["id"] == "DOC_d1"


def test_overview_counts(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1", parse_state="FAILED")
    _add_job(db, "DOC_d1", "KbDocumentProcessTask", "SUCCESS",
             {"kbId": "kb1", "documentId": "d1"}, kb_id="kb1", doc_id="d1")
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={"kw_created": 1},
             steps=[{"step_id": "n1", "status": "success"}])
    r = client.get("/api/v1/quality/build-check/overview")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 2
    assert data["by_result"]["fake_success"] == 1
    assert data["by_result"]["ok"] == 1
    assert data["by_kind"]["doc_parse"] == 1
    assert data["by_kind"]["build"] == 1


def test_detail_with_steps(env) -> None:
    db, client = env
    _add_kb(db)
    _add_run(db, "run1", "wiki构建-农业", status="success",
             inputs={"kb_id": "kb1", "kid": "d1"},
             bindings={"kw_created": 2},
             steps=[{"step_id": "n1", "step_label": "输入定义", "status": "success", "duration_ms": 100},
                    {"step_id": "n2", "step_label": "建页", "status": "failed", "error": "err"}])
    r = client.get("/api/v1/quality/build-check/tasks/run1")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["run_id"] == "run1"
    assert len(data["steps"]) == 2
    assert data["steps"][1]["status"] == "failed"


def test_detail_job_id_unknown(env) -> None:
    db, client = env
    _add_kb(db)
    _add_doc(db, "d1")
    _add_job(db, "ORCH_RUN_x", "KbOrchestrationRunTask", "SUCCESS",
             {"runId": "gone", "env": "prod"}, kb_id="kb1", doc_id="d1")
    r = client.get("/api/v1/quality/build-check/tasks/ORCH_RUN_x")
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "unknown"