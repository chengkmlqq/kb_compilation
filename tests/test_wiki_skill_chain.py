"""doc_process 自动触发「基于技能的 wiki 构建」链路测试。

上传文档 → KbDocumentProcessTask 成功 → 自动 enqueue KbAgentGatewayTask
（技能任务，config 携带 skill/kb_id/doc_name/kb_base_url）。
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

    class FakeSettings:
        WIKI_SKILL_NAME = "kb-wiki-builder"
        KB_PUBLIC_BASE_URL = "http://10.1.215.50"
        WIKI_LLM_BASE_URL = "https://inferaiapi.com/v1"
        WIKI_LLM_MODEL = "deepseek-v4-pro"
        WIKI_LLM_API_KEY = "sk-test-key"
        AUTH_ADMIN_USERS = ""

    import api.config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: FakeSettings())
    yield Session, sent


def test_enqueue_wiki_skill_task_writes_job(chain_env) -> None:
    Session, sent = chain_env
    ok = dp._enqueue_wiki_skill_task("kb-1", "doc-1")
    assert ok is True
    assert sent["name"] == "worker.tasks.scheduler.execute_modo_job"
    assert sent["queue"] == "default"
    # modo_job 行已写入（任务监控可见）
    db = Session()
    jobs = db.execute(select(Job)).scalars().all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.task_class == "KbAgentGatewayTask"
    assert job.state == "PENDING"
    params = json.loads(job.task_params)
    assert params["config"]["skill"] == "kb-wiki-builder"
    assert params["config"]["kb_id"] == "kb-1"
    assert params["config"]["doc_name"] == "doc-1"
    assert params["config"]["kb_base_url"] == "http://10.1.215.50"
    # LLM 配置注入（gateway runner 转 WEKNORA_LLM_*）
    assert params["config"]["base_url"] == "https://inferaiapi.com/v1"
    assert params["config"]["model"] == "deepseek-v4-pro"
    assert params["config"]["api_key"] == "sk-test-key"
    db.close()


def test_enqueue_wiki_skill_task_broker_failure_returns_false(chain_env, monkeypatch) -> None:
    Session, sent = chain_env

    def boom(*a, **k):
        raise RuntimeError("broker down")

    import worker.celery_app as celery_mod

    monkeypatch.setattr(celery_mod.celery_app, "send_task", boom)
    ok = dp._enqueue_wiki_skill_task("kb-1", "doc-1")
    assert ok is False


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
    """process_document 失败（parse_state=FAILED）→ 不触发技能任务。"""
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


def test_handle_process_document_no_chain_without_kb_id(chain_env, monkeypatch) -> None:
    """process_document 成功（READY）但缺 kb_id → 不触发（避免技能任务无目标库）。"""
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