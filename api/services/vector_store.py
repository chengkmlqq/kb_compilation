"""Vector store abstraction — chunk embedding storage + similarity search.

The knowledge-domain business tables live on the framework relational store;
their embeddings live in a SEPARATE vector store. Everything touching vectors
goes through this module so the backend can swap without touching callers:

- `PgVectorStore` — PostgreSQL + pgvector (current default)
- `EsVectorStore`  — Elasticsearch (reserved; raise on use until implemented)

The store works on `kb_embedding` rows keyed by chunk_id (mirroring
doc_chunk.id in the business store). Search returns (chunk_id, score) pairs
where score is cosine similarity in [0, 1] (L2-normalized vectors).
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from api.config import get_settings
from api.db import get_knowledge_sessionmaker
from api.models.knowledge import KbEmbedding

logger = logging.getLogger(__name__)

VECTOR_STORE_PG = "pg"
VECTOR_STORE_ES = "es"


def _parse_es_hosts(url: str) -> list[str]:
    """Split an ES URL list: 'http://a:9200,http://b:9200' -> [urls]."""
    return [u.strip() for u in url.split(",") if u.strip()] or ["http://127.0.0.1:9200"]


@dataclass
class VectorHit:
    """A vector-search hit (chunk + similarity score in [0, 1])."""

    chunk_id: str
    score: float


class VectorStore(ABC):
    """Contract every vector backend implements."""

    @abstractmethod
    def upsert(self, kb_id: str, chunk_id: str, vector: list[float]) -> None:
        """Insert or replace the embedding for one chunk."""

    @abstractmethod
    def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        """Remove embeddings for the given chunks (no-op for empty input)."""

    @abstractmethod
    def delete_by_kb(self, kb_id: str) -> None:
        """Remove every embedding of a knowledge base."""

    @abstractmethod
    def search(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
    ) -> list[VectorHit]:
        """Similarity search; returns hits sorted by score desc."""


class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector implementation (kb_embedding table)."""

    def __init__(self) -> None:
        self._sessionmaker = get_knowledge_sessionmaker()

    def upsert(self, kb_id: str, chunk_id: str, vector: list[float]) -> None:
        db = self._sessionmaker()
        try:
            row = (
                db.execute(select(KbEmbedding).where(KbEmbedding.chunk_id == chunk_id))
                .scalars()
                .first()
            )
            if row is None:
                db.add(
                    KbEmbedding(
                        id=uuid.uuid4().hex,
                        kb_id=kb_id,
                        chunk_id=chunk_id,
                        embedding=vector,
                        enabled=True,
                    )
                )
            else:
                row.embedding = vector
                row.kb_id = kb_id
            db.commit()
        finally:
            db.close()

    def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        db = self._sessionmaker()
        try:
            db.execute(delete(KbEmbedding).where(KbEmbedding.chunk_id.in_(chunk_ids)))
            db.commit()
        finally:
            db.close()

    def delete_by_kb(self, kb_id: str) -> None:
        db = self._sessionmaker()
        try:
            db.execute(delete(KbEmbedding).where(KbEmbedding.kb_id == kb_id))
            db.commit()
        finally:
            db.close()

    def search(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
    ) -> list[VectorHit]:
        db = self._sessionmaker()
        try:
            # cosine_distance = 1 - cosine_similarity (vectors are L2-normalized)
            distance = KbEmbedding.embedding.cosine_distance(query_vector)
            max_distance = 1.0 - threshold
            rows = db.execute(
                select(KbEmbedding.chunk_id, distance.label("dist"))
                .where(
                    KbEmbedding.kb_id == kb_id,
                    KbEmbedding.enabled.is_(True),
                )
                .where(distance < max_distance)
                .order_by(distance)
                .limit(top_k)
            ).all()
            return [
                VectorHit(chunk_id=r.chunk_id, score=1.0 - float(r.dist))
                for r in rows
            ]
        finally:
            db.close()


class EsVectorStore(VectorStore):
    """Elasticsearch implementation — dense_vector cosine kNN.

    Maps `kb_embedding` semantics onto an ES index:

    - doc `_id` = chunk_id (upsert = index with that id, same replace semantics)
    - fields: kb_id (keyword), chunk_id (keyword), enabled (boolean),
      embedding (dense_vector, index=True, similarity=cosine)
    - `search` = `_knn_search` with a kb_id+enabled bool filter; returned
      `_score` is cosine similarity, threshold applied app-side so behaviour
      matches PgVectorStore (score > threshold, sorted desc).
    """

    def __init__(self, hosts: list[str] | None = None, index: str | None = None) -> None:
        settings = get_settings()
        self.hosts = hosts or _parse_es_hosts(settings.ES_URL)
        self.index = index or settings.ES_INDEX_NAME or "kb_chunks"
        self.username = settings.ES_USERNAME or ""
        self.password = settings.ES_PASSWORD or ""
        self._client: Any | None = None
        self._dims: int | None = None

    # --- client / index plumbing -------------------------------------------
    @property
    def client(self) -> Any:
        """Lazy ES client (import elasticsearch only when actually used)."""
        if self._client is None:
            try:
                from elasticsearch import Elasticsearch
            except ImportError as exc:  # pragma: no cover - env check
                raise RuntimeError(
                    "elasticsearch package missing — add it to pyproject "
                    "(VECTOR_STORE_TYPE=es requires the ES client)"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.username:
                kwargs["basic_auth"] = (self.username, self.password)
            self._client = Elasticsearch(self.hosts, timeout=30, max_retries=2, retry_on_timeout=True, **kwargs)
        return self._client

    def _ensure_index(self, dims: int) -> None:
        if self._dims == dims:
            return
        if not self.client.indices.exists(index=self.index):
            self.client.indices.create(
                index=self.index,
                mappings={
                    "properties": {
                        "kb_id": {"type": "keyword"},
                        "chunk_id": {"type": "keyword"},
                        "enabled": {"type": "boolean"},
                        "embedding": {
                            "type": "dense_vector",
                            "dims": dims,
                            "index": True,
                            "similarity": "cosine",
                        },
                    }
                },
            )
        self._dims = dims

    # --- contract -----------------------------------------------------------
    def upsert(self, kb_id: str, chunk_id: str, vector: list[float]) -> None:
        self._ensure_index(len(vector))
        self.client.index(
            index=self.index,
            id=chunk_id,
            document={
                "kb_id": kb_id,
                "chunk_id": chunk_id,
                "enabled": True,
                "embedding": vector,
            },
        )

    def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        if not self.client.indices.exists(index=self.index):
            return
        operations = [{"delete": {"_index": self.index, "_id": cid}} for cid in chunk_ids]
        try:
            self.client.bulk(operations=operations)  # elasticsearch>=8
        except TypeError:  # pragma: no cover - client <8 fallback
            self.client.bulk(body=operations)

    def delete_by_kb(self, kb_id: str) -> None:
        if not self.client.indices.exists(index=self.index):
            return
        self.client.delete_by_query(index=self.index, query={"term": {"kb_id": kb_id}})

    def search(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
    ) -> list[VectorHit]:
        if not self.client.indices.exists(index=self.index):
            return []
        k = max(top_k * 4, 100)
        resp = self.client.knn_search(
            index=self.index,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": k,
                "num_candidates": max(top_k * 10, 100),
            },
            filter={
                "bool": {
                    "must": [
                        {"term": {"kb_id": kb_id}},
                        {"term": {"enabled": True}},
                    ]
                }
            },
            source=False,
        )
        hits: list[VectorHit] = []
        for h in resp.get("hits", {}).get("hits", []):
            # ES dense_vector cosine similarity is mapped to (1+cos)/2 (orthogonal=0.5,
            # identical=1.0); invert to the raw cosine in [-1, 1] so scores and the
            # `threshold` semantics match PgVectorStore (cosine > threshold).
            es_score = float(h.get("_score") or 0.0)
            cosine = es_score * 2.0 - 1.0
            if cosine < threshold:
                continue
            hits.append(VectorHit(chunk_id=h["_id"], score=cosine))
            if len(hits) >= top_k:
                break
        return hits


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Process-wide vector-store singleton (type from VECTOR_STORE_TYPE)."""
    global _store
    if _store is None:
        settings = get_settings()
        kind = (settings.VECTOR_STORE_TYPE or VECTOR_STORE_PG).strip().lower()
        if kind == VECTOR_STORE_ES:
            _store = EsVectorStore()
        else:
            _store = PgVectorStore()
        logger.info("vector store backend: %s", type(_store).__name__)
    return _store


def reset_vector_store() -> None:
    """Clear the cached store (tests / config change)."""
    global _store
    _store = None


__all__ = [
    "EsVectorStore",
    "PgVectorStore",
    "VectorHit",
    "VectorStore",
    "get_vector_store",
    "reset_vector_store",
    "VECTOR_STORE_ES",
    "VECTOR_STORE_PG",
]
