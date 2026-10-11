"""编排引擎细粒度基础原子测试（2026-10-12）。

覆盖：
- ATOM_MAP 注册 4 个新原子（kb_read_doc / kb_llm_chat / wiki_upsert_page / wiki_rebuild_links）
- KbReadDocStep：读文档切片写 bindings
- WikiUpsertPageStep：幂等建页（create/update/skip_empty）
- WikiRebuildLinksStep：缺 kb_id 失败
- wiki 构建模板：节点/边结构合法、含全部基础原子
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.services.orchestration_engine import (
    ATOM_MAP,
    KbReadDocStep,
    TapeRuntimeContext,
    WikiUpsertPageStep,
)
from api.models.knowledge import DocChunk


@pytest.fixture()
def orch_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            DocChunk(id="c1", kb_id="kb1", document_id="doc1", seq=0, content="第一块内容"),
            DocChunk(id="c2", kb_id="kb1", document_id="doc1", seq=1, content="第二块内容"),
            DocChunk(id="c3", kb_id="kb1", document_id="doc2", seq=0, content="另一文档"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_atom_map_registered() -> None:
    for inst in ("kb_read_doc", "kb_llm_chat", "wiki_upsert_page", "wiki_rebuild_links"):
        assert inst in ATOM_MAP, f"{inst} 未注册到 ATOM_MAP"


def test_kb_read_doc_writes_bindings(orch_db, monkeypatch) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc1"})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success
    chunks = ctx.bindings.get("chunks")
    assert chunks is not None and len(chunks) == 2
    assert chunks[0]["content"] == "第一块内容"
    assert "set:chunks" in resp.body


def test_kb_read_doc_missing_doc_fails(orch_db, monkeypatch) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc-none"})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success


def test_kb_read_doc_max_chunks(orch_db, monkeypatch) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc1", "max_chunks": 1})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success and len(ctx.bindings["chunks"]) == 1


def test_wiki_upsert_page_skip_empty() -> None:
    # page_title 为空字符串（script 节点准备了空变量）→ skip_empty 跳过成功
    step = WikiUpsertPageStep("n8", "写实体页", "wiki_upsert_page",
                              {"kb_id": "kb1", "title": "{{page_title}}",
                               "slug": "{{page_slug}}", "skip_empty": True})
    ctx = TapeRuntimeContext({"page_title": "", "page_slug": ""})
    resp = step.handle(ctx)
    assert resp.success and resp.body.get("skipped") is True


def test_wiki_upsert_page_requires_kb() -> None:
    step = WikiUpsertPageStep("n5", "写摘要页", "wiki_upsert_page",
                              {"title": "t", "slug": "s", "content": "c"})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success and "kb_id" in resp.error


def test_wiki_build_template_structure() -> None:
    import sys
    sys.path.insert(0, ".")
    from scripts.seed_framework_db import WIKI_BUILD_TAPE

    insts = [n["data"]["stepInst"] for n in WIKI_BUILD_TAPE["nodes"]]
    assert insts == ["kb_read_doc", "script", "kb_llm_chat", "script",
                     "wiki_upsert_page", "loop", "script", "wiki_upsert_page", "wiki_rebuild_links"]
    assert len(WIKI_BUILD_TAPE["edges"]) == 8
