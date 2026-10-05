"""Document ingestion pipeline: parse -> chunk -> embed -> store.

Orchestrates the knowledge-domain write path:
1. Load file bytes (from sys_file storage record or raw bytes)
2. docreader parses to markdown (+ images)
3. TextSplitter chunks the content
4. EmbeddingClient embeds each chunk
5. doc_chunk rows are written to the BUSINESS store (framework relational DB)
   and embeddings to the vector store (kb_embedding via VectorStore)

All steps are idempotent-ish: re-ingesting a document replaces its chunks
(delete-by-document then insert), so a retry after a mid-pipeline failure
does not duplicate data. Celery tasks call the pieces directly with their own
sessions — this module takes explicit db handles (no globals).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from api.models.knowledge import DocChunk, KbDocument
from api.services.chunking import ChunkConfig
from api.services.chunking_orch import split, split_parent_child
from api.services.embedding import EmbeddingClient
from worker.tasks.parsers.registry import parse_document_by_engine

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 80


def _doc_id() -> str:
    return uuid.uuid4().hex


def parse_document(
    file_name: str,
    file_type: str,
    content: bytes,
    parser_engine: str | None = None,
    engine_rules: list[dict] | None = None,
) -> str:
    """Parse file bytes to markdown via the selected engine.

    Delegates to the engine dispatcher; parser_engine forces a specific engine,
    engine_rules are the KB's type->engine rules. Returns markdown text.
    """
    markdown, _images, _engine = parse_document_by_engine(
        file_name, file_type, content,
        engine_rules=engine_rules,
        forced_engine=parser_engine,
    )
    return markdown


def chunk_text(
    content: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str = "auto",
) -> list[tuple[int, str]]:
    """Split markdown text into (seq, text) chunks via the adaptive engine.

    Backwards-compatible signature (chunk_size/overlap) plus an optional
    strategy; callers should prefer passing a full ChunkConfig.
    """
    if not content or not content.strip():
        return []
    cfg = ChunkConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy=strategy)
    chunks = split(content, cfg)
    return [(c.seq, c.content) for c in chunks]


def embed_chunks(client: EmbeddingClient, texts: list[str]) -> list[list[float]]:
    """Embed chunk texts; returns one vector per text (same order)."""
    if not texts:
        return []
    return client.embed_texts(texts)


def replace_document_chunks(
    db: Session,
    kb_id: str,
    document_id: str,
    chunks: list[tuple[int, str]],
    vectors: list[list[float]],
    meta: dict | None = None,
    parent_seq_by_child: dict[int, int] | None = None,
    parent_texts: dict[int, str] | None = None,
) -> int:
    """Write chunks for a document, replacing any previous chunks (idempotent).

    Chunk rows (id/kb_id/seq/content/meta) go to the BUSINESS store (`db`);
    embeddings go to the vector store (kb_embedding via VectorStore).

    When `parent_seq_by_child`/`parent_texts` are supplied (parent-child mode),
    each child row's meta carries `parent_seq` + `parent_content` so retrieval
    can match on the child vector but return the parent text.

    Returns the number of chunks written.
    """
    # Remove stale chunks first (idempotent re-ingest / retry safety), and
    # their embeddings from the vector store (new chunks get new ids).
    from api.services.vector_store import get_vector_store

    store = get_vector_store()
    stale_ids = [
        row[0]
        for row in db.execute(
            select(DocChunk.id).where(DocChunk.document_id == document_id)
        ).all()
    ]
    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    db.flush()
    store.delete_by_chunks(stale_ids)

    base_meta = meta or {}
    row_count = 0
    for (seq, text), vec in zip(chunks, vectors):
        chunk_id = _doc_id()
        row_meta = dict(base_meta)
        if parent_seq_by_child and seq in parent_seq_by_child:
            pseq = parent_seq_by_child[seq]
            row_meta["parent_seq"] = pseq
            if parent_texts and pseq in parent_texts:
                row_meta["parent_content"] = parent_texts[pseq]
        db.add(
            DocChunk(
                id=chunk_id,
                kb_id=kb_id,
                document_id=document_id,
                seq=seq,
                content=text,
                meta=row_meta,
                enabled=True,
            )
        )
        store.upsert(kb_id, chunk_id, vec)
        row_count += 1
    return row_count


def update_document_state(
    db: Session,
    document_id: str,
    state: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    """Advance kb_document parse state (PENDING -> PARSING -> EMBEDDING -> READY / FAILED)."""
    doc = db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
    if not doc:
        return
    doc.parse_state = state
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if error is not None:
        doc.parse_error = error


def ingest_document(
    kb_db: Session,
    document: KbDocument,
    file_content: bytes,
    embedding_client: EmbeddingClient,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    parser_engine: str | None = None,
    chunk_cfg: ChunkConfig | None = None,
) -> dict:
    """Full pipeline for one document. Returns a summary dict.

    - kb_db: business-store session (doc_chunk / kb_document live on the
      framework relational store)
    - file_content: raw bytes of the document
    - chunk_cfg: optional KB-level chunking config (parent-child aware).
      When omitted, falls back to chunk_size/chunk_overlap.
    """
    result: dict = {"document_id": document.id, "chunks": 0, "parse_state": "FAILED"}

    # resolve chunking config: explicit cfg > legacy size/overlap args
    if chunk_cfg is None:
        chunk_cfg = ChunkConfig(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy="auto"
        )

    # load the KB's engine rules (type -> engine); resolution happens in the
    # dispatcher, which also applies the availability fallback
    engine_rules: list[dict] | None = None
    try:
        from api.services.kb_chunking import load_engine_rules

        engine_rules = load_engine_rules(kb_db, document.kb_id)
    except Exception:  # noqa: BLE001 - config lookup must not block ingest
        engine_rules = None

    # 1. parse
    update_document_state(kb_db, document.id, "PARSING")
    kb_db.commit()
    used_engine = parser_engine
    try:
        markdown, _images, used_engine = parse_document_by_engine(
            document.file_name,
            document.file_ext or "",
            file_content,
            engine_rules=engine_rules,
            forced_engine=parser_engine,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("parse failed for document %s", document.id)
        update_document_state(kb_db, document.id, "FAILED", error=f"parse: {e}")
        kb_db.commit()
        result["error"] = str(e)
        result["success"] = False  # 2026-10-05: 显式失败标志(execute_modo_job 据此落 FAILED, 防假成功)
        return result

    if not markdown.strip():
        update_document_state(kb_db, document.id, "FAILED", error="empty parse result")
        kb_db.commit()
        result["error"] = "empty parse result"
        result["success"] = False
        return result

    # 2. chunk (adaptive; parent-child when enabled)
    parent_seq_by_child: dict[int, int] = {}
    parent_texts: dict[int, str] = {}
    if chunk_cfg.enable_parent_child:
        children, parents = split_parent_child(markdown, chunk_cfg)
        chunks = [(c.seq, c.content) for c in children]
        parent_seq_by_child = {c.seq: c.parent_seq for c in children if c.parent_seq is not None}
        # store parent texts; child rows reference them via meta so retrieval
        # can return parent context
        parent_texts = {p.seq: p.content for p in parents}
    else:
        chunks = [(c.seq, c.content) for c in split(markdown, chunk_cfg)]

    if not chunks:
        update_document_state(kb_db, document.id, "FAILED", error="no chunks produced")
        kb_db.commit()
        result["error"] = "no chunks produced"
        result["success"] = False
        return result

    # 3. embed
    update_document_state(kb_db, document.id, "EMBEDDING")
    kb_db.commit()
    texts = [text for _, text in chunks]
    vectors = embed_chunks(embedding_client, texts)

    # 4. store
    meta = {"source_file": document.file_name, "parse_engine": used_engine}
    written = replace_document_chunks(
        kb_db, document.kb_id, document.id, chunks, vectors, meta,
        parent_seq_by_child=parent_seq_by_child if chunk_cfg.enable_parent_child else None,
        parent_texts=parent_texts if chunk_cfg.enable_parent_child else None,
    )
    update_document_state(kb_db, document.id, "READY", chunk_count=written)
    kb_db.commit()

    result["chunks"] = written
    result["parse_state"] = "READY"
    logger.info("ingested document %s: %d chunks (engine=%s)", document.id, written, used_engine)
    return result