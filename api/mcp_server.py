"""MCP server — expose knowledge-base tools to external agents.

Ports the data-synth MCP gateway pattern (tool registry -> platform actions)
onto the Python stack using the official `mcp` SDK (v2.x, `MCPServer`) with
stdio / streamable-http transports. Instead of forwarding to /api/open/*
REST routes, tools call the knowledge-domain services directly (retrieval /
QA), keeping a single in-process code path.

Tool *logic* lives in module-level functions (`handle_*`) so it is directly
testable; the MCP decorators are thin wrappers.

Exposed tools (initial set):
- kb_list    list knowledge bases visible to a team
- kb_search  hybrid (vector + keyword + RRF) search inside one knowledge base
- kb_answer  RAG QA answer (non-streaming, convenience for agents)

Every tool accepts an optional `sessionId` so the platform can map the call
to a real user for permission checks and call tracing (the source platform
makes sessionId mandatory on all 45 of its tools).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_knowledge_sessionmaker
from api.models.knowledge import KbDatasource
from api.services.retrieval import RetrievalConfig, hybrid_search

logger = logging.getLogger(__name__)


def new_session() -> Session:
    """A scoped knowledge-store session (handlers own the lifecycle)."""
    return get_knowledge_sessionmaker()()


# ---------------------------------------------------------------------------
# Tool logic (module-level, testable)
# ---------------------------------------------------------------------------


def handle_kb_list(db: Session, team_name: str = "") -> dict:
    """List knowledge bases (optionally filtered by team)."""
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
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
) -> dict:
    """Hybrid search (vector + keyword + RRF) inside one knowledge base."""
    kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
    if not kb:
        return {"success": False, "error": f"knowledge base not found: {kb_id}"}
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


def handle_kb_answer(db: Session, kb_id: str, question: str) -> dict:
    """Answer a question from one knowledge base (RAG, non-streaming)."""
    from api.services.chat import ChatClient, build_messages, load_chat_config

    kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
    if not kb:
        return {"success": False, "error": f"knowledge base not found: {kb_id}"}
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
# MCP wiring (thin wrappers)
# ---------------------------------------------------------------------------


def create_mcp_server() -> MCPServer:
    mcp = MCPServer(
        name="kb-tools",
        instructions=(
            "KB compilation platform tools: list knowledge bases, hybrid "
            "search inside a knowledge base, and RAG question answering."
        ),
    )

    @mcp.tool()
    def kb_list(team_name: str = "", sessionId: str = "") -> dict:
        db = new_session()
        try:
            return handle_kb_list(db, team_name)
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
            return handle_kb_search(db, kb_id, query, top_k, threshold)
        finally:
            db.close()

    @mcp.tool()
    def kb_answer(kb_id: str, question: str, sessionId: str = "") -> dict:
        db = new_session()
        try:
            return handle_kb_answer(db, kb_id, question)
        finally:
            db.close()

    return mcp


def main() -> None:
    """Run the MCP server (stdio by default; streamable-http in production)."""
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()