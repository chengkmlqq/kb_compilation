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

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.models.framework import Job
from api.services.embedding import get_embedding_client
from api.services.identity import decode_identity_cookie
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
    wiki_create_folder,
    wiki_create_page,
    wiki_delete_folder,
    wiki_delete_page,
    wiki_graph,
    wiki_lint,
    wiki_rebuild_links,
    wiki_search,
    wiki_stats,
    wiki_tree,
    wiki_update_folder,
    wiki_update_page,
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
    scope: str = "system"
    team_name: str | None = None


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


class WikiPageCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    page_type: str = Field(default="entity", max_length=32)
    content: str = Field(default="", max_length=200000)
    summary: str | None = Field(default=None)
    folder_id: str | None = Field(default=None)


class WikiPageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    page_type: str | None = Field(default=None, max_length=32)
    content: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    folder_id: str | None = Field(default=None)
    status: str | None = Field(default=None, max_length=32)


class WikiFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)


class WikiFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)


@router.get("")
def get_kbs(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    scope: str | None = None,
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    ) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    caller_user_id = identity.user_id if identity else ""
    caller_team_name = identity.team_name or "" if identity else ""
    is_sys_admin = False
    if caller_user_id:
        from api.services.scope import is_admin

        is_sys_admin = is_admin(db, caller_user_id)
    return {
        "success": True,
        "data": list_kbs(
            db,
            page,
            page_size,
            keyword,
            caller_user_id=caller_user_id,
            caller_team_name=caller_team_name,
            is_sys_admin=is_sys_admin,
            scope=scope,
        ),
    }


@router.post("")
def post_kb(
    req: KBCreateRequest,
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    caller_user_id = identity.user_id if identity else ""
    caller_team_name = identity.team_name or "" if identity else ""
    is_sys_admin = False
    if caller_user_id:
        from api.services.scope import is_admin

        is_sys_admin = is_admin(db, caller_user_id)
    from api.services.scope import validate_scope_request

    scope, owner_user_id, owner_team_name = validate_scope_request(
        req.scope,
        caller_user_id=caller_user_id,
        caller_team_name=caller_team_name,
        is_sys_admin=is_sys_admin,
    )
    kb = create_kb(
        db,
        name=req.name,
        label=req.label,
        description=req.description,
        indexing_strategy=req.indexing_strategy,
        scope=scope,
        team_name=owner_team_name or None,
        owner_user_id=owner_user_id or None,
        created_by=caller_user_id or None,
    )
    return {"success": True, "data": {"id": kb.id, "name": kb.name, "scope": kb.scope}}


@router.get("/{kb_id}")
def get_kb_detail(kb_id: str, db: Session = Depends(get_db)) -> dict:
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
def put_kb(kb_id: str, req: KBUpdateRequest, db: Session = Depends(get_db)) -> dict:
    kb = update_kb(db, kb_id, req.model_dump(exclude_none=True))
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": {"id": kb.id, "name": kb.name}}


@router.delete("/{kb_id}")
def del_kb(kb_id: str, db: Session = Depends(get_db)) -> dict:
    result = delete_kb(db, kb_id)
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.get("/{kb_id}/documents")
def get_documents(
    kb_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": list_documents(db, kb_id, page, page_size)}


@router.post("/{kb_id}/documents/upload")
async def upload_document(
    kb_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
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
def del_document(kb_id: str, document_id: str, db: Session = Depends(get_db)) -> dict:
    result = delete_document(db, kb_id, document_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/{kb_id}/wiki")
def get_wiki(kb_id: str, db: Session = Depends(get_db)) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": wiki_tree(db, kb_id)}


@router.get("/{kb_id}/wiki/graph")
def get_wiki_graph_route(
    kb_id: str,
    mode: str = "overview",
    center: str = "",
    depth: int = 1,
    limit: int = 200,
    types: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """Wiki 知识图谱（wiki_page + wiki_link，不依赖 Neo4j）。

    mode=overview 全库图；mode=ego 以 center slug 为中心 depth 跳邻域。
    types 逗号分隔过滤 page_type（entity/concept/summary）。
    """
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    type_list = [t for t in types.split(",") if t.strip()] if types else []
    data = wiki_graph(
        db, kb_id, mode=mode, center=center, depth=depth, limit=limit, types=type_list
    )
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/pages/{slug}")
def get_wiki_page_route(kb_id: str, slug: str, db: Session = Depends(get_db)) -> dict:
    page = get_wiki_page(db, kb_id, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"wiki 页不存在: {slug}")
    return {"success": True, "data": page}


# ---- wiki 管理（页面/目录/统计/检查/重建链接）----

def _require_wiki_user(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


@router.get("/{kb_id}/wiki/stats")
def get_wiki_stats(kb_id: str, db: Session = Depends(get_db)) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": wiki_stats(db, kb_id)}


@router.post("/{kb_id}/wiki/pages")
def create_wiki_page(
    kb_id: str,
    req: WikiPageCreate,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    try:
        data = wiki_create_page(db, kb_id, req.model_dump(exclude_none=True), user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.put("/{kb_id}/wiki/pages/{slug}")
def update_wiki_page(
    kb_id: str,
    slug: str,
    req: WikiPageUpdate,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        data = wiki_update_page(db, kb_id, slug, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 400, detail=str(e))
    return {"success": True, "data": data}


@router.delete("/{kb_id}/wiki/pages/{slug}")
def delete_wiki_page(
    kb_id: str,
    slug: str,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        data = wiki_delete_page(db, kb_id, slug)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/folders")
def create_wiki_folder(
    kb_id: str,
    req: WikiFolderCreate,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        data = wiki_create_folder(db, kb_id, req.model_dump(exclude_none=True), user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.put("/{kb_id}/wiki/folders/{folder_id}")
def update_wiki_folder(
    kb_id: str,
    folder_id: str,
    req: WikiFolderUpdate,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        data = wiki_update_folder(db, kb_id, folder_id, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 400, detail=str(e))
    return {"success": True, "data": data}


@router.delete("/{kb_id}/wiki/folders/{folder_id}")
def delete_wiki_folder(
    kb_id: str,
    folder_id: str,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        data = wiki_delete_folder(db, kb_id, folder_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/rebuild-links")
def rebuild_wiki_links(
    kb_id: str,
    user_id: str = Depends(_require_wiki_user),
    db: Session = Depends(get_db),
) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": wiki_rebuild_links(db, kb_id)}


@router.get("/{kb_id}/wiki/lint")
def lint_wiki(kb_id: str, db: Session = Depends(get_db)) -> dict:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": wiki_lint(db, kb_id)}


@router.get("/{kb_id}/wiki/search")
def search_wiki(
    kb_id: str,
    q: str = Query("", max_length=500),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Wiki 页面内搜索：按标题/内容 ilike 匹配页面（区别于 /search 的混合检索）。"""
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": wiki_search(db, kb_id, q, limit)}


@router.post("/{kb_id}/search")
def search_route(
    kb_id: str,
    req: SearchRequest,
    db: Session = Depends(get_db),
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