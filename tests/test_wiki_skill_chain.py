"""doc_process 自动触发 wiki 构建（worker 内联 agent 链路）测试。

上传文档 → KbDocumentProcessTask 成功 → 自动 enqueue KbAgentWikiBuildTask
（worker 内联 agent 运行时跑 kb-wiki-builder 技能，投 agent 队列；
WIKI_AGENT_MODE=gateway 可回退外部 agent-gateway 的 KbAgentGatewayTask）。
（agent 运行时内跑 kb-wiki-builder 技能脚本读 chunks → LLM → 写 wiki 页）。
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Job
import worker.tasks.doc_process as dp


@pytest.fixture()
def chain_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)

    sent: dict = {}

    class FakeCelery:
        def send_task(self, name, args, task_id, queue):
            sent["name"] = name
            sent["task_id"] = task_id
            sent["queue"] = queue

    import worker.celery_app as celery_mod

    monkeypatch.setattr(celery_mod, "celery_app", FakeCelery())
    yield Session, sent


def test_enqueue_wiki_skill_task_writes_job(chain_env) -> None:
    Session, sent = chain_env
    ok = dp._enqueue_wiki_skill_task("kb-1", "doc-1")
    assert ok is True
    assert sent["name"] == "worker.tasks.scheduler.execute_modo_job"
    # 默认内联 agent：投 agent 队列（celery-agent-worker 消费）
    assert sent["queue"] == "agent"
    # job_id 前缀 WIKI_SKILL_
    assert sent["task_id"].startswith("WIKI_SKILL_")
    # modo_job 行已写入（任务监控可见）
    db = Session()
    jobs = db.execute(select(Job)).scalars().all()
    assert len(jobs) == 1
    job = jobs[0]
    # 2026-10-07: WIKI_AGENT_MODE 默认 direct——技能直跑替代 agent 编排
    assert job.task_class == "KbSkillDirectBuildTask"
    assert job.queue_name == "agent"
    assert job.state == "PENDING"
    params = json.loads(job.task_params)
    assert params["config"]["skill"] == "kb-wiki-builder"
    assert params["config"]["kb_id"] == "kb-1"
    assert params["config"]["doc_name"] == "doc-1"
    db.close()


def test_enqueue_wiki_skill_task_broker_failure_returns_false(chain_env, monkeypatch) -> None:
    Session, sent = chain_env

    def boom(*a, **k):
        raise RuntimeError("broker down")

    import worker.celery_app as celery_mod

    monkeypatch.setattr(celery_mod.celery_app, "send_task", boom)
    ok = dp._enqueue_wiki_skill_task("kb-1", "doc-1")
    assert ok is False


def test_enqueue_wiki_skill_task_gateway_mode_fallback(chain_env, monkeypatch) -> None:
    """WIKI_AGENT_MODE=gateway → 回退外部 agent-gateway（KbAgentGatewayTask + default 队列）。"""
    Session, sent = chain_env
    monkeypatch.setenv("WIKI_AGENT_MODE", "gateway")
    ok = dp._enqueue_wiki_skill_task("kb-1", "doc-1")
    assert ok is True
    assert sent["queue"] == "default"
    db = Session()
    job = db.execute(select(Job)).scalars().first()
    assert job.task_class == "KbAgentGatewayTask"
    assert job.queue_name == "default"
    db.close()


def test_enqueue_wiki_skill_task_commits_before_send(chain_env, monkeypatch) -> None:
    """回归：Job 行必须在 send_task 之前已提交可见。

    worker 空闲时会在消息进 broker 的瞬间就消费 execute_modo_job；若此时
    Job 行还没 commit，执行侧报 'job not found' 任务直接失败。
    """
    Session, _ = chain_env
    visible_at_send: dict = {}

    import worker.celery_app as celery_mod

    def checking_send(name, args, task_id, queue):
        probe = Session()
        try:
            job = probe.execute(select(Job).where(Job.id == task_id)).scalars().first()
            visible_at_send["found"] = job is not None
        finally:
            probe.close()

    monkeypatch.setattr(celery_mod.celery_app, "send_task", checking_send)
    ok = dp._enqueue_wiki_skill_task("kb-1", "doc-1")
    assert ok is True
    assert visible_at_send.get("found") is True


def test_handle_process_document_chain_triggered_on_success(chain_env, monkeypatch) -> None:
    """process_document 成功（parse_state=READY）+ kb_id 存在 → wiki_skill_triggered=True。"""
    Session, sent = chain_env
    monkeypatch.setattr(
        dp, "process_document",
        lambda doc_id, content, parser_engine=None: {"success": True, "parse_state": "READY", "document_id": doc_id},
    )
    result = dp._handle_process_document("job-1", json.dumps({"documentId": "doc-1", "kbId": "kb-1"}))
    assert result["success"] is True
    assert result["wiki_skill_triggered"] is True


def test_handle_process_document_no_chain_on_failure(chain_env, monkeypatch) -> None:
    """process_document 失败（parse_state=FAILED）→ 不触发。"""
    Session, sent = chain_env
    monkeypatch.setattr(
        dp, "process_document",
        lambda doc_id, content, parser_engine=None: {"success": False, "parse_state": "FAILED", "error": "parse failed"},
    )
    result = dp._handle_process_document("job-1", json.dumps({"documentId": "doc-1", "kbId": "kb-1"}))
    assert result["success"] is False
    assert result.get("wiki_skill_triggered") is None
    db = Session()
    assert len(db.execute(select(Job)).scalars().all()) == 0
    db.close()


def test_handle_process_document_graph_triggered_when_enabled(chain_env, monkeypatch) -> None:
    """graph_enabled=true → 额外 enqueue KbGraphBuildTask。"""
    Session, sent = chain_env
    # 种子 KB：graph 开、wiki 关
    from api.models.knowledge import KbDatasource

    db = Session()
    db.add(
        KbDatasource(
            id="kb-1",
            name="kb",
            indexing_strategy={"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": True},
            state="1",
        )
    )
    db.commit()
    db.close()
    monkeypatch.setattr(
        dp, "process_document",
        lambda doc_id, content, parser_engine=None: {"success": True, "parse_state": "READY", "document_id": doc_id},
    )
    result = dp._handle_process_document("job-1", json.dumps({"documentId": "doc-1", "kbId": "kb-1"}))
    assert result["graph_build_triggered"] is True
    # wiki 关闭 → 不触发 wiki
    assert result["wiki_skill_triggered"] is False
    db = Session()
    classes = {j.task_class for j in db.execute(select(Job)).scalars().all()}
    assert "KbGraphBuildTask" in classes
    assert "KbAgentWikiBuildTask" not in classes
    db.close()


def test_handle_process_document_no_wiki_when_disabled(chain_env, monkeypatch) -> None:
    """wiki_enabled=false → 不触发 wiki 构建（WeKnora 默认语义）。"""
    Session, sent = chain_env
    from api.models.knowledge import KbDatasource

    db = Session()
    db.add(
        KbDatasource(
            id="kb-1",
            name="kb",
            indexing_strategy={"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": False},
            state="1",
        )
    )
    db.commit()
    db.close()
    monkeypatch.setattr(
        dp, "process_document",
        lambda doc_id, content, parser_engine=None: {"success": True, "parse_state": "READY", "document_id": doc_id},
    )
    result = dp._handle_process_document("job-1", json.dumps({"documentId": "doc-1", "kbId": "kb-1"}))
    assert result["wiki_skill_triggered"] is False
    db = Session()
    assert len(db.execute(select(Job)).scalars().all()) == 0
    db.close()


def test_handle_process_document_no_chain_without_kb_id(chain_env, monkeypatch) -> None:
    """process_document 成功（READY）但缺 kb_id → 不触发（避免任务无目标库）。"""
    Session, sent = chain_env
    monkeypatch.setattr(
        dp, "process_document",
        lambda doc_id, content, parser_engine=None: {"success": True, "parse_state": "READY", "document_id": doc_id},
    )
    result = dp._handle_process_document("job-1", json.dumps({"documentId": "doc-1"}))
    assert result["success"] is True
    assert result.get("wiki_skill_triggered") is None
    db = Session()
    assert len(db.execute(select(Job)).scalars().all()) == 0
    db.close()
