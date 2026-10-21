"""Tests for worker/tasks/orphan_doc_cleanup.py（孤儿文档任务清理）。

场景：文档已删除（kb_document 无行）但任务监控（modo_job）仍有 DOC_* 链头。
手动删除文档时 kb_admin._cleanup_document_jobs 会级联清理，但该逻辑上线前的
存量残留需要本定时任务兜底（用户 2026-10-21：每 10 分钟清理一次）。
"""

from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base
from api.models.framework import Job
from api.models.knowledge import KbDocument
from api.models.orchestration import TapeRun
from worker.tasks.orphan_doc_cleanup import cleanup_orphan_doc_jobs

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def _make_session():
    Base.metadata.create_all(engine)
    db = Session()
    return db


def _patch_sessionmaker(monkeypatch):
    """让函数体内 import 的 get_sessionmaker 返回测试内存库会话。

    注意：cleanup_orphan_doc_jobs 内是 `from api.db import get_sessionmaker`
    （函数体内延迟导入）——必须 patch api.db.get_sessionmaker 本体，
    patch 调用者模块（orphan_doc_cleanup）上是无效的。
    """
    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)


def test_cleanup_removes_orphan_head_and_children(monkeypatch) -> None:
    """文档已删的链头 + 其子任务 + 关联 run 被级联删除。"""
    db = _make_session()
    db.query(Job).delete()
    db.query(KbDocument).delete()
    db.query(TapeRun).delete()
    db.commit()

    # 文档不存在（已删）
    db.add(Job(id="DOC_doc_removed", task_class="KbDocumentProcessTask",
               task_params=json.dumps({"kbId": "kb1", "documentId": "doc_removed"}),
               trigger_type="API", state="PENDING"))
    # 子任务（parent 指向链头）
    db.add(Job(id="SUMMARY_RUN_1", task_class="KbOrchestrationRunTask",
               task_params=json.dumps({"runId": "run1", "env": "prod"}),
               trigger_type="API", state="SUCCESS", parent_id="DOC_doc_removed"))
    # 文档存在（不应删）
    db.add(KbDocument(id="doc_alive", kb_id="kb1", file_name="存活.docx"))
    db.add(Job(id="DOC_doc_alive", task_class="KbDocumentProcessTask",
               task_params=json.dumps({"kbId": "kb1", "documentId": "doc_alive"}),
               trigger_type="API", state="SUCCESS"))
    # 编排 run（同文档 → 应删）
    db.add(TapeRun(id="run1", tape_id="tape1", tape_name="摘要",
                   status="success", inputs={"kb_id": "kb1", "document_id": "doc_removed"}))
    db.commit()

    _patch_sessionmaker(monkeypatch)
    result = cleanup_orphan_doc_jobs()

    assert result["orphan_heads"] == 1
    assert result["children"] == 1
    assert result["runs"] == 1
    assert job_exists("DOC_doc_removed") is False
    assert job_exists("SUMMARY_RUN_1") is False
    assert run_exists("run1") is False
    # 文档存活的任务不动
    assert job_exists("DOC_doc_alive") is True


def test_cleanup_dry_run_keeps_rows(monkeypatch) -> None:
    """dry_run=true：只统计不删除。"""
    db = _make_session()
    db.query(Job).delete()
    db.query(KbDocument).delete()
    db.commit()

    db.add(Job(id="DOC_doc_removed", task_class="KbDocumentProcessTask",
               task_params=json.dumps({"kbId": "kb1", "documentId": "doc_removed"}),
               trigger_type="API", state="PENDING"))
    db.commit()

    _patch_sessionmaker(monkeypatch)
    result = cleanup_orphan_doc_jobs({"dry_run": True})

    assert result["orphan_heads"] == 1
    assert result["total_jobs"] == 1
    assert job_exists("DOC_doc_removed") is True


def test_cleanup_skips_task_without_doc_id(monkeypatch) -> None:
    """task_params 无 documentId 的链头不参与（不是文档处理链头形态）。"""
    db = _make_session()
    db.query(Job).delete()
    db.query(KbDocument).delete()
    db.commit()

    db.add(Job(id="DOC_no_params", task_class="KbDocumentProcessTask",
               task_params=json.dumps({"kbId": "kb1"}),
               trigger_type="API", state="PENDING"))
    db.commit()

    _patch_sessionmaker(monkeypatch)
    result = cleanup_orphan_doc_jobs()

    assert result["orphan_heads"] == 0
    assert job_exists("DOC_no_params") is True


def job_exists(job_id: str) -> bool:
    db = Session()
    try:
        return db.get(Job, job_id) is not None
    finally:
        db.close()


def run_exists(run_id: str) -> bool:
    db = Session()
    try:
        return db.get(TapeRun, run_id) is not None
    finally:
        db.close()