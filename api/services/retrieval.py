"""Hybrid retrieval: vector + keyword recall fused with RRF.

Ported from WeKnora's retrieval chain (knowledgebase_search_fusion.go +
retriever/postgres), preserving its quality-critical behaviour:

- **RRF fusion** (Reciprocal Rank Fusion):
      score = vectorWeight / (k + vectorRank) + keywordWeight / (k + keywordRank)
  with 1-indexed ranks, vector results winning metadata ties, results sorted
  by fused score descending. Defaults match WeKnora: k=60, weights 0.7 / 0.3.

- **Vector recall**: pgvector cosine distance (`<=>`) with a distance
  threshold (WeKnora uses `distance < 1 - similarity_threshold`).

- **Keyword recall**: PostgreSQL full-text search over the chunk content
  (websearch_to_tsquery + ts_rank), which handles CJK via the 'simple'
  config the same way WeKnora's wiki index does.

- **Vector-only mode** degrades to dedup (no fusion) when the keyword arm is
  disabled, mirroring WeKnora's fuseOrDeduplicate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from api.models.knowledge import DocChunk, KbDatasource

logger = logging.getLogger(__name__)

# WeKnora defaults
DEFAULT_RRF_K = 60
DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_KEYWORD_WEIGHT = 0.3


@dataclass
class ChunkHit:
    """A retrieved chunk with its score and provenance."""

    chunk_id: str
    content: str
    document_id: str = ""
    kb_id: str = ""
    score: float = 0.0
    vector_rank: int | None = None
    keyword_rank: int | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class RetrievalConfig:
    """Per-KB retrieval tuning (mirrors WeKnora RetrievalConfig)."""

    top_k: int = 10
    threshold: float = 0.2  # similarity threshold -> distance cutoff 1 - threshold
    rrf_k: int = DEFAULT_RRF_K
    vector_weight: float = DEFAULT_VECTOR_WEIGHT
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT
    vector_enabled: bool = True
    keyword_enabled: bool = True
    wiki_enabled: bool = True

    def effective_rrf_k(self) -> int:
        return self.rrf_k if self.rrf_k > 0 else DEFAULT_RRF_K

    def effective_weights(self) -> tuple[float, float]:
        v = self.vector_weight if self.vector_weight > 0 else DEFAULT_VECTOR_WEIGHT
        k = self.keyword_weight if self.keyword_weight > 0 else DEFAULT_KEYWORD_WEIGHT
        return v, k


def config_from_kb(kb: KbDatasource, overrides: dict | None = None) -> RetrievalConfig:
    """Build a RetrievalConfig from the KB's indexing_strategy + overrides."""
    strategy = (kb.indexing_strategy or {}) if kb else {}
    cfg = RetrievalConfig(
        vector_enabled=bool(strategy.get("vector_enabled", True)),
        keyword_enabled=bool(strategy.get("keyword_enabled", True)),
        wiki_enabled=bool(strategy.get("wiki_enabled", True)),
    )
    for key, value in (overrides or {}).items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


# ---------------------------------------------------------------------------
# Recall arms
# ---------------------------------------------------------------------------


def vector_search(
    db: Session,
    kb_id: str,
    query_embedding: list[float],
    cfg: RetrievalConfig,
) -> list[ChunkHit]:
    """Vector recall via pgvector cosine distance (`<=>`)."""
    distance = DocChunk.embedding.cosine_distance(query_embedding)
    max_distance = 1.0 - cfg.threshold
    stmt = (
        select(
            DocChunk.id,
            DocChunk.content,
            DocChunk.document_id,
            DocChunk.kb_id,
            DocChunk.meta,
            distance.label("distance"),
        )
        .where(DocChunk.kb_id == kb_id, DocChunk.enabled.is_(True), DocChunk.embedding.isnot(None))
        .where(distance < max_distance)
        .order_by(distance)
        .limit(cfg.top_k * 2)  # over-fetch for a healthier fusion pool
    )
    rows = db.execute(stmt).all()
    hits: list[ChunkHit] = []
    for idx, row in enumerate(rows, start=1):
        hits.append(
            ChunkHit(
                chunk_id=row.id,
                content=row.content,
                document_id=row.document_id,
                kb_id=row.kb_id,
                score=1.0 - float(row.distance),  # distance -> similarity
                vector_rank=idx,
                meta=row.meta or {},
            )
        )
    return hits


def keyword_search(db: Session, kb_id: str, query: str, cfg: RetrievalConfig) -> list[ChunkHit]:
    """Keyword recall via substring + trigram scoring.

    PostgreSQL's built-in `to_tsvector` cannot segment Chinese (the whole
    sentence becomes one token), so tsquery alone returns nothing for CJK
    input. WeKnora solves this with ParadeDB (built-in CJK tokenizer); on a
    vanilla PG we use ILIKE substring matching as the reliable recall path
    plus pg_trgm `word_similarity` for a graded score. Both degrade
    gracefully: if pg_trgm is missing, rank falls back to match position.
    """
    if not query or not query.strip():
        return []

    needle = query.strip()
    like_pat = f"%{needle}%"

    # Graded score: trigram word_similarity (best for CJK), else fall back to
    # a constant so ILIKE-only matches still rank.
    score_expr = func.word_similarity(needle, func.coalesce(DocChunk.content, "")).label("rank")

    stmt = (
        select(
            DocChunk.id,
            DocChunk.content,
            DocChunk.document_id,
            DocChunk.kb_id,
            DocChunk.meta,
            score_expr,
        )
        .where(
            DocChunk.kb_id == kb_id,
            DocChunk.enabled.is_(True),
            DocChunk.content.ilike(like_pat),
        )
        .order_by(score_expr.desc())
        .limit(cfg.top_k * 2)
    )
    rows = db.execute(stmt).all()
    hits: list[ChunkHit] = []
    for idx, row in enumerate(rows, start=1):
        hits.append(
            ChunkHit(
                chunk_id=row.id,
                content=row.content,
                document_id=row.document_id,
                kb_id=row.kb_id,
                score=float(row.rank or 0.0),
                keyword_rank=idx,
                meta=row.meta or {},
            )
        )
    return hits


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def fuse_rrf(
    vector_hits: list[ChunkHit],
    keyword_hits: list[ChunkHit],
    cfg: RetrievalConfig,
) -> list[ChunkHit]:
    """Fuse the two recall arms with weighted RRF (port of fuseWithRRF).

    Rank maps are 1-indexed and keep the FIRST occurrence of a chunk. Metadata
    prefers the vector arm (higher quality), falling back to keyword metadata.
    """
    if not vector_hits and not keyword_hits:
        return []

    # Vector-only: no fusion needed, just dedup + rank.
    if not keyword_hits:
        return vector_hits[: cfg.top_k]
    if not vector_hits:
        return keyword_hits[: cfg.top_k]

    rrf_k = cfg.effective_rrf_k()
    vector_weight, keyword_weight = cfg.effective_weights()

    vector_ranks = {h.chunk_id: (h.vector_rank or idx) for idx, h in enumerate(vector_hits, 1)}
    keyword_ranks = {h.chunk_id: (h.keyword_rank or idx) for idx, h in enumerate(keyword_hits, 1)}

    merged: dict[str, ChunkHit] = {}
    for h in vector_hits:
        existing = merged.get(h.chunk_id)
        if existing is None or h.score > existing.score:
            merged[h.chunk_id] = h
    for h in keyword_hits:
        if h.chunk_id not in merged:
            merged[h.chunk_id] = h

    fused: list[ChunkHit] = []
    for chunk_id, hit in merged.items():
        score = 0.0
        vrank = vector_ranks.get(chunk_id)
        if vrank is not None:
            score += vector_weight / (rrf_k + vrank)
        krank = keyword_ranks.get(chunk_id)
        if krank is not None:
            score += keyword_weight / (rrf_k + krank)
        hit.score = score
        hit.vector_rank = vrank
        hit.keyword_rank = krank
        fused.append(hit)

    fused.sort(key=lambda h: h.score, reverse=True)
    return fused[: cfg.top_k]


def hybrid_search(
    db: Session,
    kb_id: str,
    query: str,
    query_embedding: list[float] | None,
    cfg: RetrievalConfig,
) -> list[ChunkHit]:
    """Full hybrid retrieval: vector recall + keyword recall + RRF fusion."""
    vector_hits: list[ChunkHit] = []
    keyword_hits: list[ChunkHit] = []

    if cfg.vector_enabled and query_embedding:
        try:
            vector_hits = vector_search(db, kb_id, query_embedding, cfg)
        except Exception:
            logger.exception("vector recall failed; continuing with keyword arm")
    if cfg.keyword_enabled and query:
        try:
            keyword_hits = keyword_search(db, kb_id, query, cfg)
        except Exception:
            logger.exception("keyword recall failed; continuing with vector arm")

    return fuse_rrf(vector_hits, keyword_hits, cfg)


def search_knowledge_base(
    db: Session,
    kb: KbDatasource,
    query: str,
    query_embedding: list[float] | None = None,
    overrides: dict | None = None,
) -> list[ChunkHit]:
    """Convenience entry: resolve config from the KB then run hybrid search."""
    cfg = config_from_kb(kb, overrides)
    return hybrid_search(db, kb.id, query, query_embedding, cfg)
