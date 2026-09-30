"""Knowledge base management API routes.

Backend surface for the frontend KB pages:
- GET/POST /api/v1/kbs               list / create
- GET/PUT/DELETE /api/v1/kbs/{id}    detail / update / delete (guarded)
- GET  /api/v1/kbs/{id}/documents    document list
- POST /api/v1/kbs/{id}/documents/upload   multipart upload (enqueues Celery)
- DELETE /api/v1/kbs/{id}/documents/{docId}  delete document + chunks
- GET  /api/v1/kbs/{id}/wiki         wiki folder tree + pages
- GET  /api/v1/kbs/{id}/wiki/pages/{slug}  wiki page detail
- POST /api/v1/kbs/{id}/search       JSON hybrid search (non-streaming)
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db, get_knowledge_db
from api.models.framework import Job
from api.services.embedding import get_embedding_client
from api.services.kb_admin import (
    create_document,
    create_kb,
    delete_document,
    delete_kb,
    get_kb,
    get_wiki_page,
    json_search,
    list_documents,
    list_kbs,
    update_kb,
    wiki_tree,
)
from api.services.kb_admin import _remove_local_file  # noqa: PLC2701 (same package helper)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kbs", tags=["kbs"])

settings = get_settings()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB, mirrors the source platform limit


class KBCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    label: str | None = None
    description: str | None = None
    indexing_strategy: dict | None = None


class KBUpdateRequest(BaseModel):
    name: str | None = None
    label: str | None = None
    description: str | None = None
    indexing_strategy: dict | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    embed_query: bool = Field(default=True)


@router.get("")
def get_kbs(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    db: Session = Depends(get_knowledge_db),
) -> dict:
    return {"success": True, "data": list_kbs(db, page, page_size, keyword)}


@router.post("")
def post_kb(req: KBCreateRequest, db: Session = Depends(get_knowledge_db)) -> dict:
    kb = create_kb(
        db,
        name=req.name,
        label=req.label,
        description=req.description,
        indexing_strategy=req.indexing_strategy,
    )
    return {"success": True, "data": {"id": kb.id, "name": kb.name}}


@router.get("/{kb_id}")
def get_kb_detail(kb_id: str, db: Session = Depends(get_knowledge_db)) -> dict:
    kb = get_kb(db, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    from api.services.kb_admin import list_kbs

    # reuse list_kbs counts by fetching with a filter-free page (cheap: single row)
    data = list_kbs(db, page=1, page_size=1)
    payload = None
    for item in data["items"]:
        if item["id"] == kb_id:
            payload = item
            break
    if payload is None:  # pragma: no cover - defensive
        payload = {
            "id": kb.id,
            "name": kb.name,
            "label": kb.label,
            "description": kb.description,
            "indexing_strategy": kb.indexing_strategy or {},
        }
    return {"success": True, "data": payload}


@router.put("/{kb_id}")
def put_kb(kb_id: str, req: KBUpdateRequest, db: Session = Depends(get_knowledge_db)) -> dict:
    kb = update_kb(db, kb_id, req.model_dump(exclude_none=True))
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": {"id": kb.id, "name": kb.name}}


@router.delete("/{kb_id}")
def del_kb(kb_id: str, db: Session = Depends(get_knowledge_db)) -> dict:
    result = delete_kb(db, kb_id)
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.get("/{kb_id}/documents")
def get_documents(
    kb_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_knowledge_db),
) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": list_documents(db, kb_id, page, page_size)}


@router.post("/{kb_id}/documents/upload")
async def upload_document(
    kb_id: str,
    file: UploadFile,
    db: Session = Depends(get_knowledge_db),
) -> dict:
    """Accept a document upload, persist bytes locally, enqueue Celery parse.

    Pipeline: bytes -> {KB_STORAGE_DIR}/{kb_id}/{doc_id}{ext} -> kb_document
    row (PENDING) -> modo_job row + Celery send_task (KbDocumentProcessTask).
    """
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    file_name = (file.filename or "untitled").strip()
    if not file_name or file_name == "untitled":
        raise HTTPException(status_code=400, detail="文件名不能为空")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )

    # --- persist bytes ---
    doc_id = uuid.uuid4().hex
    ext = Path(file_name).suffix or ""
    rel_dir = kb_id
    rel_path = f"{rel_dir}/{doc_id}{ext}"
    abs_dir = Path(settings.kb_storage_dir) / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    abs_path = abs_dir / f"{doc_id}{ext}"
    abs_path.write_bytes(content)

    # --- kb_document row ---
    doc = create_document(
        db,
        kb_id=kb_id,
        file_name=file_name,
        file_ext=ext,
        file_size=len(content),
        storage_path=str(abs_path),
    )

    # --- enqueue Celery job (modo_job row + send_task) ---
    try:
        _enqueue_document_process(kb_id, doc.id)
    except Exception:  # noqa: BLE001
        logger.exception("failed to enqueue doc process task; document kept as PENDING")
        # keep the row so a manual retry / worker sweep can pick it up

    return {
        "success": True,
        "data": {
            "id": doc.id,
            "file_name": doc.file_name,
            "parse_state": doc.parse_state,
            "storage_path": doc.storage_path,
        },
    }


def _enqueue_document_process(kb_id: str, document_id: str) -> None:
    """Create a modo_job row (trigger=API) and send the Celery task.

    Runs on the framework session (modo_job lives on the shared store);
    mirrors the scheduler's CRON dispatch but with trigger_type=API.
    """
    from worker.celery_app import celery_app

    job_id = f"DOC_{document_id}"
    task_class = "KbDocumentProcessTask"
    task_params = json.dumps({"kbId": kb_id, "documentId": document_id}, ensure_ascii=False)

    from api.db import get_sessionmaker

    framework_db = get_sessionmaker()()
    try:
        framework_db.add(
            Job(
                id=job_id,
                task_id=document_id,
                task_class=task_class,
                queue_name="default",
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
            )
        )
        framework_db.commit()
    finally:
        framework_db.close()

    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job",
        args=[job_id],
        task_id=job_id,
        queue="default",
    )


@router.delete("/{kb_id}/documents/{document_id}")
def del_document(kb_id: str, document_id: str, db: Session = Depends(get_knowledge_db)) -> dict:
    result = delete_document(db, kb_id, document_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/{kb_id}/wiki")
def get_wiki(kb_id: str, db: Session = Depends(get_knowledge_db)) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": wiki_tree(db, kb_id)}


@router.get("/{kb_id}/wiki/pages/{slug}")
def get_wiki_page_route(kb_id: str, slug: str, db: Session = Depends(get_knowledge_db)) -> dict:
    page = get_wiki_page(db, kb_id, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"wiki 页不存在: {slug}")
    return {"success": True, "data": page}


@router.post("/{kb_id}/search")
def search_route(
    kb_id: str,
    req: SearchRequest,
    db: Session = Depends(get_knowledge_db),
) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    query_embedding = None
    if req.embed_query:
        try:
            query_embedding = get_embedding_client(db).embed_query(req.query)
        except Exception:  # noqa: BLE001
            logger.exception("query embedding failed; keyword-only search")
    hits = json_search(
        db,
        kb_id,
        req.query,
        query_embedding=query_embedding,
        top_k=req.top_k,
        threshold=req.threshold,
    )
    return {"success": True, "data": {"items": hits, "total": len(hits), "query": req.query}}


__all__ = ["router"]