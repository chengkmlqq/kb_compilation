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
from api.services.embedding import EmbeddingClient
from docreader.parser import Parser
from docreader.splitter.splitter import TextSplitter

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
) -> str:
    """Parse file bytes to markdown text via docreader."""
    parser = Parser()
    doc = parser.parse_file(
        file_name=file_name,
        file_type=file_type,
        content=content,
        parser_engine=parser_engine,
    )
    return doc.content or ""


def chunk_text(
    content: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[tuple[int, str]]:
    """Split markdown text into (seq, text) chunks using docreader's splitter."""
    if not content or not content.strip():
        return []
    splitter = TextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    ranges = splitter.split_text(content)
    return [(i, ranges[i][2]) for i in range(len(ranges))]


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
) -> int:
    """Write chunks for a document, replacing any previous chunks (idempotent).

    Chunk rows (id/kb_id/seq/content/meta) go to the BUSINESS store (`db`);
    embeddings go to the vector store (kb_embedding via VectorStore).

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

    row_count = 0
    for (seq, text), vec in zip(chunks, vectors):
        chunk_id = _doc_id()
        db.add(
            DocChunk(
                id=chunk_id,
                kb_id=kb_id,
                document_id=document_id,
                seq=seq,
                content=text,
                meta=meta or {},
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
) -> dict:
    """Full pipeline for one document. Returns a summary dict.

    - kb_db: business-store session (doc_chunk / kb_document live on the
      framework relational store)
    - file_content: raw bytes of the document
    """
    result: dict = {"document_id": document.id, "chunks": 0, "parse_state": "FAILED"}

    # 1. parse
    update_document_state(kb_db, document.id, "PARSING")
    kb_db.commit()
    try:
        markdown = parse_document(
            document.file_name,
            document.file_ext or "",
            file_content,
            parser_engine=parser_engine,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("parse failed for document %s", document.id)
        update_document_state(kb_db, document.id, "FAILED", error=f"parse: {e}")
        kb_db.commit()
        result["error"] = str(e)
        return result

    if not markdown.strip():
        update_document_state(kb_db, document.id, "FAILED", error="empty parse result")
        kb_db.commit()
        result["error"] = "empty parse result"
        return result

    # 2. chunk
    chunks = chunk_text(markdown, chunk_size, chunk_overlap)
    if not chunks:
        update_document_state(kb_db, document.id, "FAILED", error="no chunks produced")
        kb_db.commit()
        result["error"] = "no chunks produced"
        return result

    # 3. embed
    update_document_state(kb_db, document.id, "EMBEDDING")
    kb_db.commit()
    texts = [text for _, text in chunks]
    vectors = embed_chunks(embedding_client, texts)

    # 4. store
    meta = {"source_file": document.file_name}
    written = replace_document_chunks(kb_db, document.kb_id, document.id, chunks, vectors, meta)
    update_document_state(kb_db, document.id, "READY", chunk_count=written)
    kb_db.commit()

    result["chunks"] = written
    result["parse_state"] = "READY"
    logger.info("ingested document %s: %d chunks", document.id, written)
    return result