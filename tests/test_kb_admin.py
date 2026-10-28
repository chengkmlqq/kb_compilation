"""KB management service tests — CRUD, delete guard, docs, wiki tree, JSON search."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import (
    DocChunk,
    KbAgent,
    KbDatasource,
    KbDocument,
    WikiFolder,
    WikiPage,
)
from api.services.kb_admin import (
    create_document,
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
from conftest import create_test_kb as create_kb


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


def test_delete_kb_cascade_when_documents_exist(kb_db) -> None:
    kb = create_kb(kb_db, "有文档库")
    create_document(kb_db, kb.id, "a.md")
    kb_db.add(
        WikiPage(
            id="wp1",
            kb_id=kb.id,
            slug="s1",
            title="页面1",
            page_type="entity",
            content="内容",
            status="active",
        )
    )
    kb_db.commit()
    result = delete_kb(kb_db, kb.id)
    assert result["success"] is True
    assert result["data"]["doc_count"] == 1  # 级联删除返回计数
    assert result["data"]["wiki_count"] == 1
    row = kb_db.get(KbDatasource, kb.id)
    assert row is not None
    assert row.state == "0"  # KB 软删
    # 文档与 wiki 页一并清理（对齐 WeKnora 级联语义）
    assert kb_db.query(KbDocument).filter(KbDocument.kb_id == kb.id).count() == 0
    assert kb_db.query(WikiPage).filter(WikiPage.kb_id == kb.id).count() == 0


def test_delete_kb_missing(kb_db) -> None:
    result = delete_kb(kb_db, "nope")
    assert result["success"] is False


def _make_agent(kb_db, agent_id: str, name: str, mode: str, kb_ids: list[str], state: str = "1") -> KbAgent:
    agent = KbAgent(
        id=agent_id,
        name=name,
        state=state,
        config={
            "kb_selection_mode": mode,
            "knowledge_bases": list(kb_ids),
            "knowledge_ids": [],
        },
    )
    kb_db.add(agent)
    kb_db.commit()
    return agent


def test_delete_kb_rejected_when_agent_bound(kb_db) -> None:
    """显式绑定该库的启用智能体 → 拒绝删除（AGENT_BOUND，知识库保持可用）。"""
    kb = create_kb(kb_db, "被绑定库")
    _make_agent(kb_db, "ag1", "供管制度知识智能体", "selected", [kb.id])

    result = delete_kb(kb_db, kb.id)

    assert result["success"] is False
    assert result["code"] == "AGENT_BOUND"
    assert "供管制度知识智能体" in result["message"]
    assert result["data"]["bound_agents"][0]["id"] == "ag1"
    # 库未被软删（仍在可见列表里）
    assert get_kb(kb_db, kb.id) is not None
    assert kb_db.get(KbDatasource, kb.id).state == "1"


def test_delete_kb_ok_when_agent_binds_other_kb(kb_db) -> None:
    """智能体绑定的是别的库 → 不拦截。"""
    kb = create_kb(kb_db, "待删库")
    other = create_kb(kb_db, "另一个库")
    _make_agent(kb_db, "ag2", "其他智能体", "selected", [other.id])

    result = delete_kb(kb_db, kb.id)

    assert result["success"] is True
    assert get_kb(kb_db, kb.id) is None


def test_delete_kb_ok_when_agent_mode_all(kb_db) -> None:
    """all 模式智能体不构成显式绑定（软删后检索范围自动排除），不拦截。"""
    kb = create_kb(kb_db, "全量智能体库")
    _make_agent(kb_db, "ag3", "全量智能体", "all", [])

    result = delete_kb(kb_db, kb.id)

    assert result["success"] is True
    assert get_kb(kb_db, kb.id) is None


def test_delete_kb_ok_when_agent_soft_deleted(kb_db) -> None:
    """已删除（state=0）的智能体绑定不算阻断。"""
    kb = create_kb(kb_db, "库")
    _make_agent(kb_db, "ag4", "已删智能体", "selected", [kb.id], state="0")

    result = delete_kb(kb_db, kb.id)

    assert result["success"] is True


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


def test_delete_document_cascades_chunks(kb_db, monkeypatch) -> None:
    # 2026-10-20: delete_document 向量清理改为「commit 前 + fail-closed」——
    # 测试环境无 PG 向量库，须 mock VectorStore 避免删除被中止
    calls: dict = {}

    class _FakeStore:
        def delete_by_chunks(self, chunk_ids):  # noqa: ARG002
            calls["chunk_ids"] = chunk_ids

    monkeypatch.setattr(
        "api.services.vector_store.get_vector_store", lambda *a, **k: _FakeStore()
    )
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
    # 向量清理在提交前执行且拿到全部 chunk id（防孤儿残留的回归护栏）
    assert set(calls.get("chunk_ids") or []) == {"c1", "c2"}


def test_delete_document_missing(kb_db) -> None:
    kb = create_kb(kb_db, "库")
    result = delete_document(kb_db, kb.id, "nope")
    assert result["success"] is False


def test_delete_document_wiki_shared_page_kept(kb_db, monkeypatch) -> None:
    """删文档时 wiki 层语义（2026-10-21 性能改造回归护栏）：

    - 专属页（source_refs 仅含本文档）整页删除 + 目录清理；
    - 共享页（跨文档合并实体）只摘除本文档引用、页保留；
    - 其他文档页完全不受影响。
    原实现 select(WikiPage) 全量物化整库页后 Python 精判；现改 SQL LIKE 粗筛
    + 仅取三列，此测试保证两种实现的清理结果一致。
    """
    calls: dict = {}

    class _FakeStore:
        def delete_by_chunks(self, chunk_ids):  # noqa: ARG002
            calls["chunk_ids"] = chunk_ids

    monkeypatch.setattr(
        "api.services.vector_store.get_vector_store", lambda *a, **k: _FakeStore()
    )
    kb = create_kb(kb_db, "库")
    doc1 = create_document(kb_db, kb.id, "a.md")
    doc2 = create_document(kb_db, kb.id, "b.md")
    f_exclusive = WikiFolder(id="f_ex", kb_id=kb.id, name="专属目录", parent_id="")
    f_shared = WikiFolder(id="f_sh", kb_id=kb.id, name="共享目录", parent_id="")
    p_exclusive = WikiPage(
        id="p_ex", kb_id=kb.id, slug="ex", title="专属页", content="x",
        source_refs=[f"{doc1.id}|c1", f"{doc1.id}|c2"], folder_id="f_ex",
        status="active",
    )
    p_shared = WikiPage(
        id="p_sh", kb_id=kb.id, slug="sh", title="共享页", content="y",
        source_refs=[f"{doc1.id}|c3", f"{doc2.id}|c4"], folder_id="f_sh",
        status="active",
    )
    p_other = WikiPage(
        id="p_ot", kb_id=kb.id, slug="ot", title="其他文档页", content="z",
        source_refs=[f"{doc2.id}|c5"], folder_id="f_sh", status="active",
    )
    kb_db.add_all([f_exclusive, f_shared, p_exclusive, p_shared, p_other])
    kb_db.commit()

    result = delete_document(kb_db, kb.id, doc1.id)

    assert result["success"] is True
    # 专属页整页删除 + 其独占目录回收
    assert kb_db.get(WikiPage, "p_ex") is None
    assert kb_db.get(WikiFolder, "f_ex") is None
    # 共享页保留，引用仅剩 doc2
    shared = kb_db.get(WikiPage, "p_sh")
    assert shared is not None
    assert shared.source_refs == [f"{doc2.id}|c4"]
    # 其他文档页不受影响；被共享目录（仍有 p_other/p_sh 挂靠）不删
    other = kb_db.get(WikiPage, "p_ot")
    assert other is not None
    assert other.source_refs == [f"{doc2.id}|c5"]
    assert kb_db.get(WikiFolder, "f_sh") is not None
    # 文档行删除
    assert kb_db.get(KbDocument, doc1.id) is None
    assert kb_db.get(KbDocument, doc2.id) is not None


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