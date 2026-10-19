"""Hybrid retrieval tests — RRF fusion correctness.

The fusion logic is pure Python (no DB), so it is fully testable in-memory.
Vector/keyword SQL arms are covered by the sqlite-compatible subset where
possible (they need pgvector/postgres features, so those run against PG only).
"""

from __future__ import annotations

import pytest

from api.services.retrieval import (
    ChunkHit,
    RetrievalConfig,
    _filter_weak_hits,
    fuse_rrf,
    hybrid_search,
)


def _hit(chunk_id: str, score: float, vrank: int | None = None, krank: int | None = None) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        content=f"content-{chunk_id}",
        document_id=f"doc-{chunk_id}",
        kb_id="kb1",
        score=score,
        vector_rank=vrank,
        keyword_rank=krank,
    )


def test_fuse_rrf_matches_weknora_formula() -> None:
    """Verify RRF score against the Go formula: v/k+rank + w/k+rank."""
    cfg = RetrievalConfig(top_k=10)
    vector_hits = [_hit("a", 1.0, vrank=1), _hit("b", 0.9, vrank=2)]
    keyword_hits = [_hit("b", 0.8, krank=1)]

    result = fuse_rrf(vector_hits, keyword_hits, cfg)
    by_id = {h.chunk_id: h for h in result}

    assert set(by_id) == {"a", "b"}
    # a: only vector rank 1 -> 0.7 / (60 + 1)
    expected_a = 0.7 / 61
    assert by_id["a"].score == pytest.approx(expected_a, abs=1e-12)
    # b: vector rank 2 + keyword rank 1 -> 0.7/62 + 0.3/61
    expected_b = 0.7 / 62 + 0.3 / 61
    assert by_id["b"].score == pytest.approx(expected_b, abs=1e-12)
    # b must outrank a (both arms hit)
    assert result[0].chunk_id == "b"


def test_fuse_rrf_orders_by_score_desc() -> None:
    cfg = RetrievalConfig(top_k=5)
    vector_hits = [_hit("solo_only", 0.99, vrank=3)]
    keyword_hits = [
        _hit("both", 0.5, krank=1),
        _hit("keyword_only_a", 0.4, krank=2),
        _hit("keyword_only_b", 0.3, krank=3),
    ]
    result = fuse_rrf(vector_hits, keyword_hits, cfg)
    scores = [h.score for h in result]
    assert scores == sorted(scores, reverse=True)


def test_fuse_rrf_vector_only_no_fusion() -> None:
    cfg = RetrievalConfig(top_k=10)
    vector_hits = [_hit("a", 1.0, vrank=1), _hit("b", 0.9, vrank=2)]
    result = fuse_rrf(vector_hits, [], cfg)
    assert [h.chunk_id for h in result] == ["a", "b"]  # unchanged order, no re-scoring


def test_fuse_rrf_dedup_prefers_vector_meta() -> None:
    cfg = RetrievalConfig(top_k=10)
    v = _hit("dup", 1.0, vrank=1)
    v.meta = {"src": "vector"}
    k = _hit("dup", 0.7, krank=1)
    k.meta = {"src": "keyword"}
    result = fuse_rrf([v], [k], cfg)
    assert len(result) == 1
    assert result[0].meta == {"src": "vector"}  # vector metadata wins ties


def _weak_hit(chunk_id: str, vec: float | None, krank: int | None = None) -> ChunkHit:
    h = _hit(chunk_id, 0.5, vrank=1, krank=krank)
    h.vector_score = vec
    return h


def test_filter_weak_hits_drops_low_vector_no_keyword() -> None:
    """向量弱命中（< cutoff）且无关键词背书 → 丢弃（寒暄问题不再引用无关片段）。"""
    cfg = RetrievalConfig(weak_hit_cutoff=0.55)
    hits = [
        _weak_hit("low", 0.51),           # 弱向量、无关键词 → 丢
        _weak_hit("high", 0.58),          # 强向量 → 保留
        _weak_hit("kw_backed", 0.49, krank=2),  # 弱向量但有关键词背书 → 保留
        _weak_hit("kw_only", None),       # 关键词-only（vector_score=None）→ 保留
    ]
    kept = [h.chunk_id for h in _filter_weak_hits(hits, cfg)]
    assert kept == ["high", "kw_backed", "kw_only"]


def test_filter_weak_hits_drops_empty_content_or_doc() -> None:
    """幽灵 chunk（content/document_id 空）→ 丢弃，不再产生空引用。"""
    cfg = RetrievalConfig()
    ghost = _hit("ghost", 0.9, vrank=1)
    ghost.content = ""
    ghost.document_id = ""
    ghost.vector_score = 0.9
    no_doc = _hit("no_doc", 0.9, vrank=2)
    no_doc.content = "有内容"
    no_doc.document_id = ""
    no_doc.vector_score = 0.9
    ok = _hit("ok", 0.9, vrank=3)
    ok.content = "有内容"
    ok.vector_score = 0.9
    kept = [h.chunk_id for h in _filter_weak_hits([ghost, no_doc, ok], cfg)]
    assert kept == ["ok"]


def test_filter_weak_hits_cutoff_disabled() -> None:
    """weak_hit_cutoff = None 关闭弱命中过滤（脏引用过滤仍生效）。"""
    cfg = RetrievalConfig(weak_hit_cutoff=None)
    hits = [_weak_hit("low", 0.3)]
    kept = [h.chunk_id for h in _filter_weak_hits(hits, cfg)]
    assert kept == ["low"]


def test_fuse_rrf_top_k_cap() -> None:
    cfg = RetrievalConfig(top_k=3)
    vector_hits = [_hit(f"v{i}", 1.0, vrank=i) for i in range(1, 10)]
    result = fuse_rrf(vector_hits, [], cfg)
    assert len(result) == 3


def test_custom_weights_used() -> None:
    cfg = RetrievalConfig(top_k=10, rrf_k=50, vector_weight=0.5, keyword_weight=0.5)
    v = _hit("a", 1.0, vrank=1)
    k = _hit("a", 0.8, krank=2)  # same chunk twice -> both ranks count
    result = fuse_rrf([v], [k], cfg)
    assert len(result) == 1
    expected = 0.5 / 51 + 0.5 / 52
    assert result[0].score == pytest.approx(expected, abs=1e-12)


def test_hybrid_search_empty_db_is_safe() -> None:
    """hybrid_search with both arms disabled returns an empty list safely."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    db = sessionmaker(bind=engine)()
    cfg = RetrievalConfig(vector_enabled=False, keyword_enabled=False)
    result = hybrid_search(db, "kb1", "query", None, cfg)
    assert result == []
    db.close()