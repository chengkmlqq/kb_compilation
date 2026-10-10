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

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models.knowledge import (
    DocChunk,
    DocFolder,
    KbDatasource,
    KbDocument,
    WikiFeedback,
    WikiFolder,
    WikiLink,
    WikiOperationLog,
    WikiPage,
)
from api.services.retrieval import ChunkHit, hybrid_search
from api.services import storage
from api.services.wiki import slugify
from api.services.kb_config import (
    KB_TYPES,
    normalize_asr_config,
    normalize_extract_config,
    normalize_faq_config,
    normalize_indexing_strategy,
    normalize_question_generation_config,
    normalize_storage_provider_config,
    normalize_vlm_config,
    normalize_wiki_config,
)

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Knowledge base CRUD
# ---------------------------------------------------------------------------


def kb_visible_clauses(
    caller_user_id: str, caller_team_name: str, is_sys_admin: bool
) -> list:
    """SQL OR-clauses for KB visibility (personal=owner / team / system+admin)."""
    from api.services.scope import visible_clauses

    return visible_clauses(caller_user_id, caller_team_name, is_sys_admin, KbDatasource)


def list_kbs(
    db: Session,
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    team_name: str | None = None,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
    scope: str | None = None,
) -> dict:
    """Paginated KB list with doc/page counts (state='1' only).

    With caller context, only KBs the caller may see are returned
    (personal=owner, team=team members, system=admins).
    """
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    if caller_user_id or caller_team_name or is_sys_admin:
        clauses = kb_visible_clauses(caller_user_id, caller_team_name, is_sys_admin)
        if clauses:
            stmt = stmt.where(clauses[0])
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(KbDatasource.name.ilike(like))
    if team_name:
        stmt = stmt.where(KbDatasource.team_name == team_name)
    if scope:
        stmt = stmt.where(KbDatasource.scope == scope)

    total = len(db.execute(stmt).scalars().all())
    rows = (
        db.execute(
            stmt.order_by(KbDatasource.created_at.desc(), KbDatasource.id.desc())
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


def _validate_bound_model(
    db: Session,
    model_id: str | None,
    label: str,
    caller_user_id: str | None,
    caller_team_name: str | None,
    is_sys_admin: bool,
) -> None:
    """KB 绑定的模型必须对调用方可见（personal=属主 / team=同队 / system=管理员），
    否则拒绝——防止跨用户/跨团队盗用他人模型凭据。"""
    if not model_id:
        return
    from api.services.models import get_model as get_mdl
    from api.services.scope import ResourceRow, can_see

    m = get_mdl(db, model_id)
    if m is None:
        raise ValueError(f"{label} 模型不存在: {model_id}")
    if not can_see(
        ResourceRow.from_obj(m),
        caller_user_id or "",
        caller_team_name or "",
        bool(is_sys_admin),
    ):
        raise ValueError(f"{label} 模型不可见，请选择自己有权限的模型")


def create_kb(
    db: Session,
    name: str,
    label: str | None = None,
    description: str | None = None,
    indexing_strategy: dict | None = None,
    team_name: str | None = None,
    created_by: str | None = None,
    scope: str = "system",
    owner_user_id: str | None = None,
    type: str = "document",
    custom_wiki_generation: bool = False,
    embedding_model_id: str | None = None,
    summary_model_id: str | None = None,
    storage_backend_id: str | None = None,
    vector_store_id: str | None = None,
    configs: dict | None = None,
    ontology_schema_name: str | None = None,
    caller_user_id: str | None = None,
    caller_team_name: str | None = None,
    is_sys_admin: bool = False,
) -> KbDatasource:
    """Create a KB; name is required, id is generated if absent.

    scope: personal(owner_user_id) / team(team_name) / system(admin).
    indexing_strategy 归一为 WeKnora 四路开关（缺失键用 WeKnora 默认）。
    configs 为其余 JSON 配置（wiki_config/extract_config/faq_config 等）。
    """
    from api.services.kb_config import normalize_indexing_strategy

    cfg = dict(configs or {})
    if not name or not name.strip():
        raise ValueError("知识库名称不能为空")
    name = name.strip()
    if vector_store_id:
        _validate_vector_store_ref(db, vector_store_id)
    _validate_bound_model(
        db, embedding_model_id, "向量化", caller_user_id, caller_team_name, is_sys_admin
    )
    _validate_bound_model(
        db, summary_model_id, "摘要", caller_user_id, caller_team_name, is_sys_admin
    )
    kb = KbDatasource(
        id=_uuid(),
        name=name.strip(),
        label=label,
        description=description,
        type=type if type in KB_TYPES else "document",
        custom_wiki_generation=bool(custom_wiki_generation),
        embedding_model_id=embedding_model_id,
        summary_model_id=summary_model_id,
        storage_backend_id=storage_backend_id,
        vector_store_id=vector_store_id,
        scope=scope or "system",
        team_name=team_name,
        owner_user_id=owner_user_id,
        created_by=created_by,
        indexing_strategy=normalize_indexing_strategy(indexing_strategy),
        wiki_config=normalize_wiki_config(cfg.get("wiki_config")),
        extract_config=normalize_extract_config(cfg.get("extract_config")),
        faq_config=normalize_faq_config(cfg.get("faq_config")),
        question_generation_config=normalize_question_generation_config(
            cfg.get("question_generation_config")
        ),
        vlm_config=normalize_vlm_config(cfg.get("vlm_config")),
        asr_config=normalize_asr_config(cfg.get("asr_config")),
        storage_provider_config=normalize_storage_provider_config(
            cfg.get("storage_provider_config")
        ),
        image_processing_config=dict(cfg.get("image_processing_config") or {}),
        state="1",
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    if ontology_schema_name:
        from api.models.ontology import KbOntologySchema

        bind = db.get(KbOntologySchema, kb.id)
        if bind:
            bind.schema_name = ontology_schema_name
        else:
            db.add(KbOntologySchema(kb_id=kb.id, schema_name=ontology_schema_name))
        db.commit()
    return kb


def _validate_vector_store_ref(db: Session, ds_id: str) -> None:
    """Check a datasource id is a usable vector backend (ES / PostgreSQL)."""
    from api.models.framework import Datasource
    from sqlalchemy import select

    row = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
    if row is None:
        raise ValueError(f"向量库资源不存在: {ds_id}")
    ds_type = (row.ds_type or "").strip().lower()
    if ds_type not in (
        "elasticsearch", "vector_es", "es",
        "postgresql", "pg", "postgres",
    ):
        raise ValueError(
            f"资源 {row.label or row.name or row.id} 的类型不支持作为向量库: {row.ds_type}"
        )


def update_kb(
    db: Session,
    kb_id: str,
    fields: dict,
    caller_user_id: str | None = None,
    caller_team_name: str | None = None,
    is_sys_admin: bool = False,
) -> KbDatasource | None:
    kb = get_kb(db, kb_id)
    if not kb:
        return None
    _validate_bound_model(
        db,
        fields.get("embedding_model_id"),
        "向量化",
        caller_user_id,
        caller_team_name,
        is_sys_admin,
    )
    _validate_bound_model(
        db,
        fields.get("summary_model_id"),
        "摘要",
        caller_user_id,
        caller_team_name,
        is_sys_admin,
    )
    if "name" in fields:
        kb.name = str(fields["name"]).strip() or kb.name
    if "label" in fields:
        kb.label = str(fields.get("label") or "")
    if "description" in fields:
        kb.description = str(fields.get("description") or "")
    if "type" in fields:
        t = str(fields.get("type") or "document")
        kb.type = t if t in KB_TYPES else kb.type
    if "custom_wiki_generation" in fields:
        kb.custom_wiki_generation = bool(fields["custom_wiki_generation"])
    for f in ("embedding_model_id", "summary_model_id", "storage_backend_id"):
        if f in fields:
            setattr(kb, f, str(fields.get(f) or "") or None)
    if "vector_store_id" in fields:
        new_store = str(fields.get("vector_store_id") or "") or None
        if new_store != kb.vector_store_id:
            # 向量库来源在创建时确定；非空库禁止切换（避免存量向量悬空）
            has_docs = (
                db.execute(
                    select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
                ).scalar()
                or 0
            )
            if has_docs > 0:
                raise ValueError("非空知识库不允许切换向量库，请在创建时确定向量库来源")
            if new_store:
                _validate_vector_store_ref(db, new_store)
            kb.vector_store_id = new_store
    if "indexing_strategy" in fields and isinstance(fields["indexing_strategy"], dict):
        kb.indexing_strategy = normalize_indexing_strategy(fields["indexing_strategy"])
    raw_cfg = fields.get("configs")
    cfg: dict = raw_cfg if isinstance(raw_cfg, dict) else {}
    normalizers = {
        "wiki_config": normalize_wiki_config,
        "extract_config": normalize_extract_config,
        "faq_config": normalize_faq_config,
        "question_generation_config": normalize_question_generation_config,
        "vlm_config": normalize_vlm_config,
        "asr_config": normalize_asr_config,
        "storage_provider_config": normalize_storage_provider_config,
    }
    for key, fn in normalizers.items():
        if key in cfg:
            setattr(kb, key, fn(cfg.get(key)))
        elif key in fields:  # 顶层平铺也接受
            setattr(kb, key, fn(fields.get(key)))
    if "image_processing_config" in cfg:
        kb.image_processing_config = dict(cfg["image_processing_config"] or {})
    elif "image_processing_config" in fields:
        kb.image_processing_config = dict(fields["image_processing_config"] or {})
    db.commit()
    db.refresh(kb)
    return kb


def delete_kb(db: Session, kb_id: str) -> dict:
    """级联删除知识库（对齐 WeKnora：KB 记录软删 + 内容一并清理，不再设守卫）。

    WeKnora 的 DeleteKnowledgeBase 不校验文档数：先软删 KB 记录，再清理
    共享/数据源，最后异步删 embeddings/chunks/files/图谱。本实现同步清理
    （本库数据量小），语义一致：

      1. KB 软删（state=0）
      2. wiki 链接 → 页面 → 文件夹（先删依赖，避免孤儿链接）
      3. 文档 chunks → 文档记录 → 该库 embedding 记录
      4. wiki 操作日志 / 反馈
      5. Neo4j 图谱节点（best-effort：失败不阻塞删除）

    返回 data 含删除计数，供前端确认框展示。
    """
    kb = get_kb(db, kb_id)
    if not kb:
        return {"success": False, "message": f"知识库不存在: {kb_id}"}

    # 快照计数（前端确认框「将一并删除 N 文档 / M Wiki 页」用）
    doc_count = (
        db.execute(
            select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
        ).scalar()
        or 0
    )
    wiki_count = (
        db.execute(
            select(func.count()).select_from(WikiPage).where(WikiPage.kb_id == kb_id)
        ).scalar()
        or 0
    )

    # 1) KB 软删
    kb.state = "0"

    # 1.5) 级联取消该库未完成的转换/wiki 任务（2026-10-05 实测: 删库后
    #      关联任务行残留 PENDING, celery 仍会消费 -> agent 为已删库建页）
    _cancel_kb_tasks(db, kb_id)

    # 2) wiki 层：链接 → 页面 → 文件夹
    db.execute(delete(WikiLink).where(WikiLink.kb_id == kb_id))
    db.execute(delete(WikiPage).where(WikiPage.kb_id == kb_id))
    db.execute(delete(WikiFolder).where(WikiFolder.kb_id == kb_id))
    # 3) 文档层：chunks → 文档（先收集 storage_path，删除后清理对象存储）
    doc_paths = [
        str(p)
        for p in db.execute(
            select(KbDocument.storage_path).where(
                KbDocument.kb_id == kb_id, KbDocument.storage_path.isnot(None)
            )
        ).scalars()
        if p
    ]
    db.execute(delete(DocChunk).where(DocChunk.kb_id == kb_id))
    db.execute(delete(KbDocument).where(KbDocument.kb_id == kb_id))
    # 4) wiki 日志 / 反馈
    db.execute(delete(WikiOperationLog).where(WikiOperationLog.kb_id == kb_id))
    db.execute(delete(WikiFeedback).where(WikiFeedback.kb_id == kb_id))
    db.commit()

    # 4.2) 文档对象存储清理（minio:// 路径；本地路径文件一并删；best-effort）
    for sp in doc_paths:
        try:
            storage.delete(sp)
        except Exception as exc:  # noqa: BLE001 - 对象清理失败不阻塞
            logging.getLogger(__name__).warning("文档对象清理失败(%s): %s", sp, exc)

    # 4.5) 向量索引（kb_embedding 属知识引擎侧表，走 VectorStore；best-effort）
    try:
        from api.services.vector_store import get_vector_store

        get_vector_store(kb.vector_store_id).delete_by_kb(kb_id)
    except Exception as exc:  # pragma: no cover - 向量清理为尽力而为
        logging.getLogger(__name__).warning("向量索引清理失败(kb=%s): %s", kb_id, exc)

    # 5) Neo4j 图谱：best-effort，失败仅记录不阻塞（对齐 WeKnora 异步清理语义）
    graph_nodes = 0
    try:
        settings = get_settings()

        if settings.neo4j_enabled:
            from api.services.graph import Neo4jGraphStore

            graph_nodes = Neo4jGraphStore(
                settings.NEO4J_URI,
                settings.NEO4J_USERNAME,
                settings.NEO4J_PASSWORD,
                settings.NEO4J_DATABASE or None,
            ).delete_kb_graph(kb_id)
    except Exception as exc:  # pragma: no cover - 图谱清理为尽力而为
        logging.getLogger(__name__).warning("Neo4j 图谱清理失败(kb=%s): %s", kb_id, exc)

    return {
        "success": True,
        "data": {
            "id": kb_id,
            "doc_count": doc_count,
            "wiki_count": wiki_count,
            "graph_nodes": graph_nodes,
        },
    }


def _kb_dict(kb: KbDatasource, doc_count: int = 0, page_count: int = 0) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "label": kb.label,
        "description": kb.description,
        "scope": kb.scope or "system",
        "team_name": kb.team_name or "",
        "owner_user_id": kb.owner_user_id or "",
        "indexing_strategy": kb.indexing_strategy or {},
        "state": kb.state,
        "doc_count": doc_count,
        "page_count": page_count,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        # ── WeKnora 对齐配置（详情页配置弹窗读取）──
        "type": kb.type or "document",
        "custom_wiki_generation": bool(getattr(kb, "custom_wiki_generation", False)),
        "embedding_model_id": getattr(kb, "embedding_model_id", None) or "",
        "summary_model_id": getattr(kb, "summary_model_id", None) or "",
        "wiki_config": getattr(kb, "wiki_config", None) or {},
        "extract_config": getattr(kb, "extract_config", None) or {},
        "faq_config": getattr(kb, "faq_config", None) or {},
        "question_generation_config": getattr(kb, "question_generation_config", None) or {},
        "vlm_config": getattr(kb, "vlm_config", None) or {},
        "asr_config": getattr(kb, "asr_config", None) or {},
        "storage_provider_config": getattr(kb, "storage_provider_config", None) or {},
        "storage_backend_id": getattr(kb, "storage_backend_id", None) or "",
        "vector_store_id": getattr(kb, "vector_store_id", None) or "",
    }


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def list_documents(
    db: Session,
    kb_id: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    file_type: str = "",
    parse_status: str = "",
    folder_id: str = "",
) -> dict:
    stmt = select(KbDocument).where(KbDocument.kb_id == kb_id)
    if keyword:
        stmt = stmt.where(KbDocument.file_name.like(f"%{keyword}%"))
    if file_type:
        stmt = stmt.where(KbDocument.file_ext == file_type.lstrip(".").lower())
    if parse_status:
        stmt = stmt.where(KbDocument.parse_state == parse_status.upper())
    # 递归子树浏览：folder 及其全部子孙目录的文档都算
    if folder_id:
        subtree = _doc_subtree_ids(db, kb_id, folder_id)
        stmt = stmt.where(KbDocument.folder_id.in_(subtree))
    total = len(db.execute(stmt).scalars().all())
    rows = (
        db.execute(
            stmt.order_by(KbDocument.created_at.desc(), KbDocument.id.desc())
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
            "summary": d.summary,
            "summary_status": d.summary_status,
            "summary_error": d.summary_error,
            "folder_id": d.folder_id or "",
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
    folder_id: str = "",
    process_config: str | None = None,

) -> KbDocument:
    doc = KbDocument(
        id=_uuid(),
        kb_id=kb_id,
        file_name=file_name,
        file_ext=(file_ext or "").lstrip(".").lower() or None,
        file_size=file_size,
        storage_path=storage_path,
        sys_file_id=sys_file_id,
        process_config=process_config,
        parse_state="PENDING",
        chunk_count=0,
        created_by=created_by,
        folder_id=folder_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


SUMMARY_SYSTEM_PROMPT = (
    "你是专业的文档摘要助手。请阅读用户提供的文档内容，生成一段简洁、准确的中文摘要："
    "先一句话概括文档主题，再用分点列出核心要点与关键结论。"
    "控制在 150~250 字，只输出摘要正文，不要输出多余解释。"
)
# 送入摘要 LLM 的文档正文字符上限（约 3000+ tokens）
SUMMARY_CONTENT_LIMIT = 12000


def generate_document_summary(db: Session, kb_id: str, document_id: str) -> dict:
    """用知识库配置的大语言模型（summary_model_id）为文档生成 AI 摘要（对齐 WeKnora）。

    正文取自 doc_chunk 已解析文本（按 seq 拼接，截断到字符上限）；
    结果落 kb_document.summary / summary_status / summary_error。
    """
    from api.services.chat import ChatClient, ChatConfig, ChatMessage
    from api.services.models import decrypt_secret, get_model as get_llm_model

    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        raise ValueError(f"文档不存在: {document_id}")

    kb = get_kb(db, kb_id)
    if not kb:
        raise ValueError(f"知识库不存在: {kb_id}")

    model_id = getattr(kb, "summary_model_id", None) or ""
    if not model_id:
        raise ValueError("知识库未配置大语言模型（LLM），请在知识库配置中选择摘要模型")

    model = get_llm_model(db, model_id)
    if not model:
        raise ValueError("摘要模型不存在或已停用，请在知识库配置中重新选择")

    base_url = (model.base_url or "").rstrip("/")
    if not base_url:
        raise ValueError("摘要模型缺少服务地址（base_url），请检查模型配置")

    # 拼接文档正文（解析后的文本分块）
    chunk_texts = db.execute(
        select(DocChunk.content)
        .where(DocChunk.document_id == document_id)
        .order_by(DocChunk.seq)
    ).scalars().all()
    body = "\n".join(c for c in chunk_texts if c)
    if not body.strip():
        raise ValueError("文档尚未解析出可用文本，无法生成摘要（请先重新解析）")

    cfg = ChatConfig(
        base_url=base_url,
        api_key=decrypt_secret(model.api_key),
        model=model.name,
        timeout=120.0,
    )
    messages = [
        ChatMessage(role="system", content=SUMMARY_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=f"文档《{doc.file_name}》内容如下：\n\n{body[:SUMMARY_CONTENT_LIMIT]}",
        ),
    ]
    try:
        summary = ChatClient(cfg).chat(messages, temperature=0.3, max_tokens=1024)
    except Exception as exc:  # noqa: BLE001 — 模型超时/网络错误统一转失败态
        logger.exception("doc summary generation failed doc=%s", document_id)
        doc.summary_status = "FAILED"
        doc.summary_error = str(exc)[:500]
        db.commit()
        raise ValueError(f"摘要生成失败: {exc}") from exc

    doc.summary = (summary or "").strip()
    doc.summary_status = "READY"
    doc.summary_error = None
    db.commit()
    return {"document_id": document_id, "summary": doc.summary, "summary_status": "READY"}


def delete_document(db: Session, kb_id: str, document_id: str) -> dict:
    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        return {"success": False, "message": f"文档不存在: {document_id}"}
    # 2026-10-05 实测: 删文档后其 wiki 构建任务仍执行（假成功/白跑 LLM）——
    # 删除时先级联取消关联的未完成 wiki 任务（PENDING/RUNNING → STOPPED + revoke）。
    _cancel_related_wiki_tasks(db, document_id)

    # --- 级联清理（2026-10-08 修复：此前只删 chunks+文档行，wiki 页/目录/链接/
    #     向量/图谱全部残留成孤儿。对齐 delete_kb 语义逐层清理，先删依赖）---
    #
    # 1) wiki 层：该文档专属页（source_refs 仅含本文档）整页删除；共享页
    #    （跨文档合并实体页）只摘除本 kid 引用不删页——防误伤其他文档。
    #    页面删除后，指向这些页的链接一并清；不再被任何页引用的目录删除。
    pages = db.execute(
        select(WikiPage).where(
            WikiPage.kb_id == kb_id, WikiPage.source_refs.isnot(None)
        )
    ).scalars().all()
    doc_prefix = f"{document_id}|"
    page_ids_to_delete: list[str] = []
    kept_folder_ids: set[str] = set()
    for p in pages:
        refs = p.source_refs if isinstance(p.source_refs, list) else []
        hits = [r for r in refs if isinstance(r, str) and r.startswith(doc_prefix)]
        if not hits:
            if p.folder_id:
                kept_folder_ids.add(str(p.folder_id))
            continue
        if len(refs) <= len(hits):
            # 专属页：本页所有 source_ref 都属于该文档 → 整页删除
            page_ids_to_delete.append(p.id)
        else:
            # 共享页：摘除本 kid 引用，保留其他文档引用
            p.source_refs = [r for r in refs if not r.startswith(doc_prefix)]
            if p.folder_id:
                kept_folder_ids.add(str(p.folder_id))
    if page_ids_to_delete:
        db.execute(delete(WikiLink).where(WikiLink.from_page_id.in_(page_ids_to_delete)))
        db.execute(delete(WikiLink).where(WikiLink.to_page_id.in_(page_ids_to_delete)))
        db.execute(
            delete(WikiPage).where(WikiPage.id.in_(page_ids_to_delete))
        )
    # 该文档目录子树清理：从被删页所在 folder 向上收敛（该文档页专属目录），
    # 不删根目录（parent_id 为空，全库共用）也不误伤其他文档目录。
    # 收集被删页/共享页的 folder，向上找其根（parent_id 为空即停），只删
    # 该子树下不再被任何保留页引用的目录。
    if page_ids_to_delete:
        affected_folder_ids = {
            str(p.folder_id)
            for p in pages
            if p.id in page_ids_to_delete and p.folder_id
        }
    else:
        affected_folder_ids = set()
    # 子树内所有目录
    all_folder_ids = {
        str(f.id)
        for f in db.execute(
            select(WikiFolder).where(WikiFolder.kb_id == kb_id)
        ).scalars()
        if f.id
    }
    # 保留页引用的目录（含其他文档页 → 绝不能删）
    folder_ids_to_delete = [
        fid for fid in affected_folder_ids
        if fid not in kept_folder_ids and fid in all_folder_ids
    ]
    if folder_ids_to_delete:
        db.execute(
            delete(WikiFolder).where(WikiFolder.id.in_(folder_ids_to_delete))
        )
    # 该文档操作日志 / 反馈（对齐 delete_kb 第 4 步；按 source 关联不可行则全量保日志）
    #   wiki_operation_log / wiki_feedback 无 document_id 列，按 kb 保留——不删。

    # 2) 文档层：chunks → 文档行
    # 注意 select(DocChunk.id) 单列查询 .scalars() 直接产出字符串值，
    # 不能对每个值再取 .id（曾导致删任何带 chunks 的文档 500）
    chunk_ids = list(
        db.execute(
            select(DocChunk.id).where(DocChunk.document_id == document_id)
        ).scalars()
    )
    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    db.delete(doc)
    db.commit()

    # 3) 向量索引（该文档 chunks 的 embedding，best-effort 不阻塞）
    if chunk_ids:
        try:
            from api.services.vector_store import get_vector_store

            # vector_store_id 是 KB 级列（KbDatasource），文档行没有——
            # 对齐 ingest.py 的取法，否则 doc.vector_store_id 运行时
            # AttributeError 被吞 → 向量清理静默失效
            kb_row = db.execute(
                select(KbDatasource).where(KbDatasource.id == kb_id)
            ).scalars().first()
            get_vector_store(
                kb_row.vector_store_id if kb_row else None
            ).delete_by_chunks(chunk_ids)
        except Exception as exc:  # pragma: no cover - 向量清理尽力而为
            logging.getLogger(__name__).warning(
                "向量清理失败(doc=%s): %s", document_id, exc
            )

    # 4) 本地/对象存储文件清理（_remove_local_file 内部已 try/except，幂等）
    _remove_local_file(doc.storage_path)

    # 5) Neo4j 图谱（kg = 该 kid 子图，best-effort 不阻塞）
    try:
        settings = get_settings()
        if settings.neo4j_enabled:
            from api.services.graph import Neo4jGraphStore

            Neo4jGraphStore(
                settings.NEO4J_URI,
                settings.NEO4J_USERNAME,
                settings.NEO4J_PASSWORD,
                settings.NEO4J_DATABASE or None,
            ).delete_doc_graph(document_id)
    except Exception as exc:  # pragma: no cover - 图谱清理尽力而为
        logging.getLogger(__name__).warning(
            "Neo4j 图谱清理失败(doc=%s): %s", document_id, exc
        )

    return {"success": True, "data": {"id": document_id}}


def _cancel_related_wiki_tasks(db: Session, document_id: str) -> None:
    """删除文档时级联取消其关联的未完成 wiki 构建任务（避免删后假成功/孤儿页）。"""
    from api.models.framework import Job

    jobs = db.execute(
        select(Job).where(
            Job.id.like("WIKI_%"),
            Job.task_params.like(f"%{document_id}%"),
            Job.state.in_(["PENDING", "RUNNING"]),
        )
    ).scalars().all()
    if not jobs:
        return
    try:
        from worker.celery_app import celery_app
    except Exception:  # noqa: BLE001 — 环境无 celery 时仅置状态
        celery_app = None
    for j in jobs:
        j.state = "STOPPED"
        if celery_app is not None:
            try:
                celery_app.control.revoke(j.id, terminate=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("revoke wiki task %s failed: %s", j.id, exc)
    db.commit()


def _cancel_kb_tasks(db: Session, kb_id: str) -> None:
    """删除知识库时级联取消其未完成的转换/wiki 任务。

    2026-10-05 实测：删库后关联任务行残留 PENDING，celery 队列仍持有该
    任务 -> worker 照常消费 -> agent 为已删库建页（孤儿页）。
    仅置 STOPPED + revoke（保留任务行供审计）；限定 DOC_/WIKI_ 前缀防误伤。
    """
    from api.models.framework import Job

    jobs = db.execute(
        select(Job).where(
            Job.id.like("DOC_%"),
            Job.task_params.like(f"%{kb_id}%"),
            Job.state.in_(["PENDING", "RUNNING"]),
        )
    ).scalars().all()
    jobs += db.execute(
        select(Job).where(
            Job.id.like("WIKI_%"),
            Job.task_params.like(f"%{kb_id}%"),
            Job.state.in_(["PENDING", "RUNNING"]),
        )
    ).scalars().all()
    if not jobs:
        return
    try:
        from worker.celery_app import celery_app
    except Exception:  # noqa: BLE001 — 环境无 celery 时仅置状态
        celery_app = None
    for j in jobs:
        j.state = "STOPPED"
        if celery_app is not None:
            try:
                celery_app.control.revoke(j.id, terminate=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("revoke kb task %s failed: %s", j.id, exc)
    db.commit()


def _remove_local_file(storage_path: str | None) -> None:
    """删除物理文件（本地磁盘或 MinIO 对象，幂等）。"""
    if not storage_path:
        return
    try:
        from api.services.storage import delete as storage_delete

        storage_delete(storage_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to remove stored file %s: %s", storage_path, exc)


# ---------------------------------------------------------------------------
# Doc folders (文档多级目录, doc_folder + kb_document.folder_id)
# 决策（2026-10-10）：单归属 / 递归子树浏览 / 非空禁删 / 仅浏览不参与检索
# ---------------------------------------------------------------------------


def doc_folders(db: Session, kb_id: str) -> list[dict]:
    """全量文档目录元数据（轻量，管理面板/树构建用）。"""
    folders = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id).order_by(DocFolder.created_at)
    ).scalars().all()
    counts = _doc_child_counts(db, kb_id)
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id or "",
            "child_count": counts.get(f.id, 0),
        }
        for f in folders
    ]


def _doc_child_counts(db: Session, kb_id: str) -> dict[str, int]:
    """每个 folder 的直接子项数（子目录 + 子文档），懒加载树判断展开箭头用。"""
    sub_folders = db.execute(
        select(func.coalesce(DocFolder.parent_id, ""), func.count())
        .where(DocFolder.kb_id == kb_id)
        .group_by(func.coalesce(DocFolder.parent_id, ""))
    ).all()
    sub_docs = db.execute(
        select(func.coalesce(KbDocument.folder_id, ""), func.count())
        .where(KbDocument.kb_id == kb_id)
        .group_by(func.coalesce(KbDocument.folder_id, ""))
    ).all()
    counts: dict[str, int] = {}
    for fid, n in list(sub_folders) + list(sub_docs):
        counts[fid or ""] = counts.get(fid or "", 0) + int(n)
    return counts


def doc_branch(
    db: Session, kb_id: str, folder_id: str = "", page: int = 1, page_size: int = 50
) -> dict:
    """懒加载分支（对齐 wiki_branch）：folder_id='' 返回根级直接子目录 + 直接子文档。

    每目录带 child_count（子目录+子文档数，前端据此显示展开箭头）。
    """
    fid = folder_id or ""
    folders = db.execute(
        select(DocFolder)
        .where(DocFolder.kb_id == kb_id, func.coalesce(DocFolder.parent_id, "") == fid)
        .order_by(DocFolder.created_at)
    ).scalars().all()
    docs_q = select(KbDocument).where(
        KbDocument.kb_id == kb_id,
        func.coalesce(KbDocument.folder_id, "") == fid,
    )
    total_docs = len(db.execute(docs_q).scalars().all())
    rows = db.execute(
        docs_q.order_by(KbDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    counts = _doc_child_counts(db, kb_id)
    return {
        "kb_id": kb_id,
        "folder_id": fid,
        "folders": [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id or "",
                "child_count": counts.get(f.id, 0),
            }
            for f in folders
        ],
        "documents": [
            {
                "id": d.id,
                "file_name": d.file_name,
                "file_ext": d.file_ext,
                "file_size": d.file_size,
                "parse_state": d.parse_state,
                "folder_id": d.folder_id or "",
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ],
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total_docs,
        "total_docs": total_docs,
        "total_folders": len(folders),
    }


def _doc_subtree_ids(db: Session, kb_id: str, folder_id: str) -> list[str]:
    """folder 及全部子孙目录的 id 集合（递归）。用于递归子树浏览。"""
    all_folders = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id)
    ).scalars().all()
    parent_of: dict[str, str] = {f.id: f.parent_id or "" for f in all_folders}
    ids = [folder_id]
    for f in all_folders:
        if f.id == folder_id:
            continue
        cur = f.parent_id or ""
        while cur:
            if cur == folder_id:
                ids.append(f.id)
                break
            cur = parent_of.get(cur, "")
    return ids


def doc_create_folder(
    db: Session, kb_id: str, data: dict, user_id: str | None = None
) -> dict:
    """创建文档目录。parent_id 缺省为根。"""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    parent_id = str(data.get("parent_id") or "")
    if parent_id:
        parent = db.execute(
            select(DocFolder).where(
                DocFolder.kb_id == kb_id, DocFolder.id == parent_id
            )
        ).scalars().first()
        if not parent:
            raise ValueError(f"父目录不存在: {parent_id}")
    # 同级同名唯一
    dup = db.execute(
        select(DocFolder).where(
            DocFolder.kb_id == kb_id,
            func.coalesce(DocFolder.parent_id, "") == parent_id,
            DocFolder.name == name,
        )
    ).scalars().first()
    if dup:
        raise ValueError(f"同级下已存在同名目录: {name}")
    folder = DocFolder(
        id=_uuid(),
        kb_id=kb_id,
        name=name,
        parent_id=parent_id,
        created_by=user_id,
    )
    db.add(folder)
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id or ""}


def doc_update_folder(db: Session, kb_id: str, folder_id: str, data: dict) -> dict:
    """重命名 / 移动文档目录。"""
    folder = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id, DocFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    if "name" in data:
        n = str(data["name"]).strip()
        if not n:
            raise ValueError("name 不能为空")
        folder.name = n
    if "parent_id" in data:
        new_parent = str(data["parent_id"] or "")
        if new_parent == folder.id:
            raise ValueError("不能将目录移动到自身")
        # 防环：新父目录不能是自己的子孙
        subtree = _doc_subtree_ids(db, kb_id, folder.id)
        if new_parent in subtree:
            raise ValueError("不能将目录移动到自己的子目录下")
        folder.parent_id = new_parent
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id or ""}


def doc_delete_folder(db: Session, kb_id: str, folder_id: str) -> dict:
    """删除文档目录：仅当目录下无子目录、无文档时允许（非空禁删）。"""
    folder = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id, DocFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    child_folders = db.execute(
        select(DocFolder).where(
            DocFolder.kb_id == kb_id, DocFolder.parent_id == folder_id
        )
    ).scalars().all()
    if child_folders:
        raise ValueError(f"目录下仍有 {len(child_folders)} 个子目录，请先移走")
    child_docs = db.execute(
        select(KbDocument).where(
            KbDocument.kb_id == kb_id, KbDocument.folder_id == folder_id
        )
    ).scalars().all()
    if child_docs:
        raise ValueError(f"目录下仍有 {len(child_docs)} 个文档，请先移走")
    db.delete(folder)
    db.commit()
    return {"deleted": True, "folder_id": folder_id}


def doc_move_documents(
    db: Session, kb_id: str, document_ids: list[str], folder_id: str
) -> dict:
    """批量移动文档到目录（folder_id="" = 移出所有目录到根层级）。

    单归属语义：folder_id 整体替换文档原目录。所有 id 必须属于本 KB。
    """
    if not document_ids:
        return {"moved": 0}
    if folder_id:
        folder = db.execute(
            select(DocFolder).where(
                DocFolder.kb_id == kb_id, DocFolder.id == folder_id
            )
        ).scalars().first()
        if not folder:
            raise ValueError(f"目录不存在: {folder_id}")
    docs = db.execute(
        select(KbDocument).where(
            KbDocument.kb_id == kb_id, KbDocument.id.in_(document_ids)
        )
    ).scalars().all()
    if len(docs) != len(set(document_ids)):
        raise ValueError("部分文档不存在或不属于当前知识库")
    for d in docs:
        d.folder_id = folder_id
    db.commit()
    return {"moved": len(docs)}


# ---------------------------------------------------------------------------
# Wiki browsing
# ---------------------------------------------------------------------------


def wiki_tree(db: Session, kb_id: str) -> dict:
    """Folders + pages for a KB (folder_id '' = root). 全量（保留给管理面板等主动场景）。"""
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


def _wiki_child_counts(db: Session, kb_id: str) -> dict[str, int]:
    """每个 folder 的直接子项数（子目录 + 子页面），用于懒加载树判断展开箭头。"""
    sub_folders = db.execute(
        select(func.coalesce(WikiFolder.parent_id, ""), func.count())
        .where(WikiFolder.kb_id == kb_id)
        .group_by(func.coalesce(WikiFolder.parent_id, ""))
    ).all()
    sub_pages = db.execute(
        select(func.coalesce(WikiPage.folder_id, ""), func.count())
        .where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        .group_by(func.coalesce(WikiPage.folder_id, ""))
    ).all()
    counts: dict[str, int] = {}
    for fid, n in list(sub_folders) + list(sub_pages):
        counts[fid or ""] = counts.get(fid or "", 0) + int(n)
    return counts


def wiki_branch(
    db: Session, kb_id: str, folder_id: str = "", page: int = 1, page_size: int = 50
) -> dict:
    """懒加载分支（对齐 WeKnora 侧栏按需展开）。

    folder_id='' → 根级。只返回该 folder 的直接子目录 + 直接子页面，
    每个目录带 child_count（子目录+子页面数，前端据此显示展开箭头）。
    页面分页，避免大知识库一次全量传输。
    """
    fid = folder_id or ""
    folders = db.execute(
        select(WikiFolder)
        .where(WikiFolder.kb_id == kb_id, func.coalesce(WikiFolder.parent_id, "") == fid)
        .order_by(WikiFolder.created_at)
    ).scalars().all()
    pages_q = select(WikiPage).where(
        WikiPage.kb_id == kb_id,
        WikiPage.status == "active",
        func.coalesce(WikiPage.folder_id, "") == fid,
    )
    total_pages = len(db.execute(pages_q).scalars().all())
    rows = db.execute(
        pages_q.order_by(WikiPage.created_at).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    counts = _wiki_child_counts(db, kb_id)
    return {
        "kb_id": kb_id,
        "folder_id": fid,
        "folders": [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id or "",
                "child_count": counts.get(f.id, 0),
                "page_count": 0,
            }
            for f in folders
        ],
        "pages": [
            {
                "id": p.id,
                "slug": p.slug,
                "title": p.title,
                "page_type": p.page_type,
                "folder_id": p.folder_id or "",
                "summary": p.summary,
            }
            for p in rows
        ],
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total_pages,
        "total_pages": total_pages,
        "total_folders": len(folders),
    }


def wiki_folders(db: Session, kb_id: str) -> list[dict]:
    """全量目录元数据（轻量，仅用于深链定位父链 / 管理面板目录下拉）。"""
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.created_at)
    ).scalars().all()
    counts = _wiki_child_counts(db, kb_id)
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id or "",
            "child_count": counts.get(f.id, 0),
        }
        for f in folders
    ]


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

    # 出链（本页 -> 其他页）
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

    # 反向链接（其他页 -> 本页）
    in_links_rows = db.execute(
        select(WikiLink).where(
            WikiLink.kb_id == kb_id, WikiLink.to_page_id == page.id
        )
    ).scalars().all()
    in_links = []
    if in_links_rows:
        in_ids = [l.from_page_id for l in in_links_rows]
        in_pages = db.execute(
            select(WikiPage).where(WikiPage.id.in_(in_ids), WikiPage.status == "active")
        ).scalars().all()
        in_links = [
            {"slug": ip.slug, "title": ip.title, "page_type": ip.page_type} for ip in in_pages
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
        "in_links": in_links,
        "created_at": page.created_at.isoformat() if page.created_at else None,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Wiki 知识图谱（数据源：wiki_page + wiki_link，不依赖 Neo4j）
# ---------------------------------------------------------------------------

PAGE_TYPE_LABELS = {
    "entity": "实体",
    "concept": "概念",
    "summary": "摘要",
    "synthesis": "综合",
    "comparison": "对比",
    "business_ontology": "业务本体",
    "rule_ontology": "规则本体",
    "original_sentence": "原句",
    "frequent_keyword": "高频关键词",
    "topic_cluster": "主题簇",
    "knowledge_graph_summary": "图谱摘要",
    "cross_document_insight": "跨文档洞察",
}

GRAPH_PAGE_TYPES = list(PAGE_TYPE_LABELS.keys())


def wiki_graph(
    db: Session,
    kb_id: str,
    mode: str = "overview",
    center: str = "",
    depth: int = 1,
    limit: int = 200,
    types: list[str] | None = None,
) -> dict:
    """图谱数据：nodes = wiki pages, edges = wiki_links。

    mode=overview 返回全库图（受 limit 截断）；mode=ego 以 center slug 为中心
    返回 depth 跳邻域。types 过滤 page_type。
    """
    allowed_types = [t for t in (types or []) if t in GRAPH_PAGE_TYPES]
    type_filter = set(allowed_types) if allowed_types else set(GRAPH_PAGE_TYPES)

    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    by_id = {p.id: p for p in pages}
    page_ids = {p.id for p in pages}

    # 统计每页链接数（出+入）
    link_counts: dict[str, int] = {}
    slug_by_id = {p.id: p.slug for p in pages}
    link_rows = db.execute(
        select(WikiLink.from_page_id, WikiLink.to_page_id).where(WikiLink.kb_id == kb_id)
    ).all()
    edges: list[dict] = []
    for frm, to in link_rows:
        if frm in page_ids and to in page_ids:
            link_counts[frm] = link_counts.get(frm, 0) + 1
            link_counts[to] = link_counts.get(to, 0) + 1
            edges.append({"source": slug_by_id[frm], "target": slug_by_id[to]})

    # 选择节点集
    if mode == "ego" and center:
        center_page = db.execute(
            select(WikiPage).where(
                WikiPage.kb_id == kb_id, WikiPage.slug == center, WikiPage.status == "active"
            )
        ).scalars().first()
        if not center_page:
            return {"nodes": [], "edges": [], "meta": {"mode": "ego", "total": 0, "returned": 0, "truncated": False}}
        selected: set[str] = {center_page.id}
        frontier: set[str] = {center_page.id}
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for frm, to in link_rows:
                if frm in frontier and to in page_ids and to not in selected:
                    next_frontier.add(to)
                if to in frontier and frm in page_ids and frm not in selected:
                    next_frontier.add(frm)
            selected |= next_frontier
            frontier = next_frontier
            if not frontier:
                break
    else:
        # overview：按链接数取 top-N（默认全库，limit 截断）
        ranked = sorted(
            [p for p in pages if p.page_type in type_filter],
            key=lambda p: link_counts.get(p.id, 0),
            reverse=True,
        )
        selected = {p.id for p in ranked[:limit]}

    nodes = []
    for p in pages:
        if p.id not in selected or p.page_type not in type_filter:
            continue
        nodes.append(
            {
                "slug": p.slug,
                "title": p.title,
                "page_type": p.page_type,
                "link_count": link_counts.get(p.id, 0),
                "summary": p.summary,
            }
        )

    node_slugs = {p.slug for p in pages if p.id in selected}
    edges = [e for e in edges if e["source"] in node_slugs and e["target"] in node_slugs]

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "mode": mode,
            "total": len(pages),
            "returned": len(nodes),
            "truncated": len(pages) > limit and mode != "ego",
        },
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


def wiki_search(db: Session, kb_id: str, query: str, limit: int = 20) -> dict:
    """Wiki 页面内搜索：按标题或内容 ilike 匹配（分页式、应用侧小结果集）。

    返回：{query, total, items:[{slug,title,page_type,summary,content_head}]}
    """
    q = (query or "").strip()
    if not q:
        return {"query": q, "total": 0, "items": []}
    like = f"%{q}%"
    stmt = (
        select(WikiPage)
        .where(
            WikiPage.kb_id == kb_id,
            WikiPage.status == "active",
            or_(WikiPage.title.like(like), WikiPage.content.like(like)),
        )
        .limit(limit)
    )
    pages = db.execute(stmt).scalars().all()
    items = [
        {
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "summary": p.summary,
            "content_head": (p.content or "")[:200],
        }
        for p in pages
    ]
    return {"query": q, "total": len(items), "items": items}


def wiki_log_action(
    db: Session,
    kb_id: str,
    action: str,
    slug: str = "",
    title: str = "",
    detail: str = "",
    operator: str = "",
) -> None:
    """记录一条 wiki 操作日志（页面/目录 CRUD、链接重建等）。"""
    db.add(
        WikiOperationLog(
            id=uuid.uuid4().hex,
            kb_id=kb_id,
            action=action,
            slug=slug,
            title=title,
            detail=detail,
            operator=operator,
        )
    )


def wiki_list_logs(db: Session, kb_id: str, limit: int = 100) -> dict:
    """Wiki 操作日志（倒序，最近 limit 条）。"""
    rows = (
        db.execute(
            select(WikiOperationLog)
            .where(WikiOperationLog.kb_id == kb_id)
            .order_by(WikiOperationLog.created_at.desc(), WikiOperationLog.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {
        "kb_id": kb_id,
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "slug": r.slug,
                "title": r.title,
                "detail": r.detail,
                "operator": r.operator,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def wiki_submit_feedback(
    db: Session, kb_id: str, slug: str, user_id: str, feedback_type: str, content: str
) -> dict:
    """提交页面反馈（helpful=有帮助 / issue=问题上报）。"""
    fb = WikiFeedback(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        slug=slug,
        user_id=user_id,
        feedback_type=feedback_type,
        content=content,
        status="open",
    )
    db.add(fb)
    db.commit()
    return {"id": fb.id, "feedback_type": fb.feedback_type, "status": fb.status}


def wiki_list_feedback(db: Session, kb_id: str, slug: str = "", limit: int = 100) -> dict:
    """页面反馈列表（可选按 slug 过滤，倒序）。"""
    stmt = select(WikiFeedback).where(WikiFeedback.kb_id == kb_id)
    if slug:
        stmt = stmt.where(WikiFeedback.slug == slug)
    rows = (
        db.execute(stmt.order_by(WikiFeedback.created_at.desc(), WikiFeedback.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return {
        "kb_id": kb_id,
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "slug": r.slug,
                "user_id": r.user_id,
                "feedback_type": r.feedback_type,
                "content": r.content,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def wiki_update_feedback_status(
    db: Session, kb_id: str, feedback_id: str, status: str
) -> dict:
    """更新反馈状态（open/resolved/ignored）。"""
    fb = db.execute(
        select(WikiFeedback).where(
            WikiFeedback.kb_id == kb_id, WikiFeedback.id == feedback_id
        )
    ).scalars().first()
    if not fb:
        raise ValueError(f"反馈不存在: {feedback_id}")
    fb.status = status
    db.commit()
    return {"id": fb.id, "status": fb.status}


def wiki_index(db: Session, kb_id: str) -> dict:
    """Wiki 索引：目录树 + 页面按类型统计 + 最近更新列表。"""
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.created_at)
    ).scalars().all()
    pages = db.execute(
        select(WikiPage)
        .where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        .order_by(WikiPage.created_at.desc(), WikiPage.id.desc())
    ).scalars().all()

    by_type: dict[str, int] = {}
    for p in pages:
        by_type[p.page_type or "other"] = by_type.get(p.page_type or "other", 0) + 1

    recent = [
        {
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "folder_id": p.folder_id or "",
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in pages[:30]
    ]

    folder_tree = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id or ""} for f in folders
    ]

    return {
        "kb_id": kb_id,
        "folder_tree": folder_tree,
        "pages_by_type": by_type,
        "recent_pages": recent,
        "total_pages": len(pages),
        "total_folders": len(folders),
    }


def wiki_stats(db: Session, kb_id: str) -> dict:
    """Wiki 统计：页数（按类型）/ 目录数 / 链接数 / 孤儿页数（无入链无出链）。"""
    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id)
    ).scalars().all()
    links = db.execute(
        select(WikiLink).where(WikiLink.kb_id == kb_id)
    ).scalars().all()

    by_type: dict[str, int] = {}
    for p in pages:
        by_type[p.page_type or "other"] = by_type.get(p.page_type or "other", 0) + 1

    linked_page_ids = {
        pid
        for link in links
        for pid in (link.from_page_id, link.to_page_id)
    }
    orphan_count = sum(1 for p in pages if p.id not in linked_page_ids)

    return {
        "kb_id": kb_id,
        "total_pages": len(pages),
        "total_folders": len(folders),
        "total_links": len(links),
        "pages_by_type": by_type,
        "orphan_count": orphan_count,
    }


def wiki_create_page(db: Session, kb_id: str, data: dict, user_id: str | None = None) -> dict:
    """创建 wiki 页面。slug 缺省由 title 生成（保证 kb 内唯一，重复加序号）。"""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("title 不能为空")
    slug = str(data.get("slug") or "").strip() or slugify(title)
    existing = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if existing:
        raise ValueError(f"slug 已存在: {slug}")
    page = WikiPage(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        slug=slug,
        title=title,
        page_type=str(data.get("page_type") or "entity"),
        content=str(data.get("content") or ""),
        summary=data.get("summary") or None,
        source_refs=data.get("source_refs") or [],
        folder_id=str(data.get("folder_id") or ""),
        status="active",
        created_by=user_id,
    )
    db.add(page)
    wiki_log_action(
        db, kb_id, "page_create", slug=page.slug, title=page.title,
        detail=f"page_type={page.page_type}", operator=user_id or "",
    )
    db.commit()
    return {"id": page.id, "slug": page.slug, "title": page.title}


def wiki_update_page(db: Session, kb_id: str, slug: str, data: dict) -> dict:
    """更新 wiki 页面（标题/content/摘要/类型/目录/状态）。slug 本身不可改。"""
    page = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if not page:
        raise ValueError(f"wiki 页不存在: {slug}")
    if "title" in data:
        t = str(data["title"]).strip()
        if not t:
            raise ValueError("title 不能为空")
        page.title = t
    if "content" in data:
        page.content = str(data["content"] or "")
    if "summary" in data:
        page.summary = data["summary"] or None
    if "page_type" in data:
        page.page_type = str(data["page_type"] or "entity")
    if "folder_id" in data:
        page.folder_id = str(data["folder_id"] or "")
    if "status" in data:
        page.status = str(data["status"] or "active")
    wiki_log_action(
        db, kb_id, "page_update", slug=page.slug, title=page.title,
        detail=f"updated fields: {','.join(data.keys())}",
    )
    db.commit()
    return {"id": page.id, "slug": page.slug}


def wiki_delete_page(db: Session, kb_id: str, slug: str) -> dict:
    """删除 wiki 页面（软删：status=archived，保留数据便于恢复）。

    同时清理该页全部出入链（避免孤儿链接指向已归档页）。
    """
    page = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if not page:
        raise ValueError(f"wiki 页不存在: {slug}")
    page.status = "archived"
    # 清理关联链接：以该页为端点（出/入）的全部 wiki_link
    from sqlalchemy import delete as sa_delete

    db.execute(
        sa_delete(WikiLink).where(
            WikiLink.kb_id == kb_id,
            (WikiLink.from_page_id == page.id) | (WikiLink.to_page_id == page.id),
        )
    )
    wiki_log_action(
        db, kb_id, "page_delete", slug=page.slug, title=page.title, detail="soft-delete",
    )
    db.commit()
    return {"deleted": True, "slug": slug}


def wiki_create_folder(
    db: Session, kb_id: str, data: dict, user_id: str | None = None
) -> dict:
    """创建目录。parent_id 缺省为根。"""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    folder = WikiFolder(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        name=name,
        parent_id=str(data.get("parent_id") or ""),
        created_by=user_id,
    )
    db.add(folder)
    wiki_log_action(
        db, kb_id, "folder_create", title=folder.name, operator=user_id or "",
    )
    db.commit()
    return {"id": folder.id, "name": folder.name}


def wiki_update_folder(db: Session, kb_id: str, folder_id: str, data: dict) -> dict:
    """重命名 / 移动目录。"""
    folder = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id, WikiFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    if "name" in data:
        n = str(data["name"]).strip()
        if not n:
            raise ValueError("name 不能为空")
        folder.name = n
    if "parent_id" in data:
        folder.parent_id = str(data["parent_id"] or "")
    wiki_log_action(
        db, kb_id, "folder_update", title=folder.name,
        detail=f"updated: {','.join(data.keys())}",
    )
    db.commit()
    return {"id": folder.id, "name": folder.name}


def wiki_delete_folder(db: Session, kb_id: str, folder_id: str) -> dict:
    """删除目录：仅当目录下无页面时允许；有页面则抛错提示先移走。"""
    folder = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id, WikiFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    child_pages = db.execute(
        select(WikiPage).where(
            WikiPage.kb_id == kb_id,
            WikiPage.status == "active",
            WikiPage.folder_id == folder_id,
        )
    ).scalars().all()
    if child_pages:
        raise ValueError(f"目录下仍有 {len(child_pages)} 个页面，请先移走")
    child_folders = db.execute(
        select(WikiFolder).where(
            WikiFolder.kb_id == kb_id, WikiFolder.parent_id == folder_id
        )
    ).scalars().all()
    for cf in child_folders:
        cf.parent_id = ""  # 子目录上移到根
    wiki_log_action(
        db, kb_id, "folder_delete", title=folder.name,
        detail=f"moved up {len(child_folders)} child folders",
    )
    db.delete(folder)
    db.commit()
    return {"deleted": True, "folder_id": folder_id}


def wiki_lint(db: Session, kb_id: str, limit: int = 200) -> dict:
    """Wiki 健康检查：空内容页 / 孤立页（无入链无出链）/ 断链目标。

    返回 issue 列表：{slug, title, issue_type, detail}。
    """
    pages = {
        p.slug: p
        for p in db.execute(
            select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        ).scalars().all()
    }
    links = db.execute(
        select(WikiLink).where(WikiLink.kb_id == kb_id)
    ).scalars().all()

    page_ids = {p.id for p in pages.values()}
    slug_by_id = {p.id: p.slug for p in pages.values()}

    linked_ids = {pid for l in links for pid in (l.from_page_id, l.to_page_id)}
    issues: list[dict] = []

    # 空内容页
    for p in pages.values():
        if not (p.content or "").strip():
            issues.append(
                {"slug": p.slug, "title": p.title, "issue_type": "empty_content", "detail": "页面内容为空"}
            )

    # 孤立页（无入链且无出链）
    for p in pages.values():
        if p.id not in linked_ids:
            issues.append(
                {"slug": p.slug, "title": p.title, "issue_type": "orphan", "detail": "无任何双向链接"}
            )

    # 断链：content 中的 [[slug]] 目标不存在
    import re as _re

    broken = 0
    for p in pages.values():
        targets = _re.findall(r"\[\[([^\]|]+)", p.content or "")
        for t in targets:
            if t not in pages:
                broken += 1
        if broken > limit:
            break

    return {
        "kb_id": kb_id,
        "total_issues": len(issues),
        "issues": issues[:limit],
        "broken_link_count": broken,
    }


def wiki_rebuild_links(db: Session, kb_id: str) -> dict:
    """重建双向链接：清空该 kb 全部 wiki_link，按页面 content 中的 [[slug]]
    wikilink 重新生成（A→B 与 B→A 各一条）。"""
    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    slug_to_page = {p.slug: p for p in pages}

    # 清空旧链
    from sqlalchemy import delete as sa_delete

    db.execute(sa_delete(WikiLink).where(WikiLink.kb_id == kb_id))
    db.flush()

    import re as _re

    added = 0
    for p in pages:
        targets = _re.findall(r"\[\[([^\]|]+)", p.content or "")
        seen: set[str] = set()
        for t in targets:
            # 页面内去重：select 去重查不到本事务未 flush 的 pending 行，
            # 同页重复 wikilink 会插两条触发 uk_wiki_link_pair 冲突
            if t in seen:
                continue
            seen.add(t)
            target = slug_to_page.get(t)
            if not target or target.id == p.id:
                continue
            if (
                db.execute(
                    select(WikiLink).where(
                        WikiLink.kb_id == kb_id,
                        WikiLink.from_page_id == p.id,
                        WikiLink.to_page_id == target.id,
                    )
                ).scalars().first()
                is None
            ):
                db.add(
                    WikiLink(
                        id=uuid.uuid4().hex,
                        kb_id=kb_id,
                        from_page_id=p.id,
                        to_page_id=target.id,
                        link_type="related",
                    )
                )
                added += 1
    wiki_log_action(
        db, kb_id, "rebuild_links", detail=f"added {added} links",
    )
    db.commit()
    return {"added_links": added}


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
    "wiki_stats",
    "wiki_search",
    "wiki_lint",
    "wiki_create_page",
    "wiki_update_page",
    "wiki_delete_page",
    "wiki_create_folder",
    "wiki_update_folder",
    "wiki_delete_folder",
    "wiki_rebuild_links",
    "wiki_log_action",
    "wiki_list_logs",
    "wiki_submit_feedback",
    "wiki_list_feedback",
    "wiki_update_feedback_status",
    "wiki_index",
    "_remove_local_file",
    "slugify",
]
