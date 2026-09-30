"""KB management service tests — CRUD, delete guard, docs, wiki tree, JSON search."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import (
    DocChunk,
    KbDatasource,
    KbDocument,
    WikiFolder,
    WikiPage,
)
from api.services.kb_admin import (
    create_document,
    create_kb,
    delete_document,
    delete_kb,
    get_kb,
    get_wiki_page,
    json_search,
    list_documents,
    list_kbs,
    update_kb,
    wiki_tree,
)


@pytest.fixture()
def kb_db(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    # Business tables live on the framework base (portable relational types).
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def test_create_and_list_kb(kb_db) -> None:
    kb = create_kb(kb_db, "制度库", label="制度", description="监管制度知识库")
    data = list_kbs(kb_db, page=1, page_size=10)
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == kb.id
    assert item["name"] == "制度库"
    assert item["doc_count"] == 0
    assert item["page_count"] == 0


def test_list_kbs_keyword_filter(kb_db) -> None:
    create_kb(kb_db, "技术文档库")
    create_kb(kb_db, "业务手册库")
    data = list_kbs(kb_db, keyword="技术")
    assert data["total"] == 1
    assert data["items"][0]["name"] == "技术文档库"


def test_update_kb(kb_db) -> None:
    kb = create_kb(kb_db, "旧名")
    updated = update_kb(kb_db, kb.id, {"name": "新名", "description": "desc"})
    assert updated is not None
    assert updated.name == "新名"
    assert updated.description == "desc"
    reloaded = get_kb(kb_db, kb.id)
    assert reloaded is not None
    assert reloaded.name == "新名"


def test_update_kb_missing_returns_none(kb_db) -> None:
    assert update_kb(kb_db, "nope", {"name": "x"}) is None


def test_delete_kb_ok_when_empty(kb_db) -> None:
    kb = create_kb(kb_db, "空库")
    result = delete_kb(kb_db, kb.id)
    assert result["success"] is True
    assert get_kb(kb_db, kb.id) is None  # soft-deleted -> filtered out
    row = kb_db.get(KbDatasource, kb.id)
    assert row is not None
    assert row.state == "0"


def test_delete_kb_guard_when_documents_exist(kb_db) -> None:
    kb = create_kb(kb_db, "有文档库")
    create_document(kb_db, kb.id, "a.md")
    result = delete_kb(kb_db, kb.id)
    assert result["success"] is False
    assert "文档" in result["message"]
    assert get_kb(kb_db, kb.id) is not None  # still active


def test_delete_kb_missing(kb_db) -> None:
    result = delete_kb(kb_db, "nope")
    assert result["success"] is False


def test_create_list_delete_document(kb_db) -> None:
    kb = create_kb(kb_db, "库")
    doc = create_document(kb_db, kb.id, "说明.pdf", file_ext=".pdf", file_size=1024)
    assert doc.parse_state == "PENDING"
    data = list_documents(kb_db, kb.id)
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == doc.id
    assert item["file_ext"] == "pdf"  # normalized

    result = delete_document(kb_db, kb.id, doc.id)
    assert result["success"] is True
    assert list_documents(kb_db, kb.id)["total"] == 0


def test_delete_document_cascades_chunks(kb_db) -> None:
    kb = create_kb(kb_db, "库")
    doc = create_document(kb_db, kb.id, "t.md")
    kb_db.add_all(
        [
            DocChunk(id="c1", kb_id=kb.id, document_id=doc.id, seq=0, content="x"),
            DocChunk(id="c2", kb_id=kb.id, document_id=doc.id, seq=1, content="y"),
        ]
    )
    kb_db.commit()
    result = delete_document(kb_db, kb.id, doc.id)
    assert result["success"] is True
    assert kb_db.query(DocChunk).count() == 0


def test_delete_document_missing(kb_db) -> None:
    kb = create_kb(kb_db, "库")
    result = delete_document(kb_db, kb.id, "nope")
    assert result["success"] is False


def test_wiki_tree_and_page_detail(kb_db) -> None:
    kb = create_kb(kb_db, "库")
    folder = WikiFolder(id="f1", kb_id=kb.id, name="制度", parent_id="")
    page = WikiPage(
        id="p1",
        kb_id=kb.id,
        slug="数据中台",
        title="数据中台",
        page_type="concept",
        content="# 数据中台\n\n定义…",
        summary="中台建设",
        source_refs=["d1"],
        folder_id="f1",
        status="active",
    )
    kb_db.add_all([folder, page])
    kb_db.commit()

    tree = wiki_tree(kb_db, kb.id)
    assert len(tree["folders"]) == 1
    assert tree["folders"][0]["page_count"] == 1
    assert len(tree["pages"]) == 1
    assert tree["pages"][0]["slug"] == "数据中台"

    detail = get_wiki_page(kb_db, kb.id, "数据中台")
    assert detail is not None
    assert detail["title"] == "数据中台"
    assert detail["source_refs"] == ["d1"]
    assert detail["links"] == []
    assert get_wiki_page(kb_db, kb.id, "不存在") is None


def test_json_search_no_chunks_returns_empty(kb_db) -> None:
    kb = create_kb(kb_db, "库")
    hits = json_search(kb_db, kb.id, "查询", top_k=5, threshold=0.2)
    assert hits == []


def test_json_search_unknown_kb_returns_empty(kb_db) -> None:
    assert json_search(kb_db, "nope", "查询") == []