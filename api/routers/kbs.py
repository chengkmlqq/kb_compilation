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
import re
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
    doc_folder_exists,
    doc_folders,
    doc_move_documents,
    doc_update_folder,
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
    get_document_detail,
    list_wiki_pages,
    tokenize_text,
    wiki_auto_fix,
    wiki_list_issues,
    wiki_log_write,
    wiki_lookup_by_title,
    wiki_move_page,
    wiki_sync_issues_from_lint,
    wiki_update_issue_status,
)
from api.services.kb_admin import _remove_local_file  # noqa: PLC2701 (same package helper)
from api.services.parser_registry import (
    SUPPORTED_ARCHIVE_TYPES,
    SUPPORTED_UPLOAD_ACCEPT,
    SUPPORTED_UPLOAD_TYPES,
    archive_entry_parts,
    ext_of,
    is_archive_ext,
    is_hidden_or_junk,
    is_supported_ext,
    unsupported_type_detail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kbs", tags=["kbs"])

settings = get_settings()

MAX_UPLOAD_BYTES = 600 * 1024 * 1024  # 600 MB（2026-10-16 心电图 pdf转word 大文件 258MB，原 50MB 超限）
# 压缩包上传（自动解包）限制：条目数 + 解压后总量（防解压炸弹）
MAX_ARCHIVE_ENTRIES = 2000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * MAX_UPLOAD_BYTES


class KBCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    label: str | None = None
    description: str | None = None
    indexing_strategy: dict | None = None
    scope: str = "system"
    team_name: str | None = None
    # ── WeKnora 对齐配置 ──
    type: str = "document"  # document（问答对型已下线）
    custom_wiki_generation: bool = False
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
    storage_backend_id: str | None = None
    vector_store_id: str | None = None
    configs: dict | None = None  # wiki/extract/vlm/asr/storage 等 JSON 配置
    ontology_schema_name: str | None = None  # 绑定本体 Schema（抽取分类结构，多领域）
    build_tape_id: str | None = None  # 构建引擎 = 服务编排中已发布的编排程序（kb_tape.id）
    summary_strategy: str | None = None  # 上传摘要策略: off/auto/stuff/mapreduce/refine（默认 auto）


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
    build_tape_id: str | None = None
    summary_strategy: str | None = None


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
    folder_ids: list | None = Field(default=None)  # 多目录挂载（首项为主目录）
    source_refs: list | None = Field(default=None)


class WikiPageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    page_type: str | None = Field(default=None, max_length=32)
    content: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    folder_id: str | None = Field(default=None)
    folder_ids: list | None = Field(default=None)  # 多目录挂载全量替换
    status: str | None = Field(default=None, max_length=32)


class WikiFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)


class WikiFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)


class WikiLookupByTitleRequest(BaseModel):
    titles: list[str] = Field(..., max_length=1000)
    page_types: list[str] | None = None


class WikiPageMoveRequest(BaseModel):
    folder_id: str = Field(default="", max_length=64)


class WikiLogWriteRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    knowledge_id: str | None = None
    doc_title: str | None = None
    summary: str | None = None
    page_slugs: list[str] | None = None


class WikiIssueStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|ignored|resolved)$")


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


@router.get("/build-engines")
def build_engines(
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> dict:
    """构建引擎下拉数据源：服务编排中已发布的编排程序（status=effective）。

    知识库创建表单「构建引擎」选项；返回简表（id/tape_name/tape_label/tape_descr）。
    """
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity:
        raise HTTPException(status_code=401, detail="未登录")
    from api.models.orchestration import Tape
    from sqlalchemy import select

    rows = (
        db.execute(
            select(Tape)
            .where(Tape.status == "effective")
            .order_by(Tape.updated_at.desc())
        )
        .scalars()
        .all()
    )
    return {
        "success": True,
        "data": {
            "items": [
                {
                    "id": t.id,
                    "tape_name": t.tape_name,
                    "tape_label": t.tape_label or "",
                    "tape_descr": t.tape_descr or "",
                    # 2026-10-15 功能标签：summary（摘要）/ kb_build（知识构建）/ general（其他）
                    "tape_type": t.tape_type or "general",
                }
                for t in rows
            ],
            "total": len(rows),
        },
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
            build_tape_id=req.build_tape_id,
            summary_strategy=req.summary_strategy,
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
            select(func.count())
            .select_from(WikiPage)
            .where(WikiPage.kb_id == kb.id, WikiPage.status == "active")
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
    # 只取提到本 KB 的任务：kb_id 内嵌在 task_params JSON 文本里，先用 LIKE 粗筛
    # （下方 json.loads 再精确校验 cfg.kb_id），并加 limit 兜底。
    # 原实现无条件全表扫 WIKI 任务并逐行 json.loads，随任务累积线性变慢 —— 而本函数
    # 每次文档列表请求都会执行（文档解析中前端每 3s 轮询一次），是详情页卡顿主因之一。
    jobs = db.execute(
        select(Job.id, Job.task_params, Job.state, Job.duration_ms)
        .where(Job.id.like("WIKI%"), Job.task_params.like(f"%{kb_id}%"))
        .order_by(Job.create_time.desc())
        .limit(3000)
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
    # 编排构建引擎任务（KbOrchestrationRunTask，id=ORCH_RUN_*，2026-10-12）：
    # task_params 只有 runId，经 kb_tape_run.inputs「反查」kb/document 定位，
    # 使文档列表能看到编排构建状态（此前编排跑通但列表无状态，像没生成）。
    from api.models.orchestration import TapeRun

    orch_jobs = db.execute(
        select(Job.id, Job.task_params, Job.duration_ms)
        .where(Job.id.like("ORCH_RUN%"))
        .order_by(Job.create_time.desc())
        .limit(2000)
    ).all()
    if orch_jobs:
        run_ids = []
        for jid, tp, _dur in orch_jobs:
            try:
                rid = (json.loads(tp or "{}").get("runId") or "").strip()
            except Exception:  # noqa: BLE001
                rid = ""
            if rid:
                run_ids.append(rid)
        run_rows = {}
        if run_ids:
            for run in db.execute(
                select(TapeRun.id, TapeRun.status, TapeRun.inputs).where(
                    TapeRun.id.in_(run_ids)
                )
            ).all():
                run_rows[str(run.id)] = (run.status, run.inputs or {})
        for jid, _tp, dur in orch_jobs:
            try:
                rid = str(json.loads(_tp or "{}").get("runId") or "")
            except Exception:  # noqa: BLE001
                rid = ""
            if rid not in run_rows:
                continue
            status, inputs = run_rows[rid]
            if str(inputs.get("kb_id") or "") != kb_id:
                continue
            kid = str(inputs.get("document_id") or inputs.get("doc_id") or "")
            if not kid or kid in by_doc:
                continue
            by_doc[kid] = {
                "state": str(status).upper(),
                "duration_ms": dur,
                "job_id": str(jid),
            }
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
    build_tape_id: str = Form(default=""),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),

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
    build_tape_id: 可选的**文档级构建引擎**（服务编排中已发布的编排），
    覆盖知识库默认构建引擎；留空 = 用 KB 默认。
    """
    _kb_manage(kb_id, caller, db)

    # 目录归属校验（folder_id 走 query 参数，见前端 apiUploadDocumentWithProgress）：
    # 非法/跨库目录直接拒绝，避免落出「不属于任何目录」的孤儿文档。
    folder_id = (folder_id or "").strip()
    if not doc_folder_exists(db, kb_id, folder_id):
        raise HTTPException(status_code=400, detail=f"目录不存在: {folder_id}")

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

    # --- 格式门禁（2026-10-20）：只接受系统真能解析的格式 ---
    # 原先任意扩展名都会落库并排队，worker 解析时才抛「Unsupported file type」→ 文档 FAILED，
    # 属于「传了又处理不了」。白名单与 docreader 注册表同源（见 parser_registry.SUPPORTED_UPLOAD_TYPES）。
    doc_ext = ext_of(fname)
    if not is_supported_ext(doc_ext):
        raise HTTPException(status_code=400, detail=unsupported_type_detail(doc_ext))

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
        build_tape_id=build_tape_id.strip() or None,

    )

    # --- enqueue Celery job (modo_job row + send_task) ---
    try:
        _enqueue_document_process(kb_id, doc.id, caller_identity=x_next_identity or "")
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


@router.get("/file-types/supported")
def supported_file_types() -> dict:
    """系统可处理的文档格式（上传白名单，2026-10-20）。

    与后端门禁 / 前端 accept 与预校验同源（都读 parser_registry.SUPPORTED_UPLOAD_TYPES，
    该清单与 docreader 内置解析器注册表一致）——前端据此限制可选文件类型，避免「传了又处理不了」。
    """
    return {
        "success": True,
        "data": {
            "types": list(SUPPORTED_UPLOAD_TYPES),
            "accept": SUPPORTED_UPLOAD_ACCEPT,
            "archive_types": list(SUPPORTED_ARCHIVE_TYPES),
        },
    }


@router.post("/{kb_id}/documents/upload-archive")
async def upload_archive(
    kb_id: str,
    file: UploadFile,
    folder_id: str = Query(default="", max_length=64),
    keep_structure: bool = Query(default=True),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """上传 zip 压缩包并**自动解包**入库（2026-10-20）。

    - 包内目录结构 → 知识库文档目录（同级同名复用；keep_structure=false 时全部落到 folder_id）；
    - 跳过：目录项、隐藏/系统垃圾文件（. 开头 / Thumbs.db 等）、空文件、非白名单格式、超大文件；
    - 每个文件仍走「存储 → kb_document(PENDING) → Celery 解析」同一条链路（与单文件上传一致）；
    - 返回 created + skipped 统计，前端据此提示「已解压上传 N 个、跳过 M 个」。
    """
    import zipfile

    _kb_manage(kb_id, caller, db)

    folder_id = (folder_id or "").strip()
    if not doc_folder_exists(db, kb_id, folder_id):
        raise HTTPException(status_code=400, detail=f"目录不存在: {folder_id}")

    fname = (file.filename or "").strip()
    archive_ext = ext_of(fname)
    if not is_archive_ext(archive_ext):
        suffix = f"（.{archive_ext}）" if archive_ext else ""
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 zip 压缩包自动解包；当前文件：{fname or '未命名'}{suffix}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="压缩包内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"压缩包超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400, detail="压缩包无法解压（不是有效的 zip 或文件已损坏）"
        ) from exc

    from api.services import storage as storage_svc

    created: list[dict] = []
    errors: list[str] = []
    skipped = {"hidden": 0, "empty": 0, "unsupported": 0, "oversize": 0, "failed": 0}
    unsupported_types: set[str] = set()
    existing: dict[str, str] = {}
    folder_seed_count = 0

    try:
        file_infos = [i for i in zf.infolist() if not (i.filename or "").endswith("/")]
        if len(file_infos) > MAX_ARCHIVE_ENTRIES:
            raise HTTPException(
                status_code=400,
                detail=f"压缩包内文件过多（{len(file_infos)} 个，上限 {MAX_ARCHIVE_ENTRIES} 个），请拆分后上传",
            )
        total_uncompressed = sum(i.file_size for i in file_infos)
        if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"压缩包解压后过大（约 {total_uncompressed // (1024 * 1024)}MB，上限 "
                f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES // (1024 * 1024)}MB），请拆分后上传",
            )

        # 目录映射（同级同名复用）："(parent_id)\0(name)" → folder_id
        existing = {f"{f['parent_id']}\0{f['name']}": f["id"] for f in doc_folders(db, kb_id)}
        folder_seed_count = len(existing)
        user_id = str(caller.get("user_id") or "")

        def _ensure_folder(segments: list[str]) -> str:
            parent = folder_id
            for seg in segments:
                key = f"{parent}\0{seg}"
                fid = existing.get(key)
                if not fid:
                    fid = doc_create_folder(db, kb_id, {"name": seg, "parent_id": parent}, user_id)["id"]
                    existing[key] = fid
                parent = fid
            return parent

        for info in file_infos:
            raw_name = (info.filename or "").replace("\\", "/")
            parts = archive_entry_parts(raw_name)
            if not parts:
                continue  # 非法路径（含 .. / 全空段）直接丢弃
            if is_hidden_or_junk(raw_name):
                skipped["hidden"] += 1
                continue
            base_name = parts[-1]
            entry_ext = ext_of(base_name)
            if not is_supported_ext(entry_ext):
                skipped["unsupported"] += 1
                unsupported_types.add(entry_ext or base_name)
                continue
            if info.file_size == 0:
                skipped["empty"] += 1
                continue
            if info.file_size > MAX_UPLOAD_BYTES:
                skipped["oversize"] += 1
                continue

            target_folder = _ensure_folder(parts[:-1]) if keep_structure else folder_id
            try:
                data = zf.read(info)
            except Exception as exc:  # noqa: BLE001 - 单个坏条目不阻断整包
                logger.warning("archive entry read failed: %s (%s)", raw_name, exc)
                skipped["failed"] += 1
                errors.append(f"{base_name}: 读取失败")
                continue

            doc_id = uuid.uuid4().hex
            storage_path = storage_svc.resolve_new_path(f"kb_documents/{kb_id}/{doc_id}.{entry_ext}")
            try:
                storage_svc.put_bytes(storage_path, data)
            except Exception:  # noqa: BLE001
                logger.exception("archive entry store failed: %s", raw_name)
                skipped["failed"] += 1
                errors.append(f"{base_name}: 存储失败")
                continue

            doc = create_document(
                db,
                kb_id=kb_id,
                file_name=base_name,
                file_ext=entry_ext,
                file_size=len(data),
                storage_path=storage_path,
                folder_id=target_folder,
            )
            try:
                _enqueue_document_process(kb_id, doc.id, caller_identity=x_next_identity or "")
            except Exception:  # noqa: BLE001 - 保留 PENDING 行，可手动重试
                logger.exception("failed to enqueue archived doc %s", doc.id)
            created.append({"id": doc.id, "file_name": doc.file_name, "folder_id": target_folder})
    finally:
        zf.close()

    return {
        "success": True,
        "data": {
            "archive_name": fname,
            "created_count": len(created),
            "created": created,
            "folders_created": max(0, len(existing) - folder_seed_count),
            "skipped": skipped,
            "unsupported_types": sorted(t for t in unsupported_types if t),
            "errors": errors[:5],
        },
    }


def _enqueue_document_process(
    kb_id: str,
    document_id: str,
    caller_identity: str = "",
    fresh_chain: bool = False,
) -> None:
    """Create-or-reset a modo_job row (trigger=API) and send the Celery task.

    Runs on the framework session (modo_job lives on the shared store);
    mirrors the scheduler's CRON dispatch but with trigger_type=API.

    caller_identity: 上传操作者的 x-next-identity cookie 原值，随任务参数
    透传给下游（编排脚本经 weknora_rpc 调平台 API 需登录身份，2026-10-12）。

    幂等：job_id 固定为 DOC_{document_id}，重复入队（上传重试）时该行已存在
    → 直接 insert 会撞主键(IntegrityError 1062)导致入队失败。改为先查后改——
    存在则重置为 PENDING 并清空上一轮执行痕迹，不存在则新建。

    fresh_chain=True（2026-10-15 重新解析专用）：不复用旧链头行，而是新建
    轮次链头（DOC_{id} 首次；重解析 DOC_{id}_R2/_R3...），使新一轮解析触发的
    摘要/构建编排子任务挂到新链头下，任务监控页每轮独立、不混入历史链头的
    旧子任务。旧链头行保留原状态作为历史。
    """
    from worker.celery_app import celery_app

    if fresh_chain:
        job_id = _next_chain_job_id(document_id)
    else:
        job_id = f"DOC_{document_id}"
    task_class = "KbDocumentProcessTask"
    task_params = json.dumps(
        {"kbId": kb_id, "documentId": document_id, "cookie": caller_identity},
        ensure_ascii=False,
    )

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


def _next_chain_job_id(document_id: str) -> str:
    """重新解析的新链头 job_id：DOC_{id} 已存在则顺延轮次 DOC_{id}_R2/_R3...。

    首次（无任何 DOC_{id} 前缀行）返回 DOC_{id}；已有则取现有最大轮次 +1。
    并发极端场景下若仍撞主键（两次 reparse 同时触发），insert 会抛
    IntegrityError，reparse 端点已有 502 回滚处理，用户重试即得新轮次。
    """
    from api.db import get_sessionmaker

    base = f"DOC_{document_id}"
    framework_db = get_sessionmaker()()
    try:
        rows = framework_db.execute(
            select(Job.id).where(Job.id.like(f"{base}%"))
        ).scalars().all()
    finally:
        framework_db.close()
    if not rows:
        return base
    max_round = 1
    for rid in rows:
        # 形态：DOC_{id} / DOC_{id}_R{n}
        m = re.match(rf"^{re.escape(base)}_R(\d+)$", str(rid))
        if m:
            max_round = max(max_round, int(m.group(1)))
    return f"{base}_R{max_round + 1}"


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

    # ---- 解析任务（DOC_<doc_id>[_R{n}]，任务链上唯一的解析+嵌入任务；
    #      2026-10-15 重新解析会新建轮次链头 → 取最新一条） ----
    parse_job = db.execute(
        select(Job.id, Job.state, Job.duration_ms, Job.error_message, Job.start_time, Job.end_time)
        .where(Job.id.like(f"DOC_{document_id}%"))
        .order_by(Job.create_time.desc())
        .limit(1)
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

    # ---- 编排构建兜底（2026-10-16）：绑定构建引擎（build_tape_id）时 wiki 由
    #      编排承接，无 WIKI_SKILL 任务；回退查 ORCH_RUN_* job → kb_tape_run 反查
    #      inputs.document_id（与 _attach_wiki_build_status 同源模式）。----
    if not wiki_job_id:
        from api.models.orchestration import TapeRun

        orch_jobs = db.execute(
            select(Job.id, Job.task_params, Job.state, Job.duration_ms, Job.error_message)
            .where(Job.id.like("ORCH_RUN%"))
            .order_by(Job.create_time.desc())
            .limit(2000)
        ).all()
        run_ids: list[str] = []
        for jid, tp, _st, _dur, _err in orch_jobs:
            try:
                rid = str(json.loads(tp or "{}").get("runId") or "").strip()
            except Exception:  # noqa: BLE001
                rid = ""
            if rid:
                run_ids.append(rid)
        run_rows: dict[str, tuple] = {}
        if run_ids:
            for run in db.execute(
                select(TapeRun.id, TapeRun.status, TapeRun.inputs, TapeRun.error).where(
                    TapeRun.id.in_(run_ids)
                )
            ).all():
                run_rows[str(run.id)] = (run.status, run.inputs or {}, run.error)
        for jid, tp, _st, dur, err in orch_jobs:
            try:
                rid = str(json.loads(tp or "{}").get("runId") or "").strip()
            except Exception:  # noqa: BLE001
                rid = ""
            if rid not in run_rows:
                continue
            run_status, run_inputs, run_error = run_rows[rid]
            if str(run_inputs.get("kb_id") or "") != kb_id:
                continue
            kid = str(run_inputs.get("document_id") or run_inputs.get("doc_id") or "")
            if kid != document_id:
                continue
            # run.status 库值小写（queued/running/success/failed/cancelled）→
            # 转大写对齐前端 timeline 契约（与 WIKI job 的 Job.state 大写一致）。
            wiki_state, wiki_job_id, wiki_dur, wiki_err = (
                str(run_status).upper(), str(jid), dur, (run_error or err),
            )
            break

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
            "key": "parse", "label": "文档解析",
            "job_id": parse_job[0] if parse_job else f"DOC_{document_id}",
            "state": parse_job[1] if parse_job else "NONE",
            "duration_ms": parse_job[2] if parse_job else None,
            "error": parse_job[3] if parse_job else None,
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
                    "folder_ids": item.folder_ids,
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


# 转换后的 DOCX MIME（前端 docx-preview 只认 OOXML）
_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
# 需要服务端转 DOCX 才能渲染的 Word 扩展名
_WORD_PREVIEW_CONVERT_EXTS = ("doc", "rtf", "odt")

# 预览 MIME 显式映射（优先于 mimetypes.guess_type，避免平台差异漏判：
# md/csv/tsv 在部分平台无映射，csv 甚至会被判成 application/vnd.ms-excel）
_PREVIEW_MIME_OVERRIDES = {
    "pdf": "application/pdf",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "txt": "text/plain",
    "log": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "json": "application/json",
    "svg": "image/svg+xml",
    "docx": _DOCX_MIME,
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "opus": "audio/ogg",
}


def _preview_mime(file_name: str, stored_mime: str | None) -> str:
    """预览响应的 MIME：优先按扩展名推断，回落系统 guess_type，最后用存储值。

    背景：KB 文档上传只写 kb_document.storage_path、不建 SysFile 行，预览读取时
    mime 恒为 application/octet-stream——iframe 加载 octet-stream blob 会触发
    下载而不是内联渲染（PDF 预览变下载的根因）。预览必须返回正确的媒体类型。
    """
    import mimetypes

    ext = Path(file_name or "").suffix.lstrip(".").lower()
    if ext in _PREVIEW_MIME_OVERRIDES:
        return _PREVIEW_MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(file_name or "")
    if guessed:
        return guessed
    return stored_mime or "application/octet-stream"


def _read_document_bytes(db: Session, doc) -> tuple[bytes, str, str]:
    """读取文档原始字节（本地 / MinIO 统一），返回 (data, file_name, mime_type)。"""
    if doc.sys_file_id:
        f = db.execute(
            select(SysFile).where(SysFile.id == doc.sys_file_id)
        ).scalars().first()
        if f:
            file_name = f.file_name or doc.file_name or "document.bin"
            mime = f.mime_type or "application/octet-stream"
            local_path = _local_fallback_path(f.storage_path)
            if os.path.exists(local_path):
                with open(local_path, "rb") as fh:
                    return fh.read(), file_name, mime
            if not _is_s3_row(f):
                raise HTTPException(status_code=404, detail="物理文件缺失")
            try:
                return _read_stored_bytes(f), file_name, mime
            except Exception:  # noqa: BLE001
                raise HTTPException(status_code=404, detail="物理文件缺失") from None
    if doc.storage_path:
        from api.services import storage as storage_svc

        try:
            data = storage_svc.get_bytes(doc.storage_path)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=404, detail="物理文件缺失") from None
        return data, doc.file_name or "document.bin", "application/octet-stream"
    raise HTTPException(status_code=404, detail="物理文件缺失")


@router.get("/{kb_id}/documents/{document_id}/preview")
def preview_document(
    kb_id: str,
    document_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
):
    """文档预览字节：旧版/非 OOXML Word（.doc/.rtf/.odt）用 LibreOffice 转成
    .docx 后返回，其余类型返回原文件（本地优先 / MinIO 流式回退）。

    docx-preview 只能渲染 OOXML；服务端先转换，浏览器才能在线预览旧版文档。
    """
    _kb_visible(kb_id, caller, db)
    from api.models.knowledge import KbDocument

    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    ext = (doc.file_ext or "").lstrip(".").lower()
    if ext in _WORD_PREVIEW_CONVERT_EXTS:
        from docreader.parser.doc_convert import (
            convert_doc_to_docx_bytes,
            needs_doc_to_docx_conversion,
        )

        data, file_name, mime = _read_document_bytes(db, doc)
        # 扩展名是 .doc 但内容实为 OOXML（WPS/Word 另存）→ 原样返回即可渲染
        if needs_doc_to_docx_conversion(data, ext):
            converted = convert_doc_to_docx_bytes(data, suffix=f".{ext}")
            if converted is None:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        "该 Word 文档为旧版/非 OOXML 格式，服务端缺少 LibreOffice "
                        "无法转换，请下载原文件查看。"
                    ),
                )
            stem = Path(file_name).stem or "document"
            return StreamingResponse(
                io.BytesIO(converted),
                media_type=_DOCX_MIME,
                headers={
                    "Content-Disposition": _content_disposition(
                        f"{stem}.docx", "inline"
                    )
                },
            )
        return StreamingResponse(
            io.BytesIO(data),
            media_type=_preview_mime(file_name, mime),
            headers={
                "Content-Disposition": _content_disposition(file_name, "inline")
            },
        )

    # 其他类型：与 download 一致（本地文件走 FileResponse，MinIO 走字节流）
    if doc.sys_file_id:
        f = db.execute(
            select(SysFile).where(SysFile.id == doc.sys_file_id)
        ).scalars().first()
        if f:
            local_path = _local_fallback_path(f.storage_path)
            if os.path.exists(local_path):
                # 预览走 inline：不传 filename（starlette FileResponse 传 filename
                # 会强制 attachment，浏览器/iframe 会转为下载）
                return FileResponse(
                    local_path,
                    media_type=_preview_mime(f.file_name, f.mime_type),
                    headers={
                        "Content-Disposition": _content_disposition(
                            f.file_name, "inline"
                        )
                    },
                )
            if not _is_s3_row(f):
                raise HTTPException(status_code=404, detail="物理文件缺失")
    data, file_name, mime = _read_document_bytes(db, doc)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=_preview_mime(file_name, mime),
        headers={"Content-Disposition": _content_disposition(file_name, "inline")},
    )


class ReparseRequest(BaseModel):
    """重新解析可携带文件级处理配置（对齐 WeKnora 重解析换配置）。

    process_config: JSON 字符串（chunking/parser_engine_rules/summary_strategy），
    传入则更新 kb_document.process_config。
    build_tape_id: 文档级构建引擎（重选覆盖 KB 默认；传空串 = 清除，改走 KB 默认）。
    """

    process_config: str | None = None
    build_tape_id: str | None = None


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

    # 可选：更新文档级构建引擎（None = 未传保持原值；空串 = 清除改走 KB 默认）
    if body and body.build_tape_id is not None:
        new_tape = str(body.build_tape_id or "").strip() or None
        if new_tape:
            from api.services.kb_admin import _validate_build_tape_ref

            try:
                _validate_build_tape_ref(db, new_tape)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        doc.build_tape_id = new_tape

    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    doc.parse_state = "PENDING"
    doc.parse_error = None
    doc.chunk_count = 0
    db.commit()
    try:
        # 2026-10-15 重新解析建新链头（DOC_{id}_R{n}），避免新一轮
        # 摘要/构建编排子任务挂到旧链头下、新旧混在一起。
        _enqueue_document_process(kb_id, document_id, fresh_chain=True)
    except Exception as exc:  # noqa: BLE001
        # 入队失败不裸 500：回滚文档状态（已清 chunks + 置 PENDING），
        # 返回明确错误供前端提示，文档可再次重试。
        logger.exception("failed to enqueue reparse task for doc %s", document_id)
        db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
        db.commit()
        raise HTTPException(status_code=502, detail=f"重新解析入队失败: {exc}") from exc
    return {"success": True, "data": {"id": document_id, "parse_state": "PENDING"}}


class AddTableRequest(BaseModel):
    """绑定一张数据源维度表到知识库（表→wiki，2026-10-10）。"""
    ds_name: str
    schema: str | None = None
    table_name: str
    sample_size: int | None = None  # 默认 500
    build_tape_id: str | None = None  # 文档级构建引擎（2026-10-12，空 = 用 KB 默认）


@router.get("/{kb_id}/tables/candidates")
def list_table_candidates(
    kb_id: str,
    ds_name: str = Query(default="", max_length=64),
    keyword: str = Query(default="", max_length=128),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """可绑定表清单（元数据采集已入库的 kb_metadata_table，按 ds_name/关键字过滤）。

    ⚠️ ds_name 语义 = 数据源 name（datagrid._find_entry 按 name 查；前端下拉也传
    name）。2026-10-11 修复：此前按 datasource_id 过滤/返回 id，导致绑定后
    table_ingest 按 name 找不到数据源。统一走 Datasource.name。
    """
    _kb_visible(kb_id, caller, db)
    from api.models.framework import Datasource
    from api.models.metadata import MetadataTable
    from sqlalchemy import select

    # 数据源 name → id 映射（过滤 + 回填 name）
    ds_id_by_name: dict[str, str] = {}
    if ds_name:
        ds_rows = db.execute(
            select(Datasource.id, Datasource.name).where(Datasource.name == ds_name)
        ).all()
        ds_id_by_name = {n: i for i, n in ds_rows}
        if not ds_id_by_name:
            return {"success": True, "data": []}

    q = select(MetadataTable).where(MetadataTable.state == "active")
    if ds_id_by_name:
        q = q.where(MetadataTable.datasource_id.in_(ds_id_by_name.values()))
    if keyword:
        q = q.where(MetadataTable.table_name.like(f"%{keyword}%"))
    rows = db.execute(q.order_by(MetadataTable.table_name).limit(500)).scalars().all()
    # 全量数据源 id→name（返回项用 name，前端绑定原样回传，worker 按 name 找）
    all_ds = db.execute(select(Datasource.id, Datasource.name)).all()
    ds_name_by_id = {i: n or "" for i, n in all_ds}
    return {
        "success": True,
        "data": [
            {
                "id": r.id,
                "ds_name": ds_name_by_id.get(r.datasource_id, r.datasource_id),
                "schema": r.schema_name,
                "table_name": r.table_name,
                "table_comment": r.table_comment,
                "row_count": r.row_count,
                "table_type": r.table_type,
            }
            for r in rows
        ],
    }


@router.post("/{kb_id}/tables")
def add_table_document(
    kb_id: str,
    body: AddTableRequest,
    caller: dict = Depends(_require_kb_caller),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """绑定一张表到知识库：建 KbDocument(source_type=table) + 入队 KbTableIngestTask。

    表可绑定到多个知识库（用户拍板 D7：允许重复绑定），每次绑定各建一行，
    互不影响；文档名 = 表名（可含 schema 前缀避免歧义）。
    """
    _kb_manage(kb_id, caller, db)
    from api.services.kb_admin import create_document

    if not body.ds_name or not body.table_name:
        raise HTTPException(status_code=400, detail="ds_name 与 table_name 必填")

    fname = body.table_name
    if body.schema:
        fname = f"{body.schema}.{body.table_name}"
    doc = create_document(
        db,
        kb_id=kb_id,
        file_name=fname,
        file_ext="table",
        file_size=None,
        source_type="table",
        ds_source_name=body.ds_name,
        ds_table_schema=body.schema or "",
        ds_table_name=body.table_name,
        row_count=None,
        build_tape_id=body.build_tape_id,
    )
    db.commit()

    # 入队表摄取任务（job_id = TABLE_{document_id}，幂等）
    from worker.celery_app import celery_app

    job_id = f"TABLE_{doc.id}"
    task_params = json.dumps(
        {
            "kbId": kb_id,
            "documentId": doc.id,
            "sampleSize": body.sample_size or 500,
            "cookie": x_next_identity or "",  # 操作者身份透传给编排脚本（2026-10-12）
        },
        ensure_ascii=False,
    )
    from api.db import get_sessionmaker
    from api.models.framework import Job

    fw = get_sessionmaker()()
    try:
        existing = fw.get(Job, job_id)
        if existing is None:
            fw.add(
                Job(
                    id=job_id,
                    task_id=doc.id,
                    task_class="KbTableIngestTask",
                    queue_name="default",
                    task_params=task_params,
                    trigger_type="API",
                    state="PENDING",
                )
            )
        else:
            existing.task_id = doc.id
            existing.task_class = "KbTableIngestTask"
            existing.queue_name = "default"
            existing.task_params = task_params
            existing.trigger_type = "API"
            existing.state = "PENDING"
            existing.start_time = None
            existing.end_time = None
            existing.duration_ms = None
            existing.error_message = None
        fw.commit()
    finally:
        fw.close()
    try:
        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="default",
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to enqueue table ingest; document kept as PENDING")

    return {
        "success": True,
        "data": {
            "id": doc.id,
            "file_name": doc.file_name,
            "source_type": "table",
            "parse_state": doc.parse_state,
        },
    }


@router.get("/{kb_id}/documents/{document_id}/sample")
def get_table_sample(
    kb_id: str,
    document_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """实时连库读表样例数据（表卡片/详情抽屉「预览」视图用，不落库）。"""
    _kb_visible(kb_id, caller, db)
    from api.models.knowledge import KbDocument
    from sqlalchemy import select

    doc = db.execute(
        select(KbDocument).where(KbDocument.id == document_id, KbDocument.kb_id == kb_id)
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if (doc.source_type or "file") != "table":
        raise HTTPException(status_code=400, detail="非表源文档")
    from worker.tasks.table_ingest import _load_table_rows

    try:
        rows, columns = _load_table_rows(db, doc, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"取数失败: {exc}") from exc
    return {"success": True, "data": {"columns": columns, "rows": rows, "total": len(rows)}}


# ── MCP 对齐补充（2026-10-11）：WeKnora admin-kb 工具所需端点 ──


@router.get("/{kb_id}/documents/{document_id}")
def get_document_detail_route(
    kb_id: str,
    document_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """单文档详情（对齐 WeKnora get_document）。"""
    _kb_visible(kb_id, caller, db)
    data = get_document_detail(db, kb_id, document_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {document_id}")
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/pages")
def list_wiki_pages_route(
    kb_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    folder_id: str = Query("", max_length=64),
    page_type: str = Query("", max_length=32),
    status: str = Query("active", max_length=32),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """分页列出 wiki 页（对齐 WeKnora list_wiki_pages）。"""
    _kb_visible(kb_id, caller, db)
    data = list_wiki_pages(
        db, kb_id, page=page, page_size=page_size,
        folder_id=folder_id, page_type=page_type, status=status,
    )
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/pages/lookup-by-title")
def wiki_lookup_by_title_route(
    kb_id: str,
    req: WikiLookupByTitleRequest,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """按精确标题批量查页面（对齐 WeKnora wiki_lookup_by_title）。"""
    _kb_visible(kb_id, caller, db)
    data = wiki_lookup_by_title(db, kb_id, req.titles, req.page_types)
    return {"success": True, "data": data}


@router.put("/{kb_id}/wiki/pages/{slug}/move")
def move_wiki_page_route(
    kb_id: str,
    slug: str,
    req: WikiPageMoveRequest,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """移动 wiki 页到目录（folder_id="" = 根；对齐 WeKnora move_wiki_page）。"""
    _kb_manage(kb_id, caller, db)
    try:
        data = wiki_move_page(db, kb_id, slug, req.folder_id)
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 400, detail=str(e))
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/auto-fix")
def wiki_auto_fix_route(
    kb_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """自动修复可修复的 wiki 问题（对齐 WeKnora wiki_auto_fix）。"""
    _kb_manage(kb_id, caller, db)
    data = wiki_auto_fix(db, kb_id)
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/log")
def wiki_log_write_route(
    kb_id: str,
    req: WikiLogWriteRequest,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """写一条 wiki 操作日志（对齐 WeKnora wiki_log_write，外部 agent 进度上报）。"""
    _kb_visible(kb_id, caller, db)
    try:
        data = wiki_log_write(
            db, kb_id, req.action,
            knowledge_id=req.knowledge_id or "",
            doc_title=req.doc_title or "",
            summary=req.summary or "",
            page_slugs=req.page_slugs or None,
            operator=caller.get("user_id", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": data}


@router.get("/{kb_id}/wiki/issues")
def wiki_list_issues_route(
    kb_id: str,
    slug: str = Query("", max_length=255),
    status: str = Query("", max_length=20),
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """列出 wiki 问题（对齐 WeKnora wiki_list_issues）。"""
    _kb_visible(kb_id, caller, db)
    data = wiki_list_issues(db, kb_id, slug=slug, status=status)
    return {"success": True, "data": data}


@router.put("/{kb_id}/wiki/issues/{issue_id}/status")
def wiki_update_issue_status_route(
    kb_id: str,
    issue_id: str,
    req: WikiIssueStatusUpdate,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """更新 issue 状态（pending | ignored | resolved；对齐 WeKnora）。"""
    _kb_manage(kb_id, caller, db)
    try:
        data = wiki_update_issue_status(db, kb_id, issue_id, req.status)
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 400, detail=str(e))
    return {"success": True, "data": data}


@router.post("/{kb_id}/wiki/issues/sync")
def wiki_sync_issues_route(
    kb_id: str,
    caller: dict = Depends(_require_kb_caller),
    db: Session = Depends(get_db),
) -> dict:
    """运行 lint 并把问题同步落库（对齐 WeKnora lint 落库语义）。"""
    _kb_manage(kb_id, caller, db)
    data = wiki_sync_issues_from_lint(db, kb_id)
    return {"success": True, "data": data}


__all__ = ["router"]