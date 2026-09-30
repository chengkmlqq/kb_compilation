"""Hybrid retrieval: vector + keyword recall fused with RRF.

Ported from WeKnora's retrieval chain (knowledgebase_search_fusion.go +
retriever/postgres), preserving its quality-critical behaviour:

- **RRF fusion** (Reciprocal Rank Fusion):
      score = vectorWeight / (k + vectorRank) + keywordWeight / (k + keywordRank)
  with 1-indexed ranks, vector results winning metadata ties, results sorted
  by fused score descending. Defaults match WeKnora: k=60, weights 0.7 / 0.3.

Storage separation:
- The vector arm queries the dedicated vector store (api.services.vector_store,
  PG pgvector today; the backend can swap to ES). Vectors live in `kb_embedding`
  (knowledge engine), not in the business store.
- The keyword arm queries the business store (framework relational DB). It is
  pure portable SQL: `ilike`/`like` substring matching (works on MySQL, PG and
  SQLite) with an application-side relevance score, so no PG-specific
  extension (pg_trgm) is required and the platform can switch relational DBs.

- **Vector-only mode** degrades to dedup (no fusion) when the keyword arm is
  disabled, mirroring WeKnora's fuseOrDeduplicate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
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
# Vector arm (delegates to the swappable vector store)
# ---------------------------------------------------------------------------


def vector_search(
    kb_id: str,
    query_embedding: list[float],
    cfg: RetrievalConfig,
) -> list[ChunkHit]:
    """Vector recall through the vector store; returns [] on any backend error.

    Metadata (content, document_id, meta) is filled in by the caller-facing
    hybrid_search from the business store — the vector store only knows ids.
    """
    from api.services.vector_store import get_vector_store

    try:
        hits = get_vector_store().search(
            kb_id,
            query_embedding,
            top_k=cfg.top_k,
            threshold=cfg.threshold,
        )
    except Exception:
        logger.exception("vector recall failed; continuing with keyword arm")
        return []

    out: list[ChunkHit] = []
    for rank, hit in enumerate(hits, start=1):
        out.append(
            ChunkHit(
                chunk_id=hit.chunk_id,
                content="",  # hydrated from the business store by hybrid_search
                kb_id=kb_id,
                score=hit.score,
                vector_rank=rank,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Keyword arm (portable SQL + app-side scoring)
# ---------------------------------------------------------------------------


def _keyword_score(query: str, content: str) -> float:
    """Relevance of a keyword match, computed app-side (dialect-neutral).

    Weights: earlier first-occurrence (closer to the top is more relevant) and
    more total occurrences rank higher; length-normalized so a long chunk is
    not automatically preferred. Any value in (0, 1] is fine — RRF only needs
    the RANK order, and the score surfaces as a display number.
    """
    if not query or not content:
        return 0.0
    q = query.strip().lower()
    if not q:
        return 0.0
    text_l = content.lower()
    occurrences = text_l.count(q)
    if occurrences == 0:
        return 0.0
    pos = text_l.find(q)
    position_weight = 1.0 / (1.0 + pos / 100.0)  # earlier match -> closer to 1
    freq_weight = min(1.0, occurrences / 5.0)  # more occurrences -> closer to 1
    length_penalty = 1.0 / (1.0 + max(0, len(content) - len(query)) / 500.0)
    return round(position_weight * freq_weight * length_penalty, 4)


def keyword_search(db: Session, kb_id: str, query: str, cfg: RetrievalConfig) -> list[ChunkHit]:
    """Keyword recall via portable substring matching (`ilike` on the business
    store). All dialects (MySQL, PG, SQLite) implement `ilike`/`like`; ranking
    is done app-side by `_keyword_score` (no PG-specific extensions needed)."""
    if not query or not query.strip():
        return []

    needle = query.strip()
    like_pat = f"%{needle}%"

    stmt = (
        select(
            DocChunk.id,
            DocChunk.content,
            DocChunk.document_id,
            DocChunk.kb_id,
            DocChunk.meta,
        )
        .where(
            DocChunk.kb_id == kb_id,
            DocChunk.enabled.is_(True),
            DocChunk.content.ilike(like_pat),
        )
        .limit(cfg.top_k * 4)  # over-fetch; app-side scoring then trims
    )
    rows = db.execute(stmt).all()

    scored: list[ChunkHit] = []
    for row in rows:
        content = row.content or ""
        score = _keyword_score(needle, content)
        if score <= 0:
            continue
        scored.append(
            ChunkHit(
                chunk_id=row.id,
                content=content,
                document_id=row.document_id,
                kb_id=row.kb_id,
                score=score,
                meta=row.meta or {},
            )
        )

    # Rank by score desc (app-side, dialect-neutral), then assign ranks.
    scored.sort(key=lambda h: h.score, reverse=True)
    hits: list[ChunkHit] = []
    for idx, hit in enumerate(scored[: cfg.top_k], start=1):
        hit.keyword_rank = idx
        hits.append(hit)
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


def hydrate_metadata(db: Session, hits: list[ChunkHit]) -> list[ChunkHit]:
    """Fill content/document_id/meta for vector-only hits from the business store."""
    missing = [h for h in hits if not h.content]
    if not missing:
        return hits
    ids = [h.chunk_id for h in missing]
    rows = db.execute(
        select(DocChunk.id, DocChunk.content, DocChunk.document_id, DocChunk.meta).where(
            DocChunk.id.in_(ids)
        )
    ).all()
    by_id = {r.id: r for r in rows}
    for h in missing:
        row = by_id.get(h.chunk_id)
        if row is not None:
            h.content = row.content
            h.document_id = row.document_id
            h.meta = row.meta or {}
    return hits


def hybrid_search(
    db: Session,
    kb_id: str,
    query: str,
    query_embedding: list[float] | None,
    cfg: RetrievalConfig,
) -> list[ChunkHit]:
    """Full hybrid retrieval: vector recall + keyword recall + RRF fusion.

    - `db` is a session on the BUSINESS store (chunks live there).
    - `query_embedding` drives the vector arm via the vector store.
    """
    vector_hits: list[ChunkHit] = []
    keyword_hits: list[ChunkHit] = []

    if cfg.vector_enabled and query_embedding:
        vector_hits = vector_search(kb_id, query_embedding, cfg)
    if cfg.keyword_enabled and query:
        keyword_hits = keyword_search(db, kb_id, query, cfg)

    fused = fuse_rrf(vector_hits, keyword_hits, cfg)
    return hydrate_metadata(db, fused)


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
