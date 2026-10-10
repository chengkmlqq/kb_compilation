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

import io
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.models.framework import Job, SysFile
from api.services.embedding import get_embedding_client
from api.services.identity import decode_identity_cookie
from api.routers.files import (
    _content_disposition,  # noqa: PLC2701 (same-package helpers, 文档下载复用)
    _is_s3_row,  # noqa: PLC2701
    _local_fallback_path,  # noqa: PLC2701
    _read_stored_bytes,  # noqa: PLC2701
)
from api.services.kb_admin import (
    create_document,
    create_kb,
    delete_document,
    delete_kb,
    doc_branch,
    doc_create_folder,
    doc_delete_folder,
    doc_folders,
    doc_move_documents,
    doc_update_folder,
    generate_document_summary,
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
    wiki_index,
    wiki_lint,
    wiki_list_feedback,
    wiki_list_logs,
    wiki_rebuild_links,
    wiki_search,
    wiki_stats,
    wiki_submit_feedback,
    wiki_tree,
    wiki_branch,
    wiki_folders,
    wiki_update_folder,
    wiki_update_page,
    wiki_update_feedback_status,
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
    # ── WeKnora 对齐配置 ──
    type: str = "document"  # document / faq
    custom_wiki_generation: bool = False
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
    storage_backend_id: str | None = None
    vector_store_id: str | None = None
    configs: dict | None = None  # wiki/extract/faq/vlm/asr/storage 等 JSON 配置
    ontology_schema_name: str | None = None  # 绑定本体 Schema（抽取分类结构，多领域）


class KBUpdateRequest(BaseModel):
    name: str | None = None
    label: str | None = None
    description: str | None = None
    indexing_strategy: dict | None = None
    type: str | None = None
    custom_wiki_generation: bool | None = None
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
    storage_backend_id: str | None = None
    vector_store_id: str | None = None
    configs: dict | None = None


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
    source_refs: list | None = Field(default=None)


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


class DocFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)


class DocFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)


class DocMoveRequest(BaseModel):
    """批量移动文档到目录（folder_id="" = 移出所有目录到根层级）。"""

    folder_id: str = Field(default="")
    document_ids: list[str] = Field(..., min_length=1)


class WikiFeedbackCreate(BaseModel):
    feedback_type: str = Field(default="issue", pattern="^(helpful|issue)$")
    content: str = Field(default="", max_length=2000)


class WikiFeedbackStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(open|resolved|ignored)$")


@router.get("")
def get_kbs(
    page: int = Query(1, ge=1),
    # le=1000：本体 Schema 页「绑定知识库」下拉 apiListKbs(1, 500) 全量拉取
    page_size: int = Query(10, ge=1, le=1000),
    keyword: str = "",
    scope: str | None = None,
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    ) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity:
        raise HTTPException(status_code=401, detail="未登录")
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
    if not identity:
        raise HTTPException(status_code=401, detail="未登录")
    caller_user_id = identity.user_id if identity else ""
    caller_team_name = identity.team_name or "" if identity else ""
    is_sys_admin = False
    if caller_user_id:
        from api.services.scope import is_admin

        is_sys_admin = is_admin(db, caller_user_id)
    from api.services.scope import validate_scope_request

    # 权限/参数异常转标准 HTTP 错误——裸抛 PermissionError 会变成 500
    # （普通用户建库归属选「系统」时 validate_scope_request 抛 PermissionError，
    #  2026-10-05 实测建库页默认归属=system 导致普通用户建库必 500）
    try:
        scope, owner_user_id, owner_team_name = validate_scope_request(
            req.scope,
            caller_user_id=caller_user_id,
            caller_team_name=caller_team_name,
            is_sys_admin=is_sys_admin,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
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
            type=req.type,
            custom_wiki_generation=req.custom_wiki_generation,
            embedding_model_id=req.embedding_model_id,
            summary_model_id=req.summary_model_id,
            storage_backend_id=req.storage_backend_id,
            vector_store_id=req.vector_store_id,
            configs=req.configs,
            ontology_schema_name=req.ontology_schema_name,
            caller_user_id=caller_user_id or None,
            caller_team_name=caller_team_name or None,
            is_sys_admin=is_sys_admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": {"id": kb.id, "name": kb.name, "scope": kb.scope}}


# ---------------------------------------------------------------------------
# 知识库级权限 helper（对齐 scope.py 三级模型：personal/team/system）
# ---------------------------------------------------------------------------


def _require_kb_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    from api.services.scope import is_admin

    return {
        "user_id": identity.user_id,
        "team_name": identity.team_name or "",
        "is_admin": is_admin(db, identity.user_id),
    }


def _kb_visible(kb_id: str, caller: dict, db: Session):
    """读可见性：personal=属主 / team=同队 / system=全员（对齐 can_see）。"""
    kb = get_kb(db, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    from api.services.scope import ResourceRow, can_see

    if not can_see(
        ResourceRow.from_obj(kb),
        caller["user_id"],
        caller["team_name"],
        caller["is_admin"],
    ):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return kb


def _kb_manage(kb_id: str, caller: dict, db: Session):
    """写权限：personal=属主 / team=同队 / system=admin（对齐 can_manage）。"""
    kb = _kb_visible(kb_id, caller, db)
    from api.services.scope import ResourceRow, can_manage

    if not can_manage(
        ResourceRow.from_obj(kb),
        caller["user_id"],
        caller["team_name"],
        caller["is_admin"],
    ):
        raise HTTPException(status_code=403, detail="无权操作该知识库")
    return kb


@router.get("/{kb_id}")
def get_kb_detail(
    kb_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    kb = _kb_visible(kb_id, caller, db)
    from api.services.kb_admin import _kb_dict
    from sqlalchemy import func, select

    from api.models.knowledge import KbDocument, WikiPage

    # 直接用 _kb_dict 完整序列化（含 WeKnora 对齐配置字段）。
    # 旧实现走 list_kbs(page_size=1) 只取第一页再匹配 id——查询非最新 KB 时
    # items 不命中 → 兜底缺对齐字段（详情页配置弹窗读不到 wiki_config 等）。
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
    return {"success": True, "data": _kb_dict(kb, doc_count=doc_count, page_count=page_count)}


@router.put("/{kb_id}")
def put_kb(
    kb_id: str,
    req: KBUpdateRequest,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    try:
        kb = update_kb(
            db,
            kb_id,
            req.model_dump(exclude_none=True),
            caller_user_id=caller["user_id"],
            caller_team_name=caller["team_name"],
            is_sys_admin=caller["is_admin"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return {"success": True, "data": {"id": kb.id, "name": kb.name}}


@router.delete("/{kb_id}")
def del_kb(
    kb_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    result = delete_kb(db, kb_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/{kb_id}/documents")
def get_documents(
    kb_id: str,
    caller: dict = Depends(_require_kb_caller),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    keyword: str = "",
    file_type: str = "",
    parse_status: str = "",
    folder_id: str = "",
    db: Session = Depends(get_db),
) -> dict:
    _kb_visible(kb_id, caller, db)
    data = list_documents(
        db, kb_id, page, page_size,
        keyword=keyword, file_type=file_type, parse_status=parse_status,
        folder_id=folder_id,
    )
    _attach_wiki_build_status(db, kb_id, data.get("items") or [])
    return {"success": True, "data": data}


def _attach_wiki_build_status(db: Session, kb_id: str, items: list[dict]) -> None:
    """给文档列表每行附 wiki 构建状态（最新 WIKI 任务：state/耗时/job_id）。

    通过 task_params.config.knowledge_id 关联文档；任务量级 ~1k 行，直接
    扫 modo_job 前缀匹配，不引入额外索引/轮询。
    """
    from api.models.framework import Job
    from sqlalchemy import select

    if not items:
        return
    jobs = db.execute(
        select(Job.id, Job.task_params, Job.state, Job.duration_ms)
        .where(Job.id.like("WIKI%"))
        .order_by(Job.create_time.desc())
    ).all()
    by_doc: dict[str, dict] = {}
    for jid, tp, state, dur in jobs:
        try:
            p = json.loads(tp or "{}")
            cfg = p.get("config") or {}
            if str(cfg.get("kb_id") or "") != kb_id:
                continue
            kid = str(cfg.get("knowledge_id") or cfg.get("kid") or cfg.get("doc_name") or "")
            if kid and kid not in by_doc:
                by_doc[kid] = {
                    "state": state,
                    "duration_ms": dur,
                    "job_id": str(jid),
                }
        except Exception:  # noqa: BLE001
            continue
    for it in items:
        b = by_doc.get(str(it.get("id") or ""))
        if b:
            it["wiki_build"] = b


@router.post("/{kb_id}/documents/upload")
async def upload_document(
    kb_id: str,
    file: UploadFile | None = None,
    url: str = Query(default="", max_length=2000),
    file_name: str = Query(default="", max_length=255),
    folder_id: str = Query(default="", max_length=64),
    process_config: str = Form(default=""),

    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """Accept a document upload (multipart file OR remote URL), persist bytes
    locally, enqueue Celery parse.
    Pipeline: bytes -> {KB_STORAGE_DIR}/{kb_id}/{doc_id}{ext} -> kb_document
    row (PENDING) -> modo_job row + Celery send_task (KbDocumentProcessTask).
    URL mode fetches the remote document with a 20s timeout and the same size
    cap as file uploads.

    process_config: 可选的**文件级处理配置**（对齐 WeKnora 上传确认弹窗
    KnowledgeProcessOverrides），JSON 字符串，形如
    {"chunking": {"chunk_size": 500, ...}, "parser_engine_rules": [...]}，
    存 kb_document.process_config，解析时覆盖 KB 级默认配置。
    """
    _kb_manage(kb_id, caller, db)

    if url.strip():
        # URL 导入模式
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url.strip())
            if parsed.scheme not in ("http", "https"):
                raise HTTPException(status_code=400, detail="仅支持 http/https 链接导入")
            import urllib.request

            req = urllib.request.Request(url.strip(), headers={"User-Agent": "Mozilla/5.0 kb-importer"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                content = resp.read()
            base_name = Path(parsed.path).name or ""
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("URL import fetch failed")
            raise HTTPException(status_code=400, detail=f"URL 抓取失败: {exc}") from exc
        fname = (file_name.strip() or base_name or "untitled").strip()
        if fname == "untitled":
            raise HTTPException(status_code=400, detail="无法从链接识别文件名，请用 file_name 指定")
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="请上传文件或提供 url")
        fname = (file.filename or "untitled").strip()
        if not fname or fname == "untitled":
            raise HTTPException(status_code=400, detail="文件名不能为空")
        content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )

    # --- persist bytes（本地磁盘 / MinIO，由 storage 层按配置决定）---
    from api.services import storage as storage_svc

    doc_id = uuid.uuid4().hex
    ext = Path(fname).suffix or ""
    storage_path = storage_svc.resolve_new_path(f"kb_documents/{kb_id}/{doc_id}{ext}")
    try:
        storage_svc.put_bytes(storage_path, content)
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to persist uploaded bytes")
        raise HTTPException(status_code=500, detail=f"文件存储失败: {exc}") from exc

    # --- 文件级处理配置归一化（非法 JSON 忽略，不阻塞上传）---
    pc_json = ""
    if (process_config or "").strip():
        try:
            parsed_pc = json.loads(process_config)
            if isinstance(parsed_pc, dict) and parsed_pc:
                pc_json = json.dumps(parsed_pc, ensure_ascii=False)
        except (TypeError, json.JSONDecodeError):
            logger.warning("upload %s: invalid process_config ignored", kb_id)

    # --- kb_document row ---
    doc = create_document(
        db,
        kb_id=kb_id,
        file_name=fname,
        file_ext=ext,
        file_size=len(content),
        storage_path=storage_path,
        folder_id=folder_id,
        process_config=pc_json or None,

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
    """Create-or-reset a modo_job row (trigger=API) and send the Celery task.

    Runs on the framework session (modo_job lives on the shared store);
    mirrors the scheduler's CRON dispatch but with trigger_type=API.

    幂等：job_id 固定为 DOC_{document_id}，重新解析时该行已存在 → 直接
    insert 会撞主键(IntegrityError 1062)导致入队失败。改为先查后改——
    存在则重置为 PENDING 并清空上一轮执行痕迹，不存在则新建。
    """
    from worker.celery_app import celery_app

    job_id = f"DOC_{document_id}"
    task_class = "KbDocumentProcessTask"
    task_params = json.dumps({"kbId": kb_id, "documentId": document_id}, ensure_ascii=False)

    from api.db import get_sessionmaker

    framework_db = get_sessionmaker()()
    try:
        existing = framework_db.get(Job, job_id)
        if existing is None:
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
        else:
            # 重跑：重置为待执行，清掉上一轮的结束状态/错误/耗时
            existing.task_id = document_id
            existing.task_class = task_class
            existing.queue_name = "default"
            existing.task_params = task_params
            existing.trigger_type = "API"
            existing.state = "PENDING"
            existing.start_time = None
            existing.end_time = None
            existing.duration_ms = None
            existing.error_message = None
            existing.log_path = None
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
def del_document(kb_id: str, document_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    _kb_manage(kb_id, caller, db)
    result = delete_document(db, kb_id, document_id)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/{kb_id}/documents/{document_id}/chunks")
def get_document_chunks(
    kb_id: str,
    document_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """读取文档解析后的 chunks（技能/wiki 构建用）。

    返回该文档的所有文本块（content/seq/embedding 状态），供外部技能
    脚本拉取文档内容后生成 wiki 页面。
    """
    _kb_visible(kb_id, caller, db)
    from api.models.knowledge import DocChunk
    from sqlalchemy import select

    rows = db.execute(
        select(
            DocChunk.id,
            DocChunk.seq,
            DocChunk.content,
            DocChunk.meta,
            DocChunk.enabled,
        )
        .where(DocChunk.kb_id == kb_id, DocChunk.document_id == document_id)
        .order_by(DocChunk.seq)
    ).all()
    return {
        "success": True,
        "data": {
            "kb_id": kb_id,
            "document_id": document_id,
            "total": len(rows),
            "items": [
                {
                    "chunk_id": r[0],
                    "seq": r[1],
                    "content": r[2],
                    "meta": r[3],
                    "enabled": r[4],
                }
                for r in rows
            ],
        },
    }



@router.get("/{kb_id}/documents/{document_id}/processing-timeline")
def document_processing_timeline(kb_id: str, document_id: str, db: Session = Depends(get_db)) -> dict:
    """文档全流程处理时间线（对齐 WeKnora knowledge-processing-timeline）。

    stages: 解析（DOC_ 任务）→ 向量化（kb_embedding 计数）→ Wiki 构建
    （WIKI_SKILL 任务 + 技能步骤树）。wiki 步骤树复用 /jobs/{id}/trace 的
    events.summary.json（measure 嵌套还原的执行轨迹）。
    """
    from api.models.framework import Job
    from sqlalchemy import func, select, text

    doc = db.execute(
        text("SELECT file_name, file_size, parse_state, parse_error, chunk_count "
             "FROM kb_document WHERE id=:d"), {"d": document_id}
    ).fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # ---- 解析任务（DOC_<doc_id>，任务链上唯一的解析+嵌入任务） ----
    parse_job = db.execute(
        select(Job.state, Job.duration_ms, Job.error_message, Job.start_time, Job.end_time)
        .where(Job.id == f"DOC_{document_id}")
    ).fetchone()

    # ---- wiki 构建任务（WIKI_SKILL_*，task_params.config.knowledge_id 关联） ----
    wiki_state = wiki_job_id = wiki_dur = wiki_err = None
    wiki_jobs = db.execute(
        select(Job.id, Job.task_params, Job.state, Job.duration_ms, Job.error_message)
        .where(Job.id.like("WIKI%"))
        .order_by(Job.create_time.desc())
    ).all()
    for jid, tp, st, dur, err in wiki_jobs:
        try:
            p = json.loads(tp or "{}")
            cfg = p.get("config") or {}
            if str(cfg.get("kb_id") or "") != kb_id:
                continue
            kid = str(cfg.get("knowledge_id") or cfg.get("kid") or cfg.get("doc_name") or "")
            if kid == document_id:
                wiki_state, wiki_job_id, wiki_dur, wiki_err = st, str(jid), dur, err
                break
        except Exception:  # noqa: BLE001
            continue

    # ---- 向量化进度（PG kb_embedding vs doc_chunk 数，两步查跨库关联） ----
    embed_cnt = 0
    try:
        # ① MySQL：该文档的 chunk id 列表（doc_chunk.document_id）
        chunk_rows = db.execute(
            text("SELECT id FROM doc_chunk WHERE document_id=:d LIMIT 2000"), {"d": document_id}
        ).fetchall()
        chunk_ids = [r[0] for r in chunk_rows]
        # ② PG：kb_embedding 中这些 chunk 的命中数
        if chunk_ids:
            from api.db import get_knowledge_sessionmaker
            from api.models.knowledge import KbEmbedding

            kdb = get_knowledge_sessionmaker()()
            try:
                embed_cnt = (
                    kdb.execute(
                        select(func.count()).select_from(KbEmbedding).where(
                            KbEmbedding.chunk_id.in_(chunk_ids)
                        )
                    ).scalar()
                ) or 0
            finally:
                kdb.close()
    except Exception:  # noqa: BLE001 — 向量库不可达时进度显示 0
        pass

    # ---- wiki 步骤树（events summary，measure 嵌套还原的执行轨迹） ----
    steps_tree: list = []
    if wiki_job_id:
        try:
            import json as _json
            from api.config import get_settings

            ev_path = os.path.join(
                get_settings().kb_storage_dir, f"logs/events/{wiki_job_id}.summary.json"
            )
            if os.path.isfile(ev_path):
                with open(ev_path, encoding="utf-8") as f:
                    ev = _json.load(f)
                steps_tree = ev.get("tree") or []
        except Exception:  # noqa: BLE001
            steps_tree = []

    chunk_total = int(doc[4] or 0)
    embed_state = "SUCCESS" if (chunk_total > 0 and embed_cnt >= chunk_total) else (
        "RUNNING" if embed_cnt > 0 else "PENDING" if chunk_total > 0 else "NONE"
    )
    stages = [
        {
            "key": "parse", "label": "文档解析", "job_id": f"DOC_{document_id}",
            "state": parse_job[0] if parse_job else "NONE",
            "duration_ms": parse_job[1] if parse_job else None,
            "error": parse_job[2] if parse_job else None,
            "detail": f"{chunk_total} 分块" if chunk_total else "",
        },
        {
            "key": "embedding", "label": "向量化",
            "state": embed_state,
            "detail": f"已嵌入 {embed_cnt}/{chunk_total}" if chunk_total else "",
        },
        {
            "key": "wiki", "label": "Wiki 构建",
            "job_id": wiki_job_id, "state": wiki_state or "NONE",
            "duration_ms": wiki_dur, "error": wiki_err,
            "steps": steps_tree,
        },
    ]
    return {
        "success": True,
        "data": {
            "file_name": doc[0], "parse_state": doc[2],
            "stages": stages, "wiki_job_id": wiki_job_id,
        },
    }

@router.get("/{kb_id}/wiki")
def get_wiki(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_tree(db, kb_id)}


@router.get("/{kb_id}/wiki/graph")
def get_wiki_graph_route(
    kb_id: str,
    mode: str = "overview",
    center: str = "",
    depth: int = 1,
    limit: int = 200,
    types: str = "",
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """Wiki 知识图谱（wiki_page + wiki_link，不依赖 Neo4j）。

    mode=overview 全库图；mode=ego 以 center slug 为中心 depth 跳邻域。
    types 逗号分隔过滤 page_type（entity/concept/summary）。
    """
    _kb_visible(kb_id, caller, db)
    type_list = [t for t in types.split(",") if t.strip()] if types else []
    data = wiki_graph(
        db, kb_id, mode=mode, center=center, depth=depth, limit=limit, types=type_list
    )
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/branch")
def get_wiki_branch(
    kb_id: str,
    folder_id: str = "",
    page: int = Query(1, ge=1),
    page_size: int = 50,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """懒加载目录分支（对齐 WeKnora 侧栏）：folder_id='' 返回根级直接子项，展开时按 folder 取。"""
    _kb_visible(kb_id, caller, db)
    page_size = min(max(page_size, 1), 200)
    data = wiki_branch(db, kb_id, folder_id or "", max(page, 1), page_size)
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/folders")
def get_wiki_folders(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    """全量目录元数据（轻量；懒加载树的深链定位父链 / 管理面板用）。"""
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_folders(db, kb_id)}


@router.get("/{kb_id}/wiki/pages/{slug}")
def get_wiki_page_route(kb_id: str, slug: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    _kb_visible(kb_id, caller, db)
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


# ---- 文档多级目录（doc_folder + kb_document.folder_id）----
# 决策（2026-10-10）：单归属 / 递归子树浏览 / 非空禁删 / 仅浏览不参与检索


@router.get("/{kb_id}/doc-folders")
def get_doc_folders(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    """全量文档目录元数据（轻量；懒加载树深链定位 / 上传选目录下拉用）。"""
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": doc_folders(db, kb_id)}


@router.get("/{kb_id}/doc-folders/branch")
def get_doc_branch(
    kb_id: str,
    folder_id: str = "",
    page: int = Query(1, ge=1),
    page_size: int = 50,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """懒加载文档目录分支：folder_id='' 返回根级直接子目录 + 直接子文档。"""
    _kb_visible(kb_id, caller, db)
    page_size = min(max(page_size, 1), 200)
    data = doc_branch(db, kb_id, folder_id or "", max(page, 1), page_size)
    return {"success": True, "data": data}


@router.post("/{kb_id}/doc-folders")
def create_doc_folder(
    kb_id: str,
    req: DocFolderCreate,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    try:
        data = doc_create_folder(db, kb_id, req.model_dump(exclude_none=True), user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.put("/{kb_id}/doc-folders/{folder_id}")
def update_doc_folder(
    kb_id: str,
    folder_id: str,
    req: DocFolderUpdate,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    try:
        data = doc_update_folder(db, kb_id, folder_id, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 400, detail=str(e))
    return {"success": True, "data": data}


@router.delete("/{kb_id}/doc-folders/{folder_id}")
def delete_doc_folder(
    kb_id: str,
    folder_id: str,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    try:
        data = doc_delete_folder(db, kb_id, folder_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.put("/{kb_id}/documents/move")
def move_documents(
    kb_id: str,
    req: DocMoveRequest,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """批量移动文档到目录（folder_id="" = 移出所有目录到根层级）。"""
    _kb_manage(kb_id, caller, db)
    try:
        data = doc_move_documents(db, kb_id, req.document_ids, req.folder_id or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/stats")
def get_wiki_stats(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_stats(db, kb_id)}


@router.post("/{kb_id}/wiki/pages/batch")
def create_wiki_pages_batch(
    kb_id: str,
    req: list[WikiPageCreate],
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """批量创建 wiki 页面（一次事务；技能构建批量写，替代逐页 POST）。

    单页失败不阻塞其余；返回 created/errors 供调用方定位。
    """
    _kb_manage(kb_id, caller, db)
    created = 0
    errors: list[dict] = []
    for i, item in enumerate(req):
        try:
            wiki_create_page(
                db,
                kb_id,
                {
                    "slug": item.slug,
                    "title": item.title,
                    "content": item.content,
                    "page_type": item.page_type,
                    "folder_id": item.folder_id,
                    "summary": item.summary,
                    "source_refs": item.source_refs or [],
                },
                user_id=user_id,
            )
            created += 1
        except Exception as exc:  # noqa: BLE001 - 单页失败不阻塞批量
            errors.append({"index": i, "slug": item.slug, "error": str(exc)[:120]})
    db.commit()
    return {"success": True, "data": {"created": created, "errors": errors}}


@router.post("/{kb_id}/wiki/pages")
def create_wiki_page(
    kb_id: str,
    req: WikiPageCreate,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
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
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
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
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
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
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
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
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
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
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    try:
        data = wiki_delete_folder(db, kb_id, folder_id)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/rebuild-links")
def rebuild_wiki_links(
    kb_id: str,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_manage(kb_id, caller, db)
    return {"success": True, "data": wiki_rebuild_links(db, kb_id)}


@router.get("/{kb_id}/wiki/lint")
def lint_wiki(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_lint(db, kb_id)}


@router.get("/{kb_id}/wiki/search")
def search_wiki(
    kb_id: str,
    q: str = Query("", max_length=500),
    limit: int = Query(20, ge=1, le=100),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """Wiki 页面内搜索：按标题/内容 ilike 匹配页面（区别于 /search 的混合检索）。"""
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_search(db, kb_id, q, limit)}


@router.get("/{kb_id}/wiki/logs")
def get_wiki_logs(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    """Wiki 操作日志（倒序）。"""
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_list_logs(db, kb_id)}


@router.get("/{kb_id}/wiki/index")
def get_wiki_index(kb_id: str, caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db)) -> dict:
    """Wiki 索引：目录树 + 类型统计 + 最近更新。"""
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_index(db, kb_id)}


@router.get("/{kb_id}/wiki/feedback")
def list_all_wiki_feedback(
    kb_id: str,
    status: str | None = None,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """全库反馈列表（可按状态过滤，倒序）。"""
    _kb_visible(kb_id, caller, db)
    data = wiki_list_feedback(db, kb_id, slug="")
    if status:
        data["items"] = [it for it in data["items"] if it["status"] == status]
        data["total"] = len(data["items"])
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/pages/{slug}/feedback")
def submit_wiki_feedback(
    kb_id: str,
    slug: str,
    req: WikiFeedbackCreate,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """提交页面反馈（helpful=有帮助 / issue=问题上报）。"""
    _kb_manage(kb_id, caller, db)
    data = wiki_submit_feedback(
        db, kb_id, slug, user_id, req.feedback_type, req.content
    )
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/pages/{slug}/feedback")
def list_wiki_feedback(
    kb_id: str,
    slug: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """某页面的反馈列表（倒序）。"""
    _kb_visible(kb_id, caller, db)
    return {"success": True, "data": wiki_list_feedback(db, kb_id, slug=slug)}


@router.put("/{kb_id}/wiki/feedback/{feedback_id}/status")
def update_wiki_feedback_status(
    kb_id: str,
    feedback_id: str,
    req: WikiFeedbackStatusUpdate,
    user_id: str = Depends(_require_wiki_user),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """更新反馈状态（open/resolved/ignored）。"""
    _kb_manage(kb_id, caller, db)
    try:
        data = wiki_update_feedback_status(db, kb_id, feedback_id, req.status)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": data}


@router.post("/{kb_id}/search")
def search_route(
    kb_id: str,
    req: SearchRequest,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    _kb_visible(kb_id, caller, db)
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


@router.get("/{kb_id}/documents/{document_id}/download")
def download_document(
    kb_id: str,
    document_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
):
    """按文档下载原文件（对齐 WeKnora 文档下载；本地优先，MinIO 流式回退）。"""
    _kb_visible(kb_id, caller, db)
    from api.models.knowledge import KbDocument

    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 优先走 SysFile 关联行（复用 files 模块的本地/s3 读取）
    if doc.sys_file_id:
        f = db.execute(
            select(SysFile).where(SysFile.id == doc.sys_file_id)
        ).scalars().first()
        if f:
            local_path = _local_fallback_path(f.storage_path)
            if os.path.exists(local_path):
                return FileResponse(
                    local_path,
                    media_type=f.mime_type or "application/octet-stream",
                    filename=f.file_name,
                )
            if not _is_s3_row(f):
                raise HTTPException(status_code=404, detail="物理文件缺失")
            try:
                data = _read_stored_bytes(f)
            except Exception:  # noqa: BLE001
                raise HTTPException(status_code=404, detail="物理文件缺失") from None
            return StreamingResponse(
                io.BytesIO(data),
                media_type=f.mime_type or "application/octet-stream",
                headers={"Content-Disposition": _content_disposition(f.file_name)},
            )
    # 兜底：按文档 storage_path 读取（storage 层统一处理本地/MinIO；
    # 修复 MinIO 上传未建 SysFile 行时 download「物理文件缺失」404，
    # 以及此前缩进落在 sys_file_id 分支内、sys_file_id 为空时隐式返回 null 的问题）
    if doc.storage_path:
        from api.services import storage as storage_svc

        try:
            data = storage_svc.get_bytes(doc.storage_path)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=404, detail="物理文件缺失") from None
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": _content_disposition(
                    doc.file_name or "document.bin"
                )
            },
        )
    raise HTTPException(status_code=404, detail="物理文件缺失")


class ReparseRequest(BaseModel):
    """重新解析可携带文件级处理配置（对齐 WeKnora 重解析换配置）。"""
    process_config: str | None = None


@router.post("/{kb_id}/documents/{document_id}/reparse")
def reparse_document(
    kb_id: str,
    document_id: str,
    body: ReparseRequest | None = None,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """重新解析文档：清旧 chunks + 重置状态 + 重新入队（对齐 WeKnora 重新解析）。

    可选 body.process_config：JSON 字符串（文件级处理配置，形如
    {"chunking": {...}}），传入则更新 kb_document.process_config，本次及后续
    解析均按新配置走；不传则沿用文档原配置。
    """
    _kb_manage(kb_id, caller, db)
    from api.models.knowledge import DocChunk, KbDocument

    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 可选：更新文件级配置（合法 JSON 才写入；空串/非法忽略保留原值）
    if body and body.process_config and (body.process_config or "").strip():
        try:
            parsed_pc = json.loads(body.process_config)
            if isinstance(parsed_pc, dict):
                doc.process_config = json.dumps(parsed_pc, ensure_ascii=False)
        except (TypeError, json.JSONDecodeError):
            logger.warning("reparse %s: invalid process_config ignored", document_id)

    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    doc.parse_state = "PENDING"
    doc.parse_error = None
    doc.chunk_count = 0
    db.commit()
    try:
        _enqueue_document_process(kb_id, document_id)
    except Exception as exc:  # noqa: BLE001
        # 入队失败不裸 500：回滚文档状态（已清 chunks + 置 PENDING），
        # 返回明确错误供前端提示，文档可再次重试。
        logger.exception("failed to enqueue reparse task for doc %s", document_id)
        db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
        db.commit()
        raise HTTPException(status_code=502, detail=f"重新解析入队失败: {exc}") from exc
    return {"success": True, "data": {"id": document_id, "parse_state": "PENDING"}}


@router.post("/{kb_id}/documents/{document_id}/summary")
def generate_doc_summary(
    kb_id: str,
    document_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """为文档生成 AI 摘要（用知识库配置的大语言模型 summary_model_id，对齐 WeKnora）。"""
    _kb_manage(kb_id, caller, db)
    try:
        data = generate_document_summary(db, kb_id, document_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, "data": data}


__all__ = ["router"]