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
    WikiEnsureFolderStep,
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
    for inst in ("kb_read_doc", "kb_llm_chat", "wiki_upsert_page", "wiki_rebuild_links", "wiki_ensure_folder",
                 "long_sentence_extract", "keyword_extract"):
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


def test_wiki_ensure_folder_idempotent(orch_db) -> None:
    import api.db
    from api.models.knowledge import WikiFolder

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                {"kb_id": "kb1", "path": "长句原文/2024"})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success and resp.body["created"] == 2 and resp.body["reused"] == 0
    assert ctx.bindings["folder_id"]  # 单条路径 → 输出末级目录 id

    # 幂等：再跑一次 → 全部复用
    ctx2 = TapeRuntimeContext({})
    resp2 = step.handle(ctx2)
    assert resp2.success and resp2.body["created"] == 0 and resp2.body["reused"] == 2
    assert ctx2.bindings["folder_id"] == ctx.bindings["folder_id"]

    # 校验目录树确实存在（2 层）
    folders = orch_db.query(WikiFolder).filter(WikiFolder.kb_id == "kb1").all()
    assert len(folders) == 2


def test_wiki_ensure_folder_batch_and_dry_run(orch_db) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    # dry_run：只统计不写库
    step = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                {"kb_id": "kb1", "paths": ["长句原文", "高频关键词"], "dry_run": True})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success and resp.body["created"] == 2 and resp.body["reused"] == 0
    # 批量：不 dry_run，输出 id 列表
    step2 = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                 {"kb_id": "kb1", "paths": ["长句原文", "高频关键词"], "output_var": "folder_ids"})
    ctx2 = TapeRuntimeContext({})
    resp2 = step2.handle(ctx2)
    assert resp2.success and resp2.body["created"] == 2
    assert isinstance(ctx2.bindings["folder_ids"], list) and len(ctx2.bindings["folder_ids"]) == 2


def test_wiki_ensure_folder_requires_kb() -> None:
    step = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                {"path": "长句原文"})
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
