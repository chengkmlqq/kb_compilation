"""向量切片核对服务（ChunkVerify）——MySQL 切片 ↔ 向量库 一致性核对。

设计：仅依赖 VectorStore 抽象接口（api.services.vector_store），不感知
底层是 pgvector 还是 Elasticsearch——后端可换、核对逻辑不变。

核对维度：
- missing_in_vector：MySQL 有切片、向量库没有对应向量（切片未向量化/向量丢失）
- orphan_vectors：向量库有向量、MySQL 无对应切片（孤儿向量，如 KB 重建残留）

修复动作（worker 任务内执行，避免长任务阻塞 API）：
- 清孤儿：VectorStore.delete_by_chunks
- 重嵌入缺失：doc_chunk.content → EmbeddingClient.embed_texts → upsert
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def verify_kb(kb_id: str, sample_limit: int = 20) -> dict[str, Any]:
    """核对单个 KB。返回报告（纯读取，不写）。"""
    from sqlalchemy import select

    from api.db import get_sessionmaker
    from api.models.knowledge import DocChunk
    from api.services.vector_store import get_vector_store

    store = get_vector_store()
    vec_ids = set(store.list_chunk_ids(kb_id))

    db = get_sessionmaker()()
    try:
        rows = db.execute(
            select(DocChunk.id).where(
                DocChunk.kb_id == kb_id,
                DocChunk.enabled.is_(True),
            )
        ).scalars().all()
    finally:
        db.close()
    doc_ids = set(rows)

    missing_in_vector = sorted(doc_ids - vec_ids)      # 切片未向量化
    orphan_vectors = sorted(vec_ids - doc_ids)         # 孤儿向量
    return {
        "kb_id": kb_id,
        "doc_chunks": len(doc_ids),
        "vector_chunks": len(vec_ids),
        "missing_in_vector": len(missing_in_vector),
        "orphan_vectors": len(orphan_vectors),
        "missing_samples": missing_in_vector[:sample_limit],
        "orphan_samples": orphan_vectors[:sample_limit],
        "ok": not missing_in_vector and not orphan_vectors,
    }


def fix_kb(kb_id: str, fix_orphans: bool = True, fix_missing: bool = True) -> dict[str, Any]:
    """修复单个 KB：清孤儿向量 + 重嵌入缺失切片（worker 内调用）。"""
    from sqlalchemy import select

    from api.db import get_sessionmaker
    from api.models.knowledge import DocChunk
    from api.services.embedding import get_embedding_client
    from api.services.vector_store import get_vector_store

    report = verify_kb(kb_id, sample_limit=200)
    store = get_vector_store()
    result: dict[str, Any] = {"kb_id": kb_id, "removed_orphans": 0, "embedded_missing": 0}
    result.update({k: report[k] for k in ("doc_chunks", "vector_chunks", "missing_in_vector", "orphan_vectors")})

    # 1) 清孤儿
    if fix_orphans and report["orphan_vectors"] > 0:
        ids = report["orphan_samples"]
        # sample_limit 截断了——孤儿也可能是超大集合，用完整集合删
        vec_ids = set(store.list_chunk_ids(kb_id))
        db = get_sessionmaker()()
        try:
            doc_ids = set(
                db.execute(
                    select(DocChunk.id).where(
                        DocChunk.kb_id == kb_id,
                        DocChunk.enabled.is_(True),
                    )
                ).scalars().all()
            )
        finally:
            db.close()
        orphan_all = sorted(vec_ids - doc_ids)
        if orphan_all:
            store.delete_by_chunks(orphan_all)
            result["removed_orphans"] = len(orphan_all)
            logger.info("chunk verify fix kb=%s removed %d orphans", kb_id, len(orphan_all))

    # 2) 重嵌入缺失切片
    if fix_missing and report["missing_in_vector"] > 0:
        db = get_sessionmaker()()
        try:
            chunks = db.execute(
                select(DocChunk.id, DocChunk.content)
                .where(
                    DocChunk.kb_id == kb_id,
                    DocChunk.enabled.is_(True),
                    DocChunk.id.in_(report["missing_samples"]),
                )
            ).all()
        finally:
            db.close()
        if chunks:
            # 与 doc_process 同模式：按 KB 解析评测嵌入模型（DB/RAG 配置优先）
            client = get_embedding_client(db, kb_id=kb_id)
            texts = [c.content or "" for c in chunks]
            embeddings = client.embed_texts(texts)
            for (chunk_id, _content), vec in zip(chunks, embeddings):
                if vec:
                    store.upsert(kb_id, chunk_id, vec)
            result["embedded_missing"] = len(chunks)
            logger.info("chunk verify fix kb=%s embedded %d missing chunks", kb_id, len(chunks))

    # 修复后复检
    result["after"] = verify_kb(kb_id)
    return result