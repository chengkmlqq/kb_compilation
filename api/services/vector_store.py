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

from sqlalchemy import delete, func, select

from api.config import get_settings
from api.db import get_knowledge_sessionmaker, get_sessionmaker
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
    # 切片增强元数据（2026-10-25 对齐 duowen）：检索命中时从向量介质带回，
    # 供加权重排 / 引用展示（关键词/问题/摘要/标题）。无增强时为 None。
    enhance: dict | None = None


class VectorStore(ABC):
    """Contract every vector backend implements."""

    @abstractmethod
    def upsert(self, kb_id: str, chunk_id: str, vector: list[float]) -> None:
        """Insert or replace the embedding for one chunk."""

    # 2026-10-25 增强入库：与向量同介质存切片增强元数据（duowen 对齐）。
    # 默认实现退回 upsert（旧后端 / 调用方未传增强时行为不变）。
    def upsert_with_enhance(
        self, kb_id: str, chunk_id: str, vector: list[float], enhance: dict | None = None
    ) -> None:
        if enhance:
            self.upsert_enhance(kb_id, chunk_id, vector, enhance)
        else:
            self.upsert(kb_id, chunk_id, vector)

    # 子类实现：向量 + 增强字段一起写（PG: enhance_meta 列；ES: doc 字段）。
    def upsert_enhance(
        self, kb_id: str, chunk_id: str, vector: list[float], enhance: dict
    ) -> None:  # pragma: no cover - abstract-ish default
        raise NotImplementedError

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
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        """Similarity search; returns hits sorted by score desc.

        `chunk_ids` 非空时只在该切片集合内检索（agent 多跳深读：锁定
        某个 document_id 后，向量臂通过该文档的 chunk_id 集合过滤）。
        """

    # 2026-10-25 增强检索：同 search 语义，但命中带回 enhance 元数据
    # （search 默认不携带；需要增强字段做加权/展示时调用此方法）。
    def search_enhanced(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        return self.search(kb_id, query_vector, top_k, threshold, chunk_ids=chunk_ids)

    # 2026-10-25 批量读增强元数据（重嵌入/重建向量时保留增强字段）。
    # 返回 {chunk_id: enhance_dict}；无增强的 chunk 不出现。默认空实现。
    def read_enhances(self, chunk_ids: list[str]) -> dict[str, dict]:
        return {}

    @abstractmethod
    def list_chunk_ids(self, kb_id: str) -> list[str]:
        """枚举某 KB 的全部 chunk_id（切片核对用，不依赖具体后端）。"""

    @abstractmethod
    def count(self, kb_id: str) -> int:
        """某 KB 的切片向量数量（核对统计用）。"""


class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector implementation (kb_embedding table).

    `url=None` binds to the platform-wide knowledge engine
    (KNOWLEDGE_DATABASE_URL); a custom URL is used when the store backs a
    resource-configured knowledge base (data-source management → PostgreSQL).
    """

    def __init__(self, url: str | None = None) -> None:
        if url:
            self._sessionmaker = _pg_sessionmaker_for(url)
        else:
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

    def upsert_enhance(
        self, kb_id: str, chunk_id: str, vector: list[float], enhance: dict
    ) -> None:
        """向量 + 增强元数据一起写（enhance 落 enhance_meta 列，同事务）。"""
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
                        enhance_meta=enhance,
                    )
                )
            else:
                row.embedding = vector
                row.kb_id = kb_id
                row.enhance_meta = enhance
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
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        db = self._sessionmaker()
        try:
            # cosine_distance = 1 - cosine_similarity (vectors are L2-normalized)
            distance = KbEmbedding.embedding.cosine_distance(query_vector)
            max_distance = 1.0 - threshold
            stmt = (
                select(KbEmbedding.chunk_id, distance.label("dist"))
                .where(
                    KbEmbedding.kb_id == kb_id,
                    KbEmbedding.enabled.is_(True),
                )
                .where(distance < max_distance)
                .order_by(distance)
                .limit(top_k)
            )
            if chunk_ids:
                stmt = stmt.where(KbEmbedding.chunk_id.in_(chunk_ids))
            rows = db.execute(stmt).all()
            return [
                VectorHit(chunk_id=r.chunk_id, score=1.0 - float(r.dist))
                for r in rows
            ]
        finally:
            db.close()

    def search_enhanced(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        """同 search，命中带回 enhance_meta（增强加权/引用展示用）。"""
        db = self._sessionmaker()
        try:
            distance = KbEmbedding.embedding.cosine_distance(query_vector)
            max_distance = 1.0 - threshold
            stmt = (
                select(
                    KbEmbedding.chunk_id,
                    distance.label("dist"),
                    KbEmbedding.enhance_meta,
                )
                .where(
                    KbEmbedding.kb_id == kb_id,
                    KbEmbedding.enabled.is_(True),
                )
                .where(distance < max_distance)
                .order_by(distance)
                .limit(top_k)
            )
            if chunk_ids:
                stmt = stmt.where(KbEmbedding.chunk_id.in_(chunk_ids))
            rows = db.execute(stmt).all()
            return [
                VectorHit(
                    chunk_id=r.chunk_id,
                    score=1.0 - float(r.dist),
                    enhance=(dict(r.enhance_meta) if r.enhance_meta else None),
                )
                for r in rows
            ]
        finally:
            db.close()

    def read_enhances(self, chunk_ids: list[str]) -> dict[str, dict]:
        """批量读 enhance_meta（重嵌入保留增强字段用）。"""
        if not chunk_ids:
            return {}
        db = self._sessionmaker()
        try:
            rows = db.execute(
                select(KbEmbedding.chunk_id, KbEmbedding.enhance_meta).where(
                    KbEmbedding.chunk_id.in_(chunk_ids)
                )
            ).all()
            return {
                r.chunk_id: dict(r.enhance_meta)
                for r in rows
                if r.enhance_meta
            }
        finally:
            db.close()

    def list_chunk_ids(self, kb_id: str) -> list[str]:
        db = self._sessionmaker()
        try:
            rows = db.execute(
                select(KbEmbedding.chunk_id).where(
                    KbEmbedding.kb_id == kb_id,
                    KbEmbedding.enabled.is_(True),
                )
            ).scalars().all()
            return list(rows)
        finally:
            db.close()

    def count(self, kb_id: str) -> int:
        db = self._sessionmaker()
        try:
            return int(
                db.execute(
                    select(func.count()).select_from(KbEmbedding).where(
                        KbEmbedding.kb_id == kb_id,
                        KbEmbedding.enabled.is_(True),
                    )
                ).scalar() or 0
            )
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

    def __init__(
        self,
        hosts: list[str] | None = None,
        username: str | None = None,
        password: str | None = None,
        index: str | None = None,
    ) -> None:
        settings = get_settings()
        self.hosts = hosts or _parse_es_hosts(settings.ES_URL)
        self.index = index or settings.ES_INDEX_NAME or "kb_chunks"
        self.username = username or settings.ES_USERNAME or ""
        self.password = password or settings.ES_PASSWORD or ""
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
                        # ── 切片增强元数据（2026-10-25 对齐 duowen）──
                        # 与向量同 doc 存储：标题/摘要/文档关键词/切片关键词/问题。
                        # keywords/questions 存 text 便于 query_string 加权命中
                        # （title^10 / keywords^30 / questions^20，检索组装时用）。
                        "enhance_title": {"type": "text", "analyzer": "standard"},
                        "enhance_abstract": {"type": "text"},
                        "enhance_doc_keywords": {"type": "keyword"},
                        "enhance_keywords": {"type": "keyword"},
                        "enhance_questions": {"type": "text"},
                        "enhance_pagerank": {"type": "float"},
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

    def upsert_enhance(
        self, kb_id: str, chunk_id: str, vector: list[float], enhance: dict
    ) -> None:
        """向量 + 增强字段同 doc 写入（enhance 扁平为 enhance_* 字段）。"""
        self._ensure_index(len(vector))
        doc: dict[str, Any] = {
            "kb_id": kb_id,
            "chunk_id": chunk_id,
            "enabled": True,
            "embedding": vector,
        }
        enhance = dict(enhance or {})
        if enhance.get("title"):
            doc["enhance_title"] = str(enhance["title"])
        if enhance.get("abstract"):
            doc["enhance_abstract"] = str(enhance["abstract"])
        if enhance.get("doc_keywords"):
            doc["enhance_doc_keywords"] = list(enhance["doc_keywords"])
        if enhance.get("chunk_keywords"):
            doc["enhance_keywords"] = list(enhance["chunk_keywords"])
        if enhance.get("chunk_questions"):
            doc["enhance_questions"] = list(enhance["chunk_questions"])
        if enhance.get("pagerank"):
            doc["enhance_pagerank"] = float(enhance["pagerank"])
        self.client.index(index=self.index, id=chunk_id, document=doc)

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
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        if not self.client.indices.exists(index=self.index):
            return []
        k = max(top_k * 4, 100)
        must: list[dict] = [
            {"term": {"kb_id": kb_id}},
            {"term": {"enabled": True}},
        ]
        if chunk_ids:
            must.append({"terms": {"chunk_id": chunk_ids}})
        resp = self.client.knn_search(
            index=self.index,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": k,
                "num_candidates": max(top_k * 10, 100),
            },
            filter={"bool": {"must": must}},
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

    def search_enhanced(
        self,
        kb_id: str,
        query_vector: list[float],
        top_k: int = 10,
        threshold: float = 0.2,
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        """同 search，命中带回 enhance_* 字段（组装回 enhance dict）。"""
        if not self.client.indices.exists(index=self.index):
            return []
        k = max(top_k * 4, 100)
        must: list[dict] = [
            {"term": {"kb_id": kb_id}},
            {"term": {"enabled": True}},
        ]
        if chunk_ids:
            must.append({"terms": {"chunk_id": chunk_ids}})
        resp = self.client.knn_search(
            index=self.index,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": k,
                "num_candidates": max(top_k * 10, 100),
            },
            filter={"bool": {"must": must}},
            source=True,
        )
        hits: list[VectorHit] = []
        for h in resp.get("hits", {}).get("hits", []):
            es_score = float(h.get("_score") or 0.0)
            cosine = es_score * 2.0 - 1.0
            if cosine < threshold:
                continue
            src = h.get("_source") or {}
            enhance: dict[str, Any] = {}
            if src.get("enhance_title"):
                enhance["title"] = src["enhance_title"]
            if src.get("enhance_abstract"):
                enhance["abstract"] = src["enhance_abstract"]
            if src.get("enhance_doc_keywords"):
                enhance["doc_keywords"] = list(src["enhance_doc_keywords"])
            if src.get("enhance_keywords"):
                enhance["chunk_keywords"] = list(src["enhance_keywords"])
            if src.get("enhance_questions"):
                enhance["chunk_questions"] = list(src["enhance_questions"])
            if src.get("enhance_pagerank") is not None:
                enhance["pagerank"] = float(src["enhance_pagerank"])
            hits.append(
                VectorHit(
                    chunk_id=h["_id"],
                    score=cosine,
                    enhance=enhance or None,
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def read_enhances(self, chunk_ids: list[str]) -> dict[str, dict]:
        """mget 批量读增强字段（重嵌入保留增强字段用）。"""
        if not chunk_ids:
            return {}
        if not self.client.indices.exists(index=self.index):
            return {}
        resp = self.client.mget(index=self.index, ids=chunk_ids)
        out: dict[str, dict] = {}
        for doc in resp.get("docs", []):
            src = doc.get("_source") or {}
            enhance: dict[str, Any] = {}
            if src.get("enhance_title"):
                enhance["title"] = src["enhance_title"]
            if src.get("enhance_abstract"):
                enhance["abstract"] = src["enhance_abstract"]
            if src.get("enhance_doc_keywords"):
                enhance["doc_keywords"] = list(src["enhance_doc_keywords"])
            if src.get("enhance_keywords"):
                enhance["chunk_keywords"] = list(src["enhance_keywords"])
            if src.get("enhance_questions"):
                enhance["chunk_questions"] = list(src["enhance_questions"])
            if src.get("enhance_pagerank") is not None:
                enhance["pagerank"] = float(src["enhance_pagerank"])
            if enhance:
                out[doc.get("_id", "")] = enhance
        return out

    def list_chunk_ids(self, kb_id: str) -> list[str]:
        if not self.client.indices.exists(index=self.index):
            return []
        ids: list[str] = []
        resp = self.client.search(
            index=self.index,
            query={"term": {"kb_id": kb_id}},
            source=["chunk_id"],
            size=10000,
        )
        for h in resp.get("hits", {}).get("hits", []):
            src = h.get("_source") or {}
            if src.get("enabled", True) is not False:
                ids.append(src.get("chunk_id") or h.get("_id"))
        return ids

    def count(self, kb_id: str) -> int:
        if not self.client.indices.exists(index=self.index):
            return 0
        resp = self.client.count(index=self.index, query={"term": {"kb_id": kb_id}})
        return int(resp.get("count") or 0)


_store: VectorStore | None = None
_stores: dict[str, VectorStore] = {}


def _pg_sessionmaker_for(url: str):
    """Build a pgvector sessionmaker for a resource-configured PG URL."""
    import sqlalchemy
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    connect_args: dict = {}
    if settings.SCHEMA_NAME and settings.SCHEMA_NAME != "public":
        connect_args["options"] = f"-csearch_path={settings.SCHEMA_NAME},public"
    engine = sqlalchemy.create_engine(
        url,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_MAX_CONNECTIONS,
        max_overflow=0,
        connect_args=connect_args,
        future=True,
    )
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _normalize_pg_url(url: str, acct: str | None, auth: str | None) -> str:
    """Convert a resource's PG URL (jdbc: or postgresql:) to a SQLAlchemy URL
    with credentials embedded."""
    from urllib.parse import quote_plus

    raw = (url or "").strip()
    if raw.startswith("jdbc:postgresql://"):
        raw = "postgresql://" + raw[len("jdbc:postgresql://"):]
    base = raw or ""
    acct = (acct or "").strip()
    auth = (auth or "").strip()
    if (
        base.startswith("postgresql://")
        and acct
        and "@" not in base.split("://", 1)[1].split("/", 1)[0]
    ):
        if auth:
            base = f"postgresql://{quote_plus(acct)}:{quote_plus(auth)}@{base.split('://', 1)[1]}"
        else:
            base = f"postgresql://{quote_plus(acct)}@{base.split('://', 1)[1]}"
    if base.startswith("postgresql://") and not base.startswith("postgresql+psycopg2://"):
        base = "postgresql+psycopg2://" + base[len("postgresql://"):]
    return base


def _es_hosts_from_ds(ds_url: str | None, ds_conf: dict) -> list[str]:
    """Build ES host list from a datasource row: ds_conf protocol/host/port win,
    else the row's url."""
    protocol = str(ds_conf.get("protocol") or "http").strip()
    host = str(ds_conf.get("host") or "").strip()
    port = str(ds_conf.get("port") or "").strip()
    if host:
        return [f"{protocol}://{host}:{port}" if port else f"{protocol}://{host}"]
    url = (ds_url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return [url] if url else _parse_es_hosts(get_settings().ES_URL)


def _store_from_datasource(ds_id: str) -> VectorStore | None:
    """Instantiate the vector store bound to a data-source resource.

    Returns None when the resource is missing/unsupported so callers can fall
    back to the global store instead of failing writes/reads.
    """
    try:
        from api.services.datasource import _parse_ds_conf, _reveal_secret
        from api.models.framework import Datasource
        from sqlalchemy import select

        db = get_sessionmaker()()
        try:
            row = (
                db.execute(select(Datasource).where(Datasource.id == ds_id))
                .scalars()
                .first()
            )
        finally:
            db.close()
        if row is None:
            logger.warning("vector datasource %s not found; falling back to global store", ds_id)
            return None
        ds_type = (row.ds_type or "").strip().lower()
        conf = _parse_ds_conf(row.ds_conf)
        acct = (row.ds_acct or "").strip() or None
        auth = _reveal_secret(row.ds_auth)
        if ds_type in ("elasticsearch", "vector_es", "es"):
            hosts = _es_hosts_from_ds(row.url, conf)
            index = str(conf.get("index") or get_settings().ES_INDEX_NAME or "kb_chunks")
            return EsVectorStore(hosts=hosts, username=acct, password=auth, index=index)
        if ds_type in ("postgresql", "pg", "postgres"):
            pg_url = _normalize_pg_url(row.url, acct, auth)
            if not pg_url:
                logger.warning("vector datasource %s has no url; falling back to global store", ds_id)
                return None
            return PgVectorStore(url=pg_url)
        logger.warning(
            "vector datasource %s has unsupported type %s; falling back to global store",
            ds_id, row.ds_type,
        )
        return None
    except Exception:  # noqa: BLE001 - resource errors must never break ingest/recall
        logger.exception("failed to build vector store from datasource %s; falling back", ds_id)
        return None


def get_vector_store(vector_store_id: str | None = None) -> VectorStore:
    """Process-wide vector-store singleton (type from VECTOR_STORE_TYPE).

    When `vector_store_id` names a data-source resource (data-source
    management → Elasticsearch/PostgreSQL), returns a per-resource cached
    instance so different knowledge bases can target different vector
    backends. Falls back to the global store if the resource is missing,
    unsupported, or fails to build — writes/recall never break.
    """
    global _store
    if vector_store_id:
        cached = _stores.get(vector_store_id)
        if cached is not None:
            return cached
        built = _store_from_datasource(vector_store_id)
        if built is not None:
            _stores[vector_store_id] = built
            logger.info("vector store for datasource %s: %s", vector_store_id, type(built).__name__)
            return built
        logger.warning("vector store datasource %s unavailable; using global store", vector_store_id)
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
