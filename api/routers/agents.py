"""Agent API routes — CRUD + agent-bound QA streaming."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbAgent
from api.services.agents import (
    create_agent,
    delete_agent,
    effective_system_prompt,
    list_agents,
    resolve_agent_qa_overrides,
    resolve_kb_scope,
    update_agent,
)
from api.services.agent_skills import agent_skill_tool_context, run_skill_tool_call
from api.services.chat import SYSTEM_PROMPT, ChatConfig, ChatMessage, answer_question
from api.services.embedding import get_embedding_client
from api.services.identity import decode_identity_cookie

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
    session_id: str | None = None  # 会话模式：持久化 + 加载历史 + 附件注入


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/{agent_id}/qa/stream")
def agent_qa_stream(
    agent_id: str,
    req: AgentQARequest,
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> StreamingResponse:
    """Stream RAG QA through an agent: resolve KB scope -> retrieve -> answer.

    session_id mode: persists messages to the chat_session, loads history,
    and injects session attachments into the prompt (agent conversation).
    """

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

        # 会话模式：校验 + 加载历史 + 附件
        user_id = None
        history: list[dict] = []
        attachment_ctx = ""
        image_urls: list[str] = []
        # 身份（无 session 也解析：用于个人/团队模型作用域）
        identity = decode_identity_cookie(x_next_identity or "")
        ctx_user_id = identity.user_id if identity else ""
        ctx_team_name = identity.team_name or "" if identity else ""
        ctx_is_admin = False
        if ctx_user_id:
            try:
                from api.services.models import is_admin

                ctx_is_admin = is_admin(db, ctx_user_id)
            except Exception:  # noqa: BLE001
                ctx_is_admin = False

        if req.session_id:
            if not identity or not identity.user_id:
                yield _sse({"type": "error", "message": "未登录"})
                yield _sse({"type": "done"})
                return
            user_id = identity.user_id
            try:
                from api.services.chat_sessions import (
                    append_message,
                    attachment_contents,
                    attachment_images,
                    get_context_messages,
                    get_session,
                )

                get_session(db, user_id, req.session_id)
                history = [
                    {"role": m["role"], "content": m["content"]}
                    for m in get_context_messages(db, req.session_id, limit=20)
                ]
                attachment_ctx = attachment_contents(db, req.session_id)
                image_urls = attachment_images(db, req.session_id)
                # 持久化用户消息
                append_message(db, user_id, req.session_id, "user", req.question)
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "message": str(e)})
                yield _sse({"type": "done"})
                return
        else:
            history = [
                {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                for m in req.history
                if m.get("content")
            ]

        # Retrieval overrides from the agent config.
        overrides = resolve_agent_qa_overrides(agent)
        query_embedding = None
        if overrides.get("vector_enabled", True):
            try:
                query_embedding = get_embedding_client(
                    db,
                    user_id=ctx_user_id,
                    team_name=ctx_team_name,
                    is_sys_admin=ctx_is_admin,
                ).embed_query(req.question)
            except Exception:  # noqa: BLE001
                logger.exception("query embedding failed; vector arm disabled")
                overrides["vector_enabled"] = False

        # Agent system prompt（含附件注入时合并）
        try:
            from api.services.agents import effective_system_prompt
            from api.services.chat import SYSTEM_PROMPT

            agent_prompt = effective_system_prompt(agent, default=SYSTEM_PROMPT)
        except Exception:  # noqa: BLE001
            agent_prompt = None

        # 技能白名单消费：仅当 agent 配置 skills_enabled 时注入 run_skill_script 工具，
        # 白名单语义 selected=仅 selected_skills / all=全部可见技能 / none=不注入。
        # skills_enabled=False（默认）时 skill_tools 为 None，问答链路与旧行为完全一致。
        try:
            skill_rows, skill_tools, skill_max_rounds = agent_skill_tool_context(
                db,
                agent,
                caller_user_id=ctx_user_id,
                caller_team_name=ctx_team_name,
                is_sys_admin=ctx_is_admin,
            )
        except Exception:  # noqa: BLE001 — 技能装配失败不阻断问答
            logger.exception("agent skill whitelist resolution failed; skills disabled for this call")
            skill_rows, skill_tools, skill_max_rounds = [], None, 1
        skill_executor = (
            (lambda tc: run_skill_tool_call(skill_rows, tc)) if skill_tools else None
        )

        answer_parts: list[str] = []
        refs: list[dict] = []
        last_kb_id = ""
        try:
            if image_urls:
                # 图片问答要求对话模型支持视觉（supports_vision）
                try:
                    from api.models.model import KbModel
                    from api.services.chat import load_chat_config

                    chat_cfg = load_chat_config(
                        db,
                        user_id=ctx_user_id,
                        team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                    )
                    mrow = db.execute(
                        select(KbModel).where(KbModel.name == chat_cfg.model)
                    ).scalars().first()
                    if mrow and not mrow.supports_vision:
                        yield _sse({"type": "error", "message": "当前对话模型不支持图片输入，请在模型配置中选用支持视觉的对话模型"})
                        yield _sse({"type": "done"})
                        return
                except Exception:  # noqa: BLE001
                    pass
            # 对每个 KB 依次流式回答；extra_context/图片只在首个 KB 注入避免重复
            for idx, kb in enumerate(kbs):
                last_kb_id = kb.id
                extra = attachment_ctx if idx == 0 else ""
                for event in answer_question(
                    kb_db=db,
                    kb=kb,
                    question=req.question,
                    query_embedding=query_embedding,
                    retrieval_overrides=overrides,
                    history=history,
                    chat_cfg=None,
                    extra_context=extra,
                    image_urls=image_urls if idx == 0 else None,
                    agent_prompt=agent_prompt,
                    user_id=ctx_user_id,
                    team_name=ctx_team_name,
                    is_sys_admin=ctx_is_admin,
                    tools=skill_tools,
                    tool_executor=skill_executor,
                    max_tool_iterations=skill_max_rounds,
                ):
                    event["kb_id"] = kb.id
                    if event.get("type") == "delta":
                        answer_parts.append(event.get("text", ""))
                    elif event.get("type") == "context":
                        hits = event.get("hits") or []
                        refs = [
                            {"chunk_id": h.get("chunk_id", ""), "score": h.get("score", 0)}
                            for h in hits
                        ]
                    yield _sse(event)
        except Exception as e:  # noqa: BLE001
            logger.exception("agent qa failed for kb %s", last_kb_id)
            yield _sse({"type": "error", "message": str(e), "kb_id": last_kb_id})
        finally:
            # 会话模式：持久化助手回答
            if req.session_id and user_id:
                try:
                    from api.services.chat_sessions import append_message

                    append_message(
                        db, user_id, req.session_id, "assistant", "".join(answer_parts), refs=refs
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("failed to persist agent assistant message: %s", e)
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
