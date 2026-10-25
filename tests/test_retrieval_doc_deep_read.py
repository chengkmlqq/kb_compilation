"""Agent 多跳深读（document_id 锁定检索）测试 —— 2026-10-25 新增。

覆盖 kb_search 工具加 document_id 参数后的三条链路：
1. keyword_search / hybrid_search 带 document_id → 词法臂 SQL 只在该文档切片内检索
2. vector_search 带 chunk_ids → 向量调用点把文档切片集合透传给 vector store
3. _chunk_hits_to_text 输出带文档 id（LLM 可见归属，才能发起下一跳深读）
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.models.knowledge import DocChunk, KbDocument
from api.services.agent_tools_full import _chunk_hits_to_text, TOOL_CATALOG
from api.services.retrieval import ChunkHit, RetrievalConfig, hybrid_search, keyword_search, vector_search


@pytest.fixture()
def kv_db():
    """sqlite 内存库，建全部表（对齐 test_doc_chunks.py 模式）。"""
    from api.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()


def _seed_chunks(db) -> None:
    """两个文档：doc-a 2 条、doc-b 1 条，都含关键词「报销」。"""
    db.add_all([
        DocChunk(id="c-a1", kb_id="kb1", document_id="doc-a", seq=0,
                 content="外部课程报销需提前提交申请，上限 5000 元。"),
        DocChunk(id="c-a2", kb_id="kb1", document_id="doc-a", seq=1,
                 content="培训管理办法实施细则见本文件附件。"),
        DocChunk(id="c-b1", kb_id="kb1", document_id="doc-b", seq=0,
                 content="差旅报销标准：一线城市每日 800 元。"),
    ])
    db.add_all([
        KbDocument(id="doc-a", kb_id="kb1", file_name="2024培训管理办法.pdf"),
        KbDocument(id="doc-b", kb_id="kb1", file_name="差旅费用指南.docx"),
    ])
    db.commit()


def test_keyword_search_filters_by_document_id(kv_db) -> None:
    """词法臂带 document_id → 只返回该文档的切片，不误伤其他文档。"""
    _seed_chunks(kv_db)
    cfg = RetrievalConfig(keyword_enabled=True, vector_enabled=False)
    hits = keyword_search(kv_db, "kb1", "报销", cfg, document_id="doc-a")
    assert {h.document_id for h in hits} == {"doc-a"}
    assert len(hits) == 1
    # 不传 document_id = 全库范围（原行为不变）
    hits_all = keyword_search(kv_db, "kb1", "报销", cfg)
    assert {h.document_id for h in hits_all} == {"doc-a", "doc-b"}


def test_hybrid_search_document_id_reaches_keyword_arm(kv_db) -> None:
    """hybrid_search 带 document_id → 词法臂锁定文档（向量臂关闭时纯词法路径）。"""
    _seed_chunks(kv_db)
    cfg = RetrievalConfig(keyword_enabled=True, vector_enabled=False)
    hits = hybrid_search(kv_db, "kb1", "报销", None, cfg, document_id="doc-a")
    assert {h.document_id for h in hits} == {"doc-a"}


def test_vector_search_forwards_chunk_ids() -> None:
    """向量臂调用点把文档切片集合透传给 vector store（chunk_ids 参数）。"""
    sent: dict = {}

    class _FakeStore:
        def search(self, kb_id, query_embedding, **kwargs):
            sent.update(kwargs)
            return []

    cfg = RetrievalConfig(vector_enabled=True, keyword_enabled=False)
    with patch("api.services.vector_store.get_vector_store", return_value=_FakeStore()):
        vector_search("kb1", [0.1] * 8, cfg, chunk_ids=["c-a1", "c-a2"])
    assert sent["chunk_ids"] == ["c-a1", "c-a2"]
    # 未传 chunk_ids → 不附加过滤（原行为）
    with patch("api.services.vector_store.get_vector_store", return_value=_FakeStore()):
        vector_search("kb1", [0.1] * 8, cfg)
    assert "chunk_ids" not in sent or sent["chunk_ids"] is None


def test_chunk_hits_to_text_includes_document_id() -> None:
    """检索结果回填文本带文档 id —— LLM 据此发起下一跳深读。"""
    hits = [
        ChunkHit(chunk_id="c-a1", content="外部课程报销需提前提交申请。",
                 document_id="doc-a", kb_id="kb1", score=0.8),
    ]
    text = _chunk_hits_to_text(hits)
    assert "doc-a" in text
    assert "id=" in text


def test_catalog_descriptions_mention_document_id() -> None:
    """TOOL_CATALOG 三个检索工具的描述带 document_id 语义（UI + LLM schema 双消费）。"""
    names = {t["name"]: t["description"] for t in TOOL_CATALOG}
    for n in ("knowledge_search", "kb_search", "grep_chunks"):
        assert "document_id" in names[n], n