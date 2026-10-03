"""KbSkillWikiBuildTask handler 测试：读 chunks → LLM mock → 写 wiki 页。"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Job
from api.models.knowledge import DocChunk, KbDatasource, KbDocument, WikiPage
import worker.tasks.wiki_build as wb


@pytest.fixture()
def build_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    # wiki_build 模块级 import 已绑定，直接 patch 模块内引用
    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)
    monkeypatch.setattr(wb, "get_sessionmaker", lambda: Session)

    # 预置 kb + document + chunks
    db = Session()
    db.add(
        KbDatasource(id="kb-1", name="wiki-build-kb", label="测试库", description="",
                     owner_user_id="u1", scope="system", state="1")
    )
    db.add(
        KbDocument(id="doc-1", kb_id="kb-1", file_name="test.md", file_ext="md",
                   parse_state="READY", chunk_count=2)
    )
    for i in range(2):
        db.add(
            DocChunk(id=f"chunk-{i+1}", kb_id="kb-1", document_id="doc-1",
                     seq=i + 1, content=f"chunk {i + 1} content 安全生产", enabled=True)
        )
    db.add(Job(id="wiki-job-1", task_id="wiki-job-1", task_class="KbSkillWikiBuildTask",
               queue_name="default", task_params="{}", trigger_type="API", state="RUNNING"))
    db.commit()
    db.close()

    monkeypatch.setattr(wb, "_llm_chat", lambda messages: json.dumps({
        "title": "安全生产管理办法",
        "summary": "本办法适用于公司全体员工，明确安全生产责任体系与奖惩机制。",
        "entities": [
            {"name": "安全生产责任制", "definition": "明确各岗位安全责任的管理制度。"},
            {"name": "三级安全教育", "definition": "公司级部门级班组级三级安全教育。"},
        ],
    }, ensure_ascii=False))
    yield Session


def test_handle_wiki_build_writes_pages(build_env) -> None:
    Session = build_env
    result = wb._handle_wiki_build("wiki-job-1", json.dumps({"kbId": "kb-1", "documentId": "doc-1"}))
    assert result["success"] is True
    assert result["title"] == "安全生产管理办法"
    assert len(result["pages"]) == 3  # 1 summary + 2 entity
    assert result["pages"][0]["page_type"] == "summary"
    db = Session()
    pages = db.execute(select(WikiPage)).scalars().all()
    assert len(pages) == 3
    slugs = {p.slug for p in pages}
    assert any(s.startswith("doc-") for s in slugs)
    assert any(s.startswith("entity-") for s in slugs)
    db.close()


def test_handle_wiki_build_idempotent_update(build_env) -> None:
    """同文档二次触发：slug 冲突走 update（不新增重复页）。"""
    Session = build_env
    wb._handle_wiki_build("wiki-job-1", json.dumps({"kbId": "kb-1", "documentId": "doc-1"}))
    wb._handle_wiki_build("wiki-job-2", json.dumps({"kbId": "kb-1", "documentId": "doc-1"}))
    db = Session()
    pages = db.execute(select(WikiPage)).scalars().all()
    assert len(pages) == 3  # 仍是 3 页（update 而非新增）
    db.close()


def test_handle_wiki_build_missing_chunks(build_env) -> None:
    Session = build_env
    result = wb._handle_wiki_build("wiki-job-1", json.dumps({"kbId": "kb-1", "documentId": "NOPE"}))
    assert result["success"] is False
    assert "chunks" in result["error"]


def test_handle_wiki_build_llm_error_fails_job(build_env, monkeypatch) -> None:
    Session = build_env

    def boom(messages):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(wb, "_llm_chat", boom)
    with pytest.raises(RuntimeError):
        wb._handle_wiki_build("wiki-job-1", json.dumps({"kbId": "kb-1", "documentId": "doc-1"}))
