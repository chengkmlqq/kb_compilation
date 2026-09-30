"""Agent API routes — CRUD + agent-bound QA streaming."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbAgent
from api.services.agents import (
    create_agent,
    delete_agent,
    list_agents,
    resolve_agent_qa_overrides,
    resolve_kb_scope,
    update_agent,
)
from api.services.chat import SYSTEM_PROMPT, ChatConfig, ChatMessage, answer_question
from api.services.embedding import get_embedding_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    avatar: str | None = None
    config: dict | None = None


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    avatar: str | None = None
    config: dict | None = None


@router.post("")
def create(req: AgentCreateRequest, db: Session = Depends(get_db)) -> dict:
    agent = create_agent(
        db,
        name=req.name,
        team_name=None,  # identity scoping lands with auth middleware
        created_by=None,
        description=req.description,
        avatar=req.avatar,
        config=req.config,
    )
    return {"success": True, "data": {"id": agent.id, "name": agent.name}}


@router.get("")
def list_agents_route(page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_agents(db, page=page, page_size=page_size)}


@router.put("/{agent_id}")
def update(agent_id: str, req: AgentUpdateRequest, db: Session = Depends(get_db)) -> dict:
    agent = update_agent(db, agent_id, req.model_dump(exclude_none=True))
    if not agent:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    return {"success": True, "data": {"id": agent.id}}


@router.delete("/{agent_id}")
def delete(agent_id: str, db: Session = Depends(get_db)) -> dict:
    if not delete_agent(db, agent_id):
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    return {"success": True}


class AgentQARequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[dict] = Field(default_factory=list)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/{agent_id}/qa/stream")
def agent_qa_stream(agent_id: str, req: AgentQARequest, db: Session = Depends(get_db)) -> StreamingResponse:
    """Stream RAG QA through an agent: resolve KB scope -> retrieve -> answer."""

    def generator():
        agent = db.execute(select(KbAgent).where(KbAgent.id == agent_id)).scalars().first()
        if not agent:
            yield _sse({"type": "error", "message": f"agent not found: {agent_id}"})
            yield _sse({"type": "done"})
            return

        kbs = resolve_kb_scope(db, agent)
        if not kbs:
            yield _sse({"type": "error", "message": "agent 未绑定可用知识库"})
            yield _sse({"type": "done"})
            return

        # Retrieval overrides from the agent config.
        overrides = resolve_agent_qa_overrides(agent)
        query_embedding = None
        if overrides.get("vector_enabled", True):
            try:
                query_embedding = get_embedding_client(db).embed_query(req.question)
            except Exception:  # noqa: BLE001
                logger.exception("query embedding failed; vector arm disabled")
                overrides["vector_enabled"] = False

        history = [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in req.history
            if m.get("content")
        ]

        # Agent system prompt (with the shared RAG context placeholder injected
        # per KB later — for simplicity we stream per-KB answers sequentially).
        for kb in kbs:
            try:
                for event in answer_question(
                    kb_db=db,
                    kb=kb,
                    question=req.question,
                    query_embedding=query_embedding,
                    retrieval_overrides=overrides,
                    history=history,
                ):
                    event["kb_id"] = kb.id
                    yield _sse(event)
            except Exception as e:  # noqa: BLE001
                logger.exception("agent qa failed for kb %s", kb.id)
                yield _sse({"type": "error", "message": str(e), "kb_id": kb.id})
        yield _sse({"type": "done"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
