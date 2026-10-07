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

from sqlalchemy import or_, select
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


def _global_retrieval_overrides() -> dict:
    """Global retrieval defaults from modo_dim RETRIEVAL_CONFIG (best-effort).

    Mirrors WeKnora tenant-level RetrievalSettings: platform-wide defaults
    live in the DB and every KB inherits them unless it overrides them.
    """
    try:
        from api.db import get_sessionmaker
        from api.services.system_config import retrieval_config_dict

        db = get_sessionmaker()()
        try:
            return retrieval_config_dict(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - fall back to dataclass defaults
        return {}


def config_from_kb(kb: KbDatasource, overrides: dict | None = None) -> RetrievalConfig:
    """Build a RetrievalConfig: global defaults < KB strategy < overrides.

    The global layer is the tenant-level config stored in
    modo_dim(RETRIEVAL_CONFIG), aligned with WeKnora's RetrievalSettings.
    """
    from api.services.kb_config import normalize_indexing_strategy

    strategy = normalize_indexing_strategy((kb.indexing_strategy or {}) if kb else {})
    glob = _global_retrieval_overrides()
    base = RetrievalConfig(**{
        k: v for k, v in glob.items() if k in RetrievalConfig.__dataclass_fields__
    })
    cfg = RetrievalConfig(
        top_k=base.top_k,
        threshold=base.threshold,
        rrf_k=base.rrf_k,
        vector_weight=base.vector_weight,
        keyword_weight=base.keyword_weight,
        vector_enabled=bool(strategy.get("vector_enabled", base.vector_enabled)),
        keyword_enabled=bool(strategy.get("keyword_enabled", base.keyword_enabled)),
        wiki_enabled=bool(strategy.get("wiki_enabled", base.wiki_enabled)),
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
    vector_store_id: str | None = None,
) -> list[ChunkHit]:
    """Vector recall through the vector store; returns [] on any backend error.

    Metadata (content, document_id, meta) is filled in by the caller-facing
    hybrid_search from the business store — the vector store only knows ids.
    `vector_store_id` optionally routes recall to a resource-configured vector
    backend (data-source management); None uses the platform default.
    """
    from api.services.vector_store import get_vector_store

    try:
        hits = get_vector_store(vector_store_id).search(
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


# 查询切词时过滤的助词/疑问词（中文无分词器，用停用词表 + 2-gram 滑窗）
_KEYWORD_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "及", "等", "吗", "呢", "吧", "啊",
    "什么", "怎么", "怎样", "多少", "哪些", "哪个", "如何", "请问", "请", "我", "你",
    "他", "她", "它", "有", "没", "不", "要", "可以", "应该", "能否", "是否",
}


def _split_terms(query: str) -> list[str]:
    """把查询拆成检索词（无外部分词器）。

    规则：
    - 英文/数字按空白与标点切分为单词（≥2 字符才保留）；
    - 中文连续串按标点切段，去掉停用词后：段长 ≤ 6 字保留整段，
      段长 > 6 字用 2-gram 滑窗（覆盖任意二字词，代价是少量噪音词）；
    - 结果去重，保持原序。
    """
    import re

    if not query or not query.strip():
        return []

    # 统一分隔：空白与常见中文标点都切成独立段
    parts = re.split(r"[\s,，。；;、!！?？:：""''《》<>（）()\[\]{}|/\\_\-]+", query.strip())
    terms: list[str] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 英文/数字词（纯 ASCII 单词）
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", part):
            if len(part) >= 2 and part.lower() not in terms:
                terms.append(part.lower())
            continue
        # 中文：去掉停用词后处理
        seg = part
        if seg in _KEYWORD_STOPWORDS:
            continue
        if len(seg) <= 6:
            # 段完全由停用词拼接而成（如「是什么」）→ 无检索价值
            if all(_is_stopword_char(c) for c in seg):
                continue
            if seg not in terms:
                terms.append(seg)
        else:
            # 2-gram 滑窗：连续两字组合
            for i in range(len(seg) - 1):
                gram = seg[i : i + 2]
                if gram not in _KEYWORD_STOPWORDS and gram not in terms:
                    terms.append(gram)
    return terms


def _is_stopword_char(ch: str) -> bool:
    """单字是否命中停用词表（含作为组合词组成部分的单字）。"""
    if ch in _KEYWORD_STOPWORDS:
        return True
    return any(ch in w for w in _KEYWORD_STOPWORDS if len(w) > 1)


def _keyword_score(keywords: list[str], content: str) -> float:
    """多关键词相关性评分（方言中立，app-side）。

    - 覆盖率：命中的关键词数 / 关键词总数（召回的核心信号）；
    - 位置：最早命中的位置越靠前越好；
    - 频次：命中词在内容中的总出现次数（归一化）。
    返回 (0, 1] 区间，供 RRF 排序（只需 RANK 序）。
    """
    if not keywords or not content:
        return 0.0
    text_l = content.lower()

    hit_terms = [k for k in keywords if text_l.count(k) > 0]
    if not hit_terms:
        return 0.0

    coverage = len(hit_terms) / len(keywords)

    # 最早命中位置（相对内容开头）
    first_pos = min(text_l.find(k) for k in hit_terms)
    position_weight = 1.0 / (1.0 + first_pos / 100.0)

    # 总频次（去重计数，限制上限）
    total_occurrences = sum(text_l.count(k) for k in hit_terms)
    freq_weight = min(1.0, total_occurrences / 5.0)

    # 长内容轻微惩罚（避免大 chunk 无脑占优）
    length_penalty = 1.0 / (1.0 + max(0, len(content) - 200) / 1000.0)

    return round(coverage * (0.5 * position_weight + 0.3 * freq_weight + 0.2) * length_penalty, 4)


def keyword_search(db: Session, kb_id: str, query: str, cfg: RetrievalConfig) -> list[ChunkHit]:
    """Keyword recall via multi-term substring matching (`ilike` OR).

    Query is split into terms (`_split_terms`), then chunks matching ANY term
    are recalled with portable `ilike`/`like` (works on MySQL, PG, SQLite);
    ranking is app-side by `_keyword_score` (no PG-specific extensions).
    """
    if not query or not query.strip():
        return []

    keywords = _split_terms(query)
    if not keywords:
        return []

    # 关键词去重（_split_terms 已去重；防御重复）
    keywords = list(dict.fromkeys(keywords))

    conditions = [DocChunk.content.ilike(f"%{k}%") for k in keywords]
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
            or_(*conditions),
        )
        .limit(cfg.top_k * 8)  # over-fetch; app-side scoring then trims
    )
    rows = db.execute(stmt).all()

    scored: list[ChunkHit] = []
    for row in rows:
        content = row.content or ""
        score = _keyword_score(keywords, content)
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
    """Fill content/document_id/meta for vector-only hits from the business store.

    Parent-child chunking: when a chunk carries `parent_content` in its meta
    (child matched, parent returned for context — mirroring WeKnora), the hit's
    content is promoted to the parent text.
    """
    missing = [h for h in hits if not h.content]
    rows_by_id = {}
    if missing:
        ids = [h.chunk_id for h in missing]
        rows = db.execute(
            select(DocChunk.id, DocChunk.content, DocChunk.document_id, DocChunk.meta).where(
                DocChunk.id.in_(ids)
            )
        ).all()
        rows_by_id = {r.id: r for r in rows}
        for h in missing:
            row = rows_by_id.get(h.chunk_id)
            if row is not None:
                h.content = row.content
                h.document_id = row.document_id
                h.meta = row.meta or {}
    # promote parent content for parent-child hits (both arms)
    for h in hits:
        parent = (h.meta or {}).get("parent_content")
        if parent:
            h.content = str(parent)
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
        from api.models.knowledge import KbDatasource

        kb_row = db.get(KbDatasource, kb_id)
        vector_hits = vector_search(
            kb_id,
            query_embedding,
            cfg,
            vector_store_id=kb_row.vector_store_id if kb_row else None,
        )
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
