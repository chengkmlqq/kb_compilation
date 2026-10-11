"""MCP server — expose knowledge-base tools to external agents.

Ports the data-synth MCP gateway pattern (tool registry -> platform actions)
onto the Python stack using the official `mcp` SDK (v2.x, `MCPServer`) with
stdio / streamable-http transports. Instead of forwarding to /api/open/*
REST routes, tools call the knowledge-domain services directly (retrieval /
QA), keeping a single in-process code path.

Tool *logic* lives in module-level functions (`handle_*`) so it is directly
testable; the MCP decorators are thin wrappers.

Exposed tools (initial set):
- kb_list    list knowledge bases visible to the caller
- kb_search  hybrid (vector + keyword + RRF) search inside one knowledge base
- kb_answer  RAG QA answer (non-streaming, convenience for agents)

Authorization (2026-10-11 补完): every tool REQUIRES a `sessionId` which is
resolved to the platform user via chat_session.user_id, then the same
visibility rules as the REST API apply (kb_visible_clauses: own / team /
system+admin). No sessionId -> tools refuse. This closes the original gap
where sessionId was accepted but never used for permission checks.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_sessionmaker
from api.models.chat_session import ChatSession
from api.models.knowledge import KbDatasource
from api.services.retrieval import RetrievalConfig, hybrid_search

logger = logging.getLogger(__name__)

# MCP 工具暴露给外部 agent 时携带的鉴权参数名（与平台会话语义一致）
_SESSION_ARG = "sessionId"


def new_session() -> Session:
    """A scoped framework-store session (handlers own the lifecycle)."""
    return get_sessionmaker()()


# ---------------------------------------------------------------------------
# Caller resolution (sessionId -> user -> visibility scope)
# ---------------------------------------------------------------------------


def resolve_caller(db: Session, session_id: str) -> dict | None:
    """Resolve MCP sessionId to a platform caller context.

    Returns {user_id, team_name, is_admin} or None when the session does not
    map to a known user (missing / invalid sessionId → tools refuse).
    """
    sid = (session_id or "").strip()
    if not sid:
        return None
    row = db.execute(
        select(ChatSession.user_id).where(ChatSession.id == sid)
    ).scalars().first()
    if not row:
        return None
    from api.services.scope import caller_context

    return caller_context(db, row)


def _visible_kb_stmt(db: Session, caller: dict):
    """Select state='1' KBs the caller may see (mirrors kb_visible_clauses)."""
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    from api.services.kb_admin import kb_visible_clauses

    clauses = kb_visible_clauses(
        caller.get("user_id", ""),
        caller.get("team_name", ""),
        bool(caller.get("is_admin")),
    )
    if clauses:
        stmt = stmt.where(clauses[0])
    return stmt


def _kb_visible(db: Session, kb_id: str, caller: dict) -> KbDatasource | None:
    """Fetch a KB only if the caller may see it; None otherwise (404 semantics)."""
    row = db.execute(
        _visible_kb_stmt(db, caller).where(KbDatasource.id == kb_id)
    ).scalars().first()
    return row


# ---------------------------------------------------------------------------
# Tool logic (module-level, testable)
# ---------------------------------------------------------------------------


def handle_kb_list(db: Session, team_name: str = "", caller: dict | None = None) -> dict:
    """List knowledge bases (optionally filtered by team) visible to caller.

    caller=None skips visibility filtering (internal/testing use only;
    the MCP entry always passes a resolved caller).
    """
    stmt = _visible_kb_stmt(db, caller) if caller else select(KbDatasource).where(KbDatasource.state == "1")
    if team_name:
        stmt = stmt.where(KbDatasource.team_name == team_name)
    rows = db.execute(stmt).scalars().all()
    return {
        "success": True,
        "items": [
            {
                "id": kb.id,
                "name": kb.name,
                "label": kb.label,
                "description": kb.description,
                "team_name": kb.team_name,
            }
            for kb in rows
        ],
    }


def handle_kb_search(
    db: Session,
    kb_id: str,
    query: str,
    top_k: int = 5,
    threshold: float = 0.2,
    caller: dict | None = None,
) -> dict:
    """Hybrid search (vector + keyword + RRF) inside one knowledge base.

    caller given: KB must be visible to the caller, else 404-style error.
    """
    kb = None
    if caller:
        kb = _kb_visible(db, kb_id, caller)
    else:
        kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
    if not kb:
        return {"success": False, "error": f"knowledge base not found or not visible: {kb_id}"}
    cfg = RetrievalConfig(top_k=max(1, min(top_k, 50)), threshold=threshold)
    hits = hybrid_search(db, kb_id, query, None, cfg)
    return {
        "success": True,
        "items": [
            {
                "chunk_id": h.chunk_id,
                "content": h.content,
                "score": round(float(h.score), 4),
                "document_id": h.document_id,
            }
            for h in hits
        ],
    }


def handle_kb_answer(db: Session, kb_id: str, question: str, caller: dict | None = None) -> dict:
    """Answer a question from one knowledge base (RAG, non-streaming).

    caller given: KB must be visible to the caller, else 404-style error.
    """
    from api.services.chat import ChatClient, build_messages, load_chat_config

    kb = None
    if caller:
        kb = _kb_visible(db, kb_id, caller)
    else:
        kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
    if not kb:
        return {"success": False, "error": f"knowledge base not found or not visible: {kb_id}"}
    cfg = RetrievalConfig(top_k=5)
    hits = hybrid_search(db, kb_id, question, None, cfg)
    if not hits:
        # 空库/无命中：不调 LLM 直接返回（对齐 test_handle_kb_answer_visible_kb_no_llm_crash
        # 的意图；否则空库在未配置 AI_CHAT_API_ENDPOINT 时 RuntimeError）
        return {
            "success": True,
            "answer": "知识库中暂未找到相关内容。",
            "references": [],
        }
    messages = build_messages(question, hits)
    # 零检索命中时不调用 LLM（无上下文可答，避免白耗配额/配置缺失时硬失败）
    if not hits:
        return {
            "success": True,
            "answer": "未在知识库中检索到相关内容，无法作答。",
            "references": [],
        }
    answer = ChatClient(load_chat_config(db)).chat(messages)
    return {
        "success": True,
        "answer": answer,
        "references": [h.chunk_id for h in hits[:5]],
    }


# ---------------------------------------------------------------------------
# MCP 对齐补充（2026-10-11）：WeKnora admin-kb 工具全集（文档/切片/wiki/图谱）
# ---------------------------------------------------------------------------


def _require_kb_visible(db: Session, kb_id: str, caller: dict, *, manage: bool = False):
    """KB 可见性/管理校验：read 用 can_see，write 用 can_manage（对齐 REST）。"""
    kb = db.execute(
        select(KbDatasource).where(KbDatasource.id == kb_id, KbDatasource.state == "1")
    ).scalars().first()
    if not kb:
        return {"success": False, "error": f"knowledge base not found: {kb_id}"}
    from api.services.scope import ResourceRow, can_manage, can_see

    ok = can_manage(
        ResourceRow.from_obj(kb), caller["user_id"], caller["team_name"], caller["is_admin"]
    ) if manage else can_see(
        ResourceRow.from_obj(kb), caller["user_id"], caller["team_name"], caller["is_admin"]
    )
    if not ok:
        return {"success": False, "error": f"knowledge base not found: {kb_id}"}
    return {"success": True, "kb": kb}


def handle_list_documents(
    db: Session, kb_id: str, page: int = 1, page_size: int = 20, caller: dict | None = None
) -> dict:
    """List documents in one knowledge base (对齐 WeKnora list_documents)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import list_documents

    data = list_documents(db, kb_id, page=page, page_size=page_size)
    return {"success": True, "items": data.get("items") or [], "total": data.get("total", 0)}


def handle_get_document(db: Session, knowledge_id: str, caller: dict | None = None) -> dict:
    """Get metadata for a single document by its UUID (对齐 WeKnora get_document)."""
    from api.models.knowledge import KbDocument

    doc = db.execute(
        select(KbDocument).where(KbDocument.id == knowledge_id)
    ).scalars().first()
    if not doc:
        return {"success": False, "error": f"document not found: {knowledge_id}"}
    if caller:
        chk = _require_kb_visible(db, doc.kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import get_document_detail

    return {"success": True, "data": get_document_detail(db, doc.kb_id, knowledge_id)}


def handle_list_chunks(
    db: Session, knowledge_id: str, page: int = 1, page_size: int = 20,
    caller: dict | None = None,
) -> dict:
    """List text chunks of a document (对齐 WeKnora list_chunks)."""
    from api.models.knowledge import DocChunk, KbDocument

    doc = db.execute(
        select(KbDocument).where(KbDocument.id == knowledge_id)
    ).scalars().first()
    if not doc:
        return {"success": False, "error": f"document not found: {knowledge_id}"}
    if caller:
        chk = _require_kb_visible(db, doc.kb_id, caller)
        if not chk["success"]:
            return chk
    stmt = (
        select(DocChunk)
        .where(DocChunk.document_id == knowledge_id, DocChunk.enabled == 1)
        .order_by(DocChunk.seq)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.execute(stmt).scalars().all()
    total = db.execute(
        select(DocChunk).where(DocChunk.document_id == knowledge_id, DocChunk.enabled == 1)
    ).scalars().all()
    return {
        "success": True,
        "items": [
            {"id": c.id, "seq": c.seq, "content": c.content, "document_id": c.document_id}
            for c in rows
        ],
        "total": len(total),
    }


def handle_wiki_search(
    db: Session, kb_id: str, query: str, limit: int = 20, caller: dict | None = None
) -> dict:
    """Search wiki pages within one KB (对齐 WeKnora wiki_search)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_search

    data = wiki_search(db, kb_id, query, limit=max(1, min(limit, 100)))
    return {"success": True, "items": data.get("items") or [], "total": data.get("total", 0)}


def handle_wiki_read_page(db: Session, kb_id: str, slug: str, caller: dict | None = None) -> dict:
    """Read a wiki page by slug (对齐 WeKnora wiki_read_page)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import get_wiki_page

    page = get_wiki_page(db, kb_id, slug)
    if not page:
        return {"success": False, "error": f"wiki page not found: {slug}"}
    return {"success": True, "data": page}


def handle_create_wiki_page(
    db: Session, kb_id: str, slug: str, title: str, content: str,
    summary: str = "", page_type: str = "entity", folder_id: str = "",
    caller: dict | None = None,
) -> dict:
    """Create a wiki page (对齐 WeKnora create_wiki_page)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_create_page

    try:
        data = wiki_create_page(
            db, kb_id,
            {"slug": slug, "title": title, "content": content, "summary": summary,
             "page_type": page_type, "folder_id": folder_id},
            user_id=caller.get("user_id", "") if caller else "",
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": data}


def handle_update_wiki_page(
    db: Session, kb_id: str, slug: str, title: str = "", summary: str = "",
    content: str = "", page_type: str = "", folder_id: str = "", status: str = "",
    caller: dict | None = None,
) -> dict:
    """Update a wiki page (对齐 WeKnora update_wiki_page)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_update_page

    data = {k: v for k, v in {
        "title": title, "summary": summary, "content": content,
        "page_type": page_type, "folder_id": folder_id, "status": status,
    }.items() if v}
    try:
        result = wiki_update_page(db, kb_id, slug, data)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_delete_wiki_page(db: Session, kb_id: str, slug: str, caller: dict | None = None) -> dict:
    """Soft-delete a wiki page (对齐 WeKnora delete_wiki_page)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_delete_page

    try:
        result = wiki_delete_page(db, kb_id, slug)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_move_wiki_page(
    db: Session, kb_id: str, slug: str, folder_id: str, caller: dict | None = None
) -> dict:
    """Move a wiki page into a folder (对齐 WeKnora move_wiki_page)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_move_page

    try:
        result = wiki_move_page(db, kb_id, slug, folder_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_list_wiki_pages(
    db: Session, kb_id: str, page: int = 1, page_size: int = 20,
    folder_id: str = "", page_type: str = "", status: str = "active",
    caller: dict | None = None,
) -> dict:
    """Paginated list of wiki pages (对齐 WeKnora list_wiki_pages)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import list_wiki_pages

    data = list_wiki_pages(
        db, kb_id, page=page, page_size=page_size,
        folder_id=folder_id, page_type=page_type, status=status,
    )
    return {"success": True, "items": data.get("items") or [], "total": data.get("total", 0)}


def handle_wiki_lookup_by_title(
    db: Session, kb_id: str, titles: list[str], page_types: list[str] | None = None,
    caller: dict | None = None,
) -> dict:
    """Batch exact-title lookup of wiki pages (对齐 WeKnora wiki_lookup_by_title)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_lookup_by_title

    data = wiki_lookup_by_title(db, kb_id, titles, page_types)
    return {"success": True, "items": data.get("items") or []}


def handle_wiki_create_pages(
    db: Session, kb_id: str, pages: list[dict], caller: dict | None = None
) -> dict:
    """Batch-create wiki pages (对齐 WeKnora wiki_create_pages)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_create_page

    created, errors = 0, []
    for i, p in enumerate(pages or []):
        try:
            wiki_create_page(
                db, kb_id,
                {"slug": p.get("slug", ""), "title": p.get("title", ""),
                 "content": p.get("content", ""), "summary": p.get("summary", ""),
                 "page_type": p.get("page_type", "entity"),
                 "folder_id": p.get("folder_id", "")},
                user_id=caller.get("user_id", "") if caller else "",
            )
            created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"index": i, "slug": p.get("slug", ""), "error": str(exc)[:120]})
    db.commit()
    return {"success": True, "created": created, "errors": errors}


def handle_list_wiki_folders(
    db: Session, kb_id: str, parent_id: str = "", include_empty: bool = False,
    caller: dict | None = None,
) -> dict:
    """List direct child folders (对齐 WeKnora list_wiki_folders)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_folders

    rows = wiki_folders(db, kb_id) or []
    if parent_id:
        rows = [r for r in rows if (r.get("parent_id") or "") == parent_id]
    if not include_empty:
        rows = [r for r in rows if r.get("name")]
    return {"success": True, "items": rows}


def handle_create_wiki_folder(
    db: Session, kb_id: str, name: str, parent_id: str = "", caller: dict | None = None
) -> dict:
    """Create a wiki folder (对齐 WeKnora create_wiki_folder)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_create_folder

    try:
        result = wiki_create_folder(db, kb_id, {"name": name, "parent_id": parent_id})
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_update_wiki_folder(
    db: Session, kb_id: str, folder_id: str, name: str = "", parent_id: str = "", 
    move_parent: bool = False, caller: dict | None = None,
) -> dict:
    """Rename/reparent a wiki folder (对齐 WeKnora update_wiki_folder)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_update_folder

    data = {}
    if name:
        data["name"] = name
    if parent_id is not None:
        data["parent_id"] = parent_id
    if move_parent:
        data["move_parent"] = True
    try:
        result = wiki_update_folder(db, kb_id, folder_id, data)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_delete_wiki_folder(db: Session, kb_id: str, folder_id: str, caller: dict | None = None) -> dict:
    """Delete an empty wiki folder (对齐 WeKnora delete_wiki_folder)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_delete_folder

    try:
        result = wiki_delete_folder(db, kb_id, folder_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_wiki_graph(
    db: Session, kb_id: str, mode: str = "overview", center: str = "",
    depth: int | None = None, limit: int | None = None, types: str = "",
    caller: dict | None = None,
) -> dict:
    """Return a slice of the wiki link graph (对齐 WeKnora wiki_graph)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_graph

    try:
        data = wiki_graph(db, kb_id)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"wiki graph failed: {exc}"}
    return {"success": True, "data": data}


def handle_graph_add(
    db: Session, kb_id: str, graphs: list[dict], knowledge_id: str = "",
    caller: dict | None = None,
) -> dict:
    """Write nodes+relations into the Neo4j graph (对齐 WeKnora graph_add)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.graph import Entity, Neo4jGraphStore, Relationship
    from api.config import get_settings

    s = get_settings()
    if not (s.NEO4J_URI and s.NEO4J_PASSWORD):
        return {"success": False, "error": "Neo4j 未配置（缺少 NEO4J_URI / NEO4J_PASSWORD）"}
    store = Neo4jGraphStore(s.NEO4J_URI, s.NEO4J_USERNAME or "neo4j", s.NEO4J_PASSWORD, s.NEO4J_DATABASE)
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    for g in graphs or []:
        for n in g.get("node") or []:
            entities.append(Entity(
                title=n.get("name") or n.get("title") or "",
                entity_type=n.get("entity_type") or "Entity",
                chunk_ids=n.get("chunks") or [],
            ))
        for r in g.get("relation") or []:
            relationships.append(Relationship(
                source=r.get("node1") or "", target=r.get("node2") or "",
                relation_type=r.get("type") or "related",
            ))
    if not entities and not relationships:
        return {"success": False, "error": "graphs 为空（无 node/relation 数据）"}
    try:
        store.write_graph(kb_id, entities, relationships)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"图谱写入失败: {exc}"}
    return {"success": True, "entities": len(entities), "relationships": len(relationships)}


def handle_graph_delete(
    db: Session, kb_id: str, knowledge_id: str = "", caller: dict | None = None
) -> dict:
    """Delete the Neo4j graph of a KB or one file (对齐 WeKnora graph_delete)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.graph import Neo4jGraphStore
    from api.config import get_settings

    s = get_settings()
    if not (s.NEO4J_URI and s.NEO4J_PASSWORD):
        return {"success": False, "error": "Neo4j 未配置（缺少 NEO4J_URI / NEO4J_PASSWORD）"}
    store = Neo4jGraphStore(s.NEO4J_URI, s.NEO4J_USERNAME or "neo4j", s.NEO4J_PASSWORD, s.NEO4J_DATABASE)
    try:
        if knowledge_id:
            cnt = store.delete_doc_graph(document_id=knowledge_id)
        else:
            cnt = store.delete_kb_graph(kb_id=kb_id)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"图谱删除失败: {exc}"}
    return {"success": True, "deleted": cnt}


def handle_graph_search_node(
    db: Session, kb_id: str, query: str, caller: dict | None = None
) -> dict:
    """Search Neo4j graph nodes by name fragment (对齐 WeKnora graph_search_node)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.graph_query import get_graph_reader

    reader = get_graph_reader()
    if reader is None:
        return {"success": False, "error": "Neo4j 未配置"}
    try:
        data = reader.search(kb_id, query, limit=100)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"图节点搜索失败: {exc}"}
    return {"success": True, "data": data}


def handle_wiki_stats(db: Session, kb_id: str, caller: dict | None = None) -> dict:
    """Return aggregate wiki statistics (对齐 WeKnora wiki_stats)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_stats

    return {"success": True, "data": wiki_stats(db, kb_id)}


def handle_wiki_index_view(
    db: Session, kb_id: str, limit: int = 50, types: str = "", caller: dict | None = None
) -> dict:
    """Get the wiki index view (对齐 WeKnora wiki_index_view)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_index

    data = wiki_index(db, kb_id)
    return {"success": True, "data": data}


def handle_wiki_rebuild_links(db: Session, kb_id: str, caller: dict | None = None) -> dict:
    """Re-parse all pages and rebuild bidirectional links (对齐 WeKnora)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_rebuild_links

    return {"success": True, "data": wiki_rebuild_links(db, kb_id)}


def handle_wiki_lint(db: Session, kb_id: str, caller: dict | None = None) -> dict:
    """Run a comprehensive wiki health check (对齐 WeKnora wiki_lint)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_lint

    return {"success": True, "data": wiki_lint(db, kb_id)}


def handle_wiki_auto_fix(db: Session, kb_id: str, caller: dict | None = None) -> dict:
    """Automatically fix fixable wiki issues (对齐 WeKnora wiki_auto_fix)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_auto_fix

    return {"success": True, "data": wiki_auto_fix(db, kb_id)}


def handle_wiki_log(db: Session, kb_id: str, limit: int = 50, cursor: str = "", caller: dict | None = None) -> dict:
    """Get a paginated feed of wiki operation events (对齐 WeKnora wiki_log)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_list_logs

    data = wiki_list_logs(db, kb_id, limit=max(1, min(limit, 200)))
    return {"success": True, "items": data.get("items") or [], "total": data.get("total", 0)}


def handle_wiki_log_write(
    db: Session, kb_id: str, action: str, knowledge_id: str = "", doc_title: str = "",
    summary: str = "", page_slugs: list[str] | None = None, caller: dict | None = None,
) -> dict:
    """Append a wiki operation/progress event (对齐 WeKnora wiki_log_write)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_log_write

    try:
        result = wiki_log_write(
            db, kb_id, action, knowledge_id=knowledge_id, doc_title=doc_title,
            summary=summary, page_slugs=page_slugs,
            operator=caller.get("user_id", "") if caller else "",
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_wiki_list_issues(
    db: Session, kb_id: str, slug: str = "", status: str = "", caller: dict | None = None
) -> dict:
    """List issues flagged on wiki pages (对齐 WeKnora wiki_list_issues)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_list_issues

    data = wiki_list_issues(db, kb_id, slug=slug, status=status)
    return {"success": True, "items": data.get("items") or [], "total": data.get("total", 0)}


def handle_wiki_update_issue_status(
    db: Session, kb_id: str, issue_id: str, status: str, caller: dict | None = None
) -> dict:
    """Set an issue status: pending | ignored | resolved (对齐 WeKnora)."""
    if caller:
        chk = _require_kb_visible(db, kb_id, caller, manage=True)
        if not chk["success"]:
            return chk
    from api.services.kb_admin import wiki_update_issue_status

    try:
        result = wiki_update_issue_status(db, kb_id, issue_id, status)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": result}


def handle_tokenize(text: str, mode: str = "cut") -> dict:
    """Tokenize text (对齐 WeKnora tokenize)."""
    from api.services.kb_admin import tokenize_text

    data = tokenize_text(text, mode=mode)
    return {"success": True, "words": data["words"], "count": data["count"]}


# ---------------------------------------------------------------------------
# MCP wiring (thin wrappers) — sessionId REQUIRED for every tool
# ---------------------------------------------------------------------------


def _require_caller(db: Session, session_id: str) -> dict | None:
    """Resolve + log; MCP entry refuses when sessionId is missing/invalid."""
    caller = resolve_caller(db, session_id)
    if caller is None:
        logger.warning("mcp: rejected call without valid sessionId")
    return caller


def create_mcp_server() -> MCPServer:
    mcp = MCPServer(
        name="kb-tools",
        instructions=(
            "KB compilation platform tools: list knowledge bases, hybrid "
            "search inside a knowledge base, and RAG question answering. "
            "Every tool requires a valid sessionId for permission checks."
        ),
    )

    @mcp.tool()
    def kb_list(team_name: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_kb_list(db, team_name, caller)
        finally:
            db.close()

    @mcp.tool()
    def kb_search(
        kb_id: str,
        query: str,
        top_k: int = 5,
        threshold: float = 0.2,
        sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_kb_search(db, kb_id, query, top_k, threshold, caller)
        finally:
            db.close()

    @mcp.tool()
    def kb_answer(kb_id: str, question: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_kb_answer(db, kb_id, question, caller)
        finally:
            db.close()

    # ── WeKnora admin-kb 对齐工具（文档/切片/wiki/图谱，2026-10-11）──

    @mcp.tool()
    def list_documents(kb_id: str, page: int = 1, page_size: int = 20, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_list_documents(db, kb_id, page, page_size, caller)
        finally:
            db.close()

    @mcp.tool()
    def get_document(knowledge_id: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_get_document(db, knowledge_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def list_chunks(knowledge_id: str, page: int = 1, page_size: int = 20, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_list_chunks(db, knowledge_id, page, page_size, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_search(kb_id: str, query: str, limit: int = 20, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_search(db, kb_id, query, limit, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_read_page(kb_id: str, slug: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_read_page(db, kb_id, slug, caller)
        finally:
            db.close()

    @mcp.tool()
    def create_wiki_page(
        kb_id: str, slug: str, title: str, content: str,
        summary: str = "", page_type: str = "entity", folder_id: str = "",
        sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_create_wiki_page(db, kb_id, slug, title, content, summary, page_type, folder_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def update_wiki_page(
        kb_id: str, slug: str, title: str = "", summary: str = "",
        content: str = "", page_type: str = "", folder_id: str = "", status: str = "",
        sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_update_wiki_page(db, kb_id, slug, title, summary, content, page_type, folder_id, status, caller)
        finally:
            db.close()

    @mcp.tool()
    def delete_wiki_page(kb_id: str, slug: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_delete_wiki_page(db, kb_id, slug, caller)
        finally:
            db.close()

    @mcp.tool()
    def move_wiki_page(kb_id: str, slug: str, folder_id: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_move_wiki_page(db, kb_id, slug, folder_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def list_wiki_pages(
        kb_id: str, page: int = 1, page_size: int = 20,
        folder_id: str = "", page_type: str = "", status: str = "active",
        sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_list_wiki_pages(db, kb_id, page, page_size, folder_id, page_type, status, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_lookup_by_title(kb_id: str, titles: list[str], page_types: list[str] | None = None, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_lookup_by_title(db, kb_id, titles, page_types, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_create_pages(kb_id: str, pages: list[dict], sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_create_pages(db, kb_id, pages, caller)
        finally:
            db.close()

    @mcp.tool()
    def list_wiki_folders(kb_id: str, parent_id: str = "", include_empty: bool = False, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_list_wiki_folders(db, kb_id, parent_id, include_empty, caller)
        finally:
            db.close()

    @mcp.tool()
    def create_wiki_folder(kb_id: str, name: str, parent_id: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_create_wiki_folder(db, kb_id, name, parent_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def update_wiki_folder(
        kb_id: str, folder_id: str, name: str = "", parent_id: str = "",
        move_parent: bool = False, sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_update_wiki_folder(db, kb_id, folder_id, name, parent_id, move_parent, caller)
        finally:
            db.close()

    @mcp.tool()
    def delete_wiki_folder(kb_id: str, folder_id: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_delete_wiki_folder(db, kb_id, folder_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_graph(
        kb_id: str, mode: str = "overview", center: str = "",
        depth: int | None = None, limit: int | None = None, types: str = "",
        sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_graph(db, kb_id, mode, center, depth, limit, types, caller)
        finally:
            db.close()

    @mcp.tool()
    def graph_add(kb_id: str, graphs: list[dict], knowledge_id: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_graph_add(db, kb_id, graphs, knowledge_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def graph_delete(kb_id: str, knowledge_id: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_graph_delete(db, kb_id, knowledge_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def graph_search_node(kb_id: str, query: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_graph_search_node(db, kb_id, query, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_stats(kb_id: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_stats(db, kb_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_index_view(kb_id: str, limit: int = 50, types: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_index_view(db, kb_id, limit, types, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_rebuild_links(kb_id: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_rebuild_links(db, kb_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_lint(kb_id: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_lint(db, kb_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_auto_fix(kb_id: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_auto_fix(db, kb_id, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_log(kb_id: str, limit: int = 50, cursor: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_log(db, kb_id, limit, cursor, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_log_write(
        kb_id: str, action: str, knowledge_id: str = "", doc_title: str = "",
        summary: str = "", page_slugs: list[str] | None = None, sessionId: str = "",
    ) -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_log_write(db, kb_id, action, knowledge_id, doc_title, summary, page_slugs, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_list_issues(kb_id: str, slug: str = "", status: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_list_issues(db, kb_id, slug, status, caller)
        finally:
            db.close()

    @mcp.tool()
    def wiki_update_issue_status(kb_id: str, issue_id: str, status: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_wiki_update_issue_status(db, kb_id, issue_id, status, caller)
        finally:
            db.close()

    @mcp.tool()
    def tokenize(text: str, mode: str = "cut", sessionId: str = "") -> dict:
        db = new_session()
        try:
            caller = _require_caller(db, sessionId)
            if caller is None:
                return {"success": False, "error": "无效的 sessionId（无法解析调用者身份）"}
            return handle_tokenize(text, mode)
        finally:
            db.close()

    return mcp


def main() -> None:
    """Run the MCP server (stdio by default; streamable-http in production)."""
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
