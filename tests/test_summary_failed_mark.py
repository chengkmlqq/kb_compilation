"""摘要编排失败回写测试（2026-10-21）。

需求：摘要编排 run 失败时要能把 kb_document.summary_status 标成 FAILED，
否则文档列表里「已就绪」灰色（摘要未生成）与「摘要失败」无法区分。

实现：文档链触发的摘要 run 在 inputs 打 purpose="summary" 标记
（worker/tasks/doc_process.py _enqueue_summary_task），orch_runner 的
_update_run 把 run 置 failed 终态时经 _mark_summary_failed 回写文档状态。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import KbDocument
from api.models.orchestration import TapeRun
import worker.tasks.orch_runner as runner


@pytest.fixture()
def orch_env(monkeypatch) -> sessionmaker:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda env=None: Session)
    return Session


def _add_doc(db, doc_id: str = "doc-1", summary_status: str = "") -> None:
    db.add(KbDocument(
        id=doc_id, kb_id="kb-1", file_name="测试.md", file_ext="md",
        parse_state="READY", summary_status=summary_status,
    ))
    db.commit()


def _add_run(db, run_id: str = "run-sum", inputs: dict | None = None) -> None:
    db.add(TapeRun(
        id=run_id, tape_id="tape-sum", tape_name="摘要-Map-Reduce",
        status="queued", inputs=inputs or {},
    ))
    db.commit()


def test_mark_summary_failed_writes_status(orch_env) -> None:
    """摘要 run（purpose=summary）失败 → 回写 summary_status=FAILED + summary_error。"""
    Session = orch_env
    db = Session()
    _add_doc(db)
    db.close()

    runner._mark_summary_failed(
        {"purpose": "summary", "document_id": "doc-1"}, "LLM 调用失败: 429"
    )

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == "FAILED"
    assert doc.summary_error == "LLM 调用失败: 429"
    db.close()


def test_mark_summary_failed_ignores_non_summary_run(orch_env) -> None:
    """构建/题材分析等非摘要 run（无 purpose 标记）失败 → 不动文档。"""
    Session = orch_env
    db = Session()
    _add_doc(db)
    db.close()

    runner._mark_summary_failed({"document_id": "doc-1"}, "boom")

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == ""
    db.close()


def test_mark_summary_failed_ignores_unknown_purpose(orch_env) -> None:
    """purpose 是别的值（如 build）→ 不动文档。"""
    Session = orch_env
    db = Session()
    _add_doc(db)
    db.close()

    runner._mark_summary_failed({"purpose": "build", "document_id": "doc-1"}, "boom")

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == ""
    db.close()


def test_mark_summary_failed_does_not_override_ready(orch_env) -> None:
    """文档已有成功摘要（READY）→ 旧失败 run 不覆盖。"""
    Session = orch_env
    db = Session()
    _add_doc(db, summary_status="READY")
    db.close()

    runner._mark_summary_failed(
        {"purpose": "summary", "document_id": "doc-1"}, "迟到的失败"
    )

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == "READY"
    db.close()


def test_update_run_failed_triggers_summary_mark(orch_env) -> None:
    """_update_run 置 failed 终态 → 摘要 run 自动回写文档 FAILED。"""
    Session = orch_env
    db = Session()
    _add_doc(db)
    _add_run(db, inputs={"purpose": "summary", "document_id": "doc-1"})
    db.close()

    runner._update_run("run-sum", status="failed", error="终态失败")

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == "FAILED"
    run = db.get(TapeRun, "run-sum")
    assert run.status == "failed"
    db.close()


def test_update_run_success_does_not_mark(orch_env) -> None:
    """_update_run 置 success 终态 → 不回写。"""
    Session = orch_env
    db = Session()
    _add_doc(db)
    _add_run(db, inputs={"purpose": "summary", "document_id": "doc-1"})
    db.close()

    runner._update_run("run-sum", status="success")

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == ""
    db.close()


def test_update_run_non_summary_failed_does_not_mark(orch_env) -> None:
    """非摘要 run failed → 不动文档。"""
    Session = orch_env
    db = Session()
    _add_doc(db)
    _add_run(db, inputs={"purpose": "build", "document_id": "doc-1"})
    db.close()

    runner._update_run("run-sum", status="failed", error="boom")

    db = Session()
    doc = db.get(KbDocument, "doc-1")
    assert doc.summary_status == ""
    db.close()