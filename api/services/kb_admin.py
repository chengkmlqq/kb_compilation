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
    scope: str = "system",
    owner_user_id: str | None = None,
    type: str = "document",
    custom_wiki_generation: bool = False,
    embedding_model_id: str | None = None,
    summary_model_id: str | None = None,
    storage_backend_id: str | None = None,
    vector_store_id: str | None = None,
    configs: dict | None = None,
) -> KbDatasource:
    """Create a KB; name is required, id is generated if absent.

    scope: personal(owner_user_id) / team(team_name) / system(admin).
    indexing_strategy 归一为 WeKnora 四路开关（缺失键用 WeKnora 默认）。
    configs 为其余 JSON 配置（wiki_config/extract_config/faq_config 等）。
    """
    from api.services.kb_config import normalize_indexing_strategy

    cfg = dict(configs or {})
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
    if "type" in fields:
        t = str(fields.get("type") or "document")
        kb.type = t if t in KB_TYPES else kb.type
    if "custom_wiki_generation" in fields:
        kb.custom_wiki_generation = bool(fields["custom_wiki_generation"])
    for f in ("embedding_model_id", "summary_model_id", "storage_backend_id", "vector_store_id"):
        if f in fields:
            setattr(kb, f, str(fields.get(f) or "") or None)
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

        get_vector_store().delete_by_kb(kb_id)
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
    """删除物理文件（本地磁盘或 MinIO 对象，幂等）。"""
    if not storage_path:
        return
    try:
        from api.services.storage import delete as storage_delete

        storage_delete(storage_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to remove stored file %s: %s", storage_path, exc)


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
            .order_by(WikiOperationLog.created_at.desc())
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
        db.execute(stmt.order_by(WikiFeedback.created_at.desc()).limit(limit))
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
        .order_by(WikiPage.created_at.desc())
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
        for t in targets:
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
