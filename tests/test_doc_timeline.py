"""文档处理时间线（processing-timeline）测试——wiki 构建阶段源判定。

覆盖 2026-10-16 改造：绑定构建引擎的文档 wiki 由编排（ORCH_RUN_* job →
kb_tape_run）承接，无 WIKI_SKILL 任务；timeline 的 wiki 阶段须回退编排 run，
否则显示 NONE（此前只查 WIKI% 任务）。状态值 run.status 小写转大写对齐前端。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Job
from api.models.knowledge import KbDocument
from api.models.orchestration import TapeRun
from api.routers.kbs import document_processing_timeline
from api.services.kb_admin import create_document, create_kb


@pytest.fixture()
def tl_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def _make_doc(db, file_name: str = "制度文档.md") -> tuple[str, str]:
    kb = create_kb(db, "制度库")
    doc = create_document(db, kb.id, file_name)
    return kb.id, doc.id


def _seed_orch_run(db, kb_id: str, doc_id: str, status: str = "success", error: str | None = None) -> tuple[str, str]:
    """建 ORCH_RUN_* job + kb_tape_run（inputs 按上传链契约：kb_id/document_id/doc_id）。"""
    job_id = f"ORCH_RUN_{doc_id[:8]}"
    run_id = f"run_{doc_id[:12]}"
    db.add(
        TapeRun(
            id=run_id,
            tape_id="tape-build",
            tape_name="wiki构建-基础原子版",
            status=status,
            inputs={"kb_id": kb_id, "document_id": doc_id, "doc_id": doc_id},
            error=error,
        )
    )
    db.add(
        Job(
            id=job_id,
            task_id=job_id,
            task_class="KbOrchestrationRunTask",
            state="SUCCESS" if status == "success" else "FAILED",
            task_params=f'{{"runId": "{run_id}", "env": "prod"}}',
            parent_id=f"DOC_{doc_id}",
        )
    )
    db.commit()
    return job_id, run_id


def test_timeline_wiki_stage_from_orch_run_success(tl_db) -> None:
    """无 WIKI 任务、有 ORCH_RUN 编排 run（build engine 承接）→ wiki 阶段取 run 状态。"""
    kb_id, doc_id = _make_doc(tl_db)
    job_id, _run_id = _seed_orch_run(tl_db, kb_id, doc_id, status="success")

    data = document_processing_timeline(kb_id, doc_id, db=tl_db)
    stages = {s["key"]: s for s in data["data"]["stages"]}
    wiki = stages["wiki"]
    assert wiki["state"] == "SUCCESS"
    assert wiki["job_id"] == job_id


def test_timeline_wiki_stage_orch_run_failed(tl_db) -> None:
    """编排 run failed → wiki 阶段 FAILED，并带 run.error。"""
    kb_id, doc_id = _make_doc(tl_db)
    _seed_orch_run(tl_db, kb_id, doc_id, status="failed", error="LLM 429 超限")

    data = document_processing_timeline(kb_id, doc_id, db=tl_db)
    wiki = {s["key"]: s for s in data["data"]["stages"]}["wiki"]
    assert wiki["state"] == "FAILED"
    assert wiki["error"] == "LLM 429 超限"


def test_timeline_wiki_stage_other_doc_run_ignored(tl_db) -> None:
    """ORCH_RUN 指向其他文档 → 不误配，wiki 阶段保持 NONE。"""
    kb_id, doc_id = _make_doc(tl_db, "目标.md")
    other_kb_id, other_doc_id = _make_doc(tl_db, "其他.md")
    _seed_orch_run(tl_db, other_kb_id, other_doc_id, status="success")

    data = document_processing_timeline(kb_id, doc_id, db=tl_db)
    wiki = {s["key"]: s for s in data["data"]["stages"]}["wiki"]
    assert wiki["state"] == "NONE"
    assert wiki["job_id"] is None


def test_timeline_wiki_stage_orch_running_uppercased(tl_db) -> None:
    """run.status 库值小写（running）→ 输出大写 RUNNING（对齐前端契约）。"""
    kb_id, doc_id = _make_doc(tl_db)
    _seed_orch_run(tl_db, kb_id, doc_id, status="running")

    data = document_processing_timeline(kb_id, doc_id, db=tl_db)
    wiki = {s["key"]: s for s in data["data"]["stages"]}["wiki"]
    assert wiki["state"] == "RUNNING"