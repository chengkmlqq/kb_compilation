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

from sqlalchemy import delete, select

from api.config import get_settings
from api.db import get_knowledge_sessionmaker
from api.models.knowledge import KbEmbedding

logger = logging.getLogger(__name__)

VECTOR_STORE_PG = "pg"
VECTOR_STORE_ES = "es"


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
    """Elasticsearch implementation — reserved.

    Kept as a stub so the platform can adopt ES without changing callers.
    Until configured, every operation raises (the vector arm is then disabled
    by callers and keyword-only search continues to work).
    """

    def __init__(self, hosts: list[str] | None = None, index: str = "kb_chunks") -> None:
        self.hosts = hosts or []
        self.index = index

    def _unavailable(self) -> RuntimeError:
        return RuntimeError(
            "EsVectorStore is reserved (not implemented). Configure VECTOR_STORE_TYPE=pg "
            "or implement the ES adapter (see api/services/vector_store.py)."
        )

    def upsert(self, kb_id: str, chunk_id: str, vector: list[float]) -> None:
        raise self._unavailable()

    def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        raise self._unavailable()

    def delete_by_kb(self, kb_id: str) -> None:
        raise self._unavailable()

    def search(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
    ) -> list[VectorHit]:
        raise self._unavailable()


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
