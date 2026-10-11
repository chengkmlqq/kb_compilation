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
    messages = build_messages(question, hits)
    answer = ChatClient(load_chat_config(db)).chat(messages)
    return {
        "success": True,
        "answer": answer,
        "references": [h.chunk_id for h in hits[:5]],
    }


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

    return mcp


def main() -> None:
    """Run the MCP server (stdio by default; streamable-http in production)."""
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
