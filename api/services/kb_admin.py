"""Knowledge base management service — CRUD + documents + wiki browsing.

Owns the read/write paths the frontend needs beyond RAG QA:
- KB CRUD (kb_datasource), with a delete guard (a KB that still has
  documents cannot be deleted — mirrors WeKnora's vectorstore guard)
- document listing / delete (kb_document + doc_chunk cleanup)
- wiki browsing (folder tree + page detail with links)
- JSON hybrid search (non-streaming variant of the QA retrieval)

All functions take explicit sessions (knowledge store unless noted), so
Celery tasks and FastAPI routers reuse the same logic without globals.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models.knowledge import (
    DocChunk,
    KbDatasource,
    KbDocument,
    WikiFolder,
    WikiLink,
    WikiPage,
)
from api.services.retrieval import ChunkHit, hybrid_search
from api.services.wiki import slugify

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Knowledge base CRUD
# ---------------------------------------------------------------------------


def list_kbs(
    db: Session,
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    team_name: str | None = None,
) -> dict:
    """Paginated KB list with doc/page counts (state='1' only)."""
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(KbDatasource.name.ilike(like))
    if team_name:
        stmt = stmt.where(KbDatasource.team_name == team_name)

    total = len(db.execute(stmt).scalars().all())
    rows = (
        db.execute(
            stmt.order_by(KbDatasource.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    items = []
    for kb in rows:
        doc_count = (
            db.execute(
                select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb.id)
            ).scalar()
            or 0
        )
        page_count = (
            db.execute(
                select(func.count()).select_from(WikiPage).where(WikiPage.kb_id == kb.id)
            ).scalar()
            or 0
        )
        items.append(_kb_dict(kb, doc_count=doc_count, page_count=page_count))
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def get_kb(db: Session, kb_id: str) -> KbDatasource | None:
    return db.execute(
        select(KbDatasource).where(KbDatasource.id == kb_id, KbDatasource.state == "1")
    ).scalars().first()


def create_kb(
    db: Session,
    name: str,
    label: str | None = None,
    description: str | None = None,
    indexing_strategy: dict | None = None,
    team_name: str | None = None,
    created_by: str | None = None,
) -> KbDatasource:
    """Create a KB; name is required, id is generated if absent."""
    kb = KbDatasource(
        id=_uuid(),
        name=name.strip(),
        label=label,
        description=description,
        team_name=team_name,
        created_by=created_by,
        indexing_strategy=indexing_strategy or {},
        state="1",
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def update_kb(db: Session, kb_id: str, fields: dict) -> KbDatasource | None:
    kb = get_kb(db, kb_id)
    if not kb:
        return None
    if "name" in fields:
        kb.name = str(fields["name"]).strip() or kb.name
    if "label" in fields:
        kb.label = str(fields.get("label") or "")
    if "description" in fields:
        kb.description = str(fields.get("description") or "")
    if "indexing_strategy" in fields and isinstance(fields["indexing_strategy"], dict):
        kb.indexing_strategy = fields["indexing_strategy"]
    db.commit()
    db.refresh(kb)
    return kb


def delete_kb(db: Session, kb_id: str) -> dict:
    """Soft-delete a KB. Guard: rejects when documents still exist.

    Mirrors WeKnora's vectorstore delete guard — deleting a KB that has
    documents would strand their chunks/pages, so the caller must delete
    documents first.
    """
    kb = get_kb(db, kb_id)
    if not kb:
        return {"success": False, "message": f"知识库不存在: {kb_id}"}
    doc_count = (
        db.execute(
            select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
        ).scalar()
        or 0
    )
    if doc_count > 0:
        return {
            "success": False,
            "message": f"知识库仍有 {doc_count} 个文档，请先删除文档后再删除知识库",
        }
    kb.state = "0"
    db.commit()
    return {"success": True, "data": {"id": kb_id}}


def _kb_dict(kb: KbDatasource, doc_count: int = 0, page_count: int = 0) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "label": kb.label,
        "description": kb.description,
        "indexing_strategy": kb.indexing_strategy or {},
        "state": kb.state,
        "doc_count": doc_count,
        "page_count": page_count,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def list_documents(db: Session, kb_id: str, page: int = 1, page_size: int = 20) -> dict:
    stmt = select(KbDocument).where(KbDocument.kb_id == kb_id)
    total = len(db.execute(stmt).scalars().all())
    rows = (
        db.execute(
            stmt.order_by(KbDocument.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": d.id,
            "kb_id": d.kb_id,
            "file_name": d.file_name,
            "file_ext": d.file_ext,
            "file_size": d.file_size,
            "parse_state": d.parse_state,
            "parse_error": d.parse_error,
            "chunk_count": d.chunk_count,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def create_document(
    db: Session,
    kb_id: str,
    file_name: str,
    file_ext: str | None = None,
    file_size: int | None = None,
    storage_path: str | None = None,
    sys_file_id: str | None = None,
    created_by: str | None = None,
) -> KbDocument:
    doc = KbDocument(
        id=_uuid(),
        kb_id=kb_id,
        file_name=file_name,
        file_ext=(file_ext or "").lstrip(".").lower() or None,
        file_size=file_size,
        storage_path=storage_path,
        sys_file_id=sys_file_id,
        parse_state="PENDING",
        chunk_count=0,
        created_by=created_by,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def delete_document(db: Session, kb_id: str, document_id: str) -> dict:
    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        return {"success": False, "message": f"文档不存在: {document_id}"}
    # Cascade: drop chunks first, then the document row.
    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    db.delete(doc)
    db.commit()
    # Best-effort local file cleanup (never fatal).
    _remove_local_file(doc.storage_path)
    return {"success": True, "data": {"id": document_id}}


def _remove_local_file(storage_path: str | None) -> None:
    if not storage_path:
        return
    try:
        if os.path.isfile(storage_path) and os.path.exists(storage_path):
            os.remove(storage_path)
    except OSError:
        logger.warning("failed to remove local file: %s", storage_path)


# ---------------------------------------------------------------------------
# Wiki browsing
# ---------------------------------------------------------------------------


def wiki_tree(db: Session, kb_id: str) -> dict:
    """Folders + pages for a KB (folder_id '' = root)."""
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.created_at)
    ).scalars().all()
    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    folder_items = [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id or "",
            "page_count": sum(1 for p in pages if (p.folder_id or "") == f.id),
        }
        for f in folders
    ]
    page_items = [
        {
            "id": p.id,
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "folder_id": p.folder_id or "",
            "summary": p.summary,
        }
        for p in pages
    ]
    return {"kb_id": kb_id, "folders": folder_items, "pages": page_items}


def get_wiki_page(db: Session, kb_id: str, slug: str) -> dict | None:
    page = db.execute(
        select(WikiPage).where(
            WikiPage.kb_id == kb_id,
            WikiPage.slug == slug,
            WikiPage.status == "active",
        )
    ).scalars().first()
    if not page:
        return None
    links = db.execute(
        select(WikiLink).where(
            WikiLink.kb_id == kb_id, WikiLink.from_page_id == page.id
        )
    ).scalars().all()
    linked_pages = []
    if links:
        linked_ids = [l.to_page_id for l in links]
        linked = db.execute(
            select(WikiPage).where(WikiPage.id.in_(linked_ids), WikiPage.status == "active")
        ).scalars().all()
        linked_pages = [
            {"slug": lp.slug, "title": lp.title, "page_type": lp.page_type} for lp in linked
        ]
    return {
        "id": page.id,
        "slug": page.slug,
        "title": page.title,
        "page_type": page.page_type,
        "content": page.content,
        "summary": page.summary,
        "source_refs": page.source_refs or [],
        "folder_id": page.folder_id or "",
        "links": linked_pages,
        "created_at": page.created_at.isoformat() if page.created_at else None,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }


# ---------------------------------------------------------------------------
# JSON search (non-streaming retrieval for the frontend search box)
# ---------------------------------------------------------------------------


def json_search(
    db: Session,
    kb_id: str,
    query: str,
    query_embedding: list[float] | None = None,
    top_k: int = 5,
    threshold: float = 0.2,
) -> list[dict]:
    """Hybrid search returning serializable hit dicts (no LLM involved)."""
    from api.services.retrieval import config_from_kb

    kb = get_kb(db, kb_id)
    if not kb:
        return []
    cfg = config_from_kb(kb, {"top_k": top_k, "threshold": threshold})
    hits: list[ChunkHit] = hybrid_search(db, kb_id, query, query_embedding, cfg)
    return [
        {
            "chunk_id": h.chunk_id,
            "content": h.content,
            "document_id": h.document_id,
            "kb_id": h.kb_id,
            "score": round(h.score, 4),
        }
        for h in hits
    ]


__all__ = [
    "create_document",
    "create_kb",
    "delete_document",
    "delete_kb",
    "get_kb",
    "get_wiki_page",
    "json_search",
    "list_documents",
    "list_kbs",
    "update_kb",
    "wiki_tree",
    "_remove_local_file",
    "slugify",
]
