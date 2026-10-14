"""Agent API routes — CRUD + agent-bound QA streaming."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbAgent
from api.services.agents import (
    config_from_dict,
    copy_agent,
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
def list_agents_route(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_agents(db, page=page, page_size=page_size)}


@router.get("/tool-catalog")
def tool_catalog_route() -> dict:
    """可装配的工具目录（前端工具选择列表，对齐 WeKnora 全目录）。"""
    from api.services.agent_tools_full import TOOL_CATALOG

    return {"success": True, "data": {"tools": TOOL_CATALOG}}


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


@router.post("/{agent_id}/copy")
def copy(agent_id: str, db: Session = Depends(get_db)) -> dict:
    agent = copy_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    return {"success": True, "data": {"id": agent.id, "name": agent.name}}


class AgentQARequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[dict] = Field(default_factory=list)
    session_id: str | None = None  # 会话模式：持久化 + 加载历史 + 附件注入
    # 2026-10-12: 问答界面的模型选择此前只走 /qa/stream，agent 模式前端仍在
    # 发送 model_id 但此处无对应字段 → pydantic 静默丢弃，用户选了模型不生效。
    # 与 qa.py 的 QARequest.model_id 对齐：不传=用默认模型。
    model_id: str | None = None
    # 2026-10-12 对齐 WeKnora 输入区联网开关：请求级显式开关（None=跟随 agent 配置）
    web_search_enabled: bool | None = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("问题不能为空")
        return v


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
        # 2026-10-14 done 事件回传持久化消息 id（前端对齐服务端 id，避免点赞 404「消息不存在」）
        user_message_id = ""
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
                persisted = append_message(db, user_id, req.session_id, "user", req.question)
                user_message_id = persisted.get("id") or ""
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

        # 联网搜索工具：仅当 agent 配置 web_search_enabled 时注入 web_search，
        # provider 按 agent.web_search_provider_id（或调用方可见的 system provider）
        # 解析并做可见性校验；未启用/无可用 provider 时不注入。
        ws_tools: list[dict] = []
        ws_executor = None
        try:
            from api.services.websearch import agent_websearch_tool_context

            ws_tools, ws_executor = agent_websearch_tool_context(
                db,
                agent,
                caller_user_id=ctx_user_id,
                caller_team_name=ctx_team_name,
                is_sys_admin=ctx_is_admin,
                enabled_override=req.web_search_enabled,
            )
        except Exception:  # noqa: BLE001 — 联网搜索装配失败不阻断问答
            logger.exception("agent websearch tool resolution failed; websearch disabled for this call")
            ws_tools, ws_executor = [], None

        # 合并工具集：技能 run_skill_script + 联网 web_search（按工具名分派 executor）
        merged_tools = (skill_tools or []) + ws_tools
        if ws_executor is not None and skill_executor is not None:
            merged_executor = lambda tc: (  # noqa: E731
                ws_executor(tc)
                if str(tc.get("name") or "") == "web_search"
                else skill_executor(tc)
            )
        else:
            merged_executor = ws_executor or skill_executor

        # allowed_tools 白名单：非空 = 仅注入白名单内已装配的工具；
        # 空 = 默认只读工具（安全默认——写工具/wiki写需显式 allowed_tools 才注入）。
        # web_search / run_skill_script 由各自 enabled 开关控制装配，白名单不裁剪。
        agent_cfg = config_from_dict(agent.config or {})
        tool_whitelist = {str(t).strip() for t in (agent_cfg.allowed_tools or []) if str(t).strip()}
        DEFAULT_READONLY_TOOLS = {
            "thinking", "todo_write", "knowledge_search", "kb_search", "grep_chunks",
            "list_knowledge_chunks", "get_document_info", "database_query",
            "query_knowledge_graph", "wiki_search", "wiki_read_page",
            "wiki_read_source_doc", "wiki_read_issue", "data_schema", "data_analysis",
        }
        DYNAMIC_TOOLS = {"web_search", "run_skill_script"}

        def filter_by_whitelist(tools: list[dict]) -> list[dict]:
            # 显式白名单 = 严格裁剪（dynamic 工具也受白名单约束，勾选才生效）；
            # 空 = 默认只读工具 + dynamic（web_search/run_skill_script 由各自开关控制）
            allow = tool_whitelist if tool_whitelist else (
                DEFAULT_READONLY_TOOLS | DYNAMIC_TOOLS
            )
            return [
                t for t in tools
                if str((t.get("function") or {}).get("name") or "") in allow
            ]

        answer_parts: list[str] = []
        refs: list[dict] = []
        last_kb_id = ""
        # 2026-10-12: 模型选择生效——指定模型则显式解析（文本/图片均生效），
        # 与 qa.py 的 _run_generation 同款逻辑；解析失败回退默认模型。
        requested_cfg = None
        if req.model_id:
            try:
                from api.services.chat import load_chat_config

                requested_cfg = load_chat_config(
                    db,
                    user_id=ctx_user_id,
                    team_name=ctx_team_name,
                    is_sys_admin=ctx_is_admin,
                    model_id=req.model_id,
                )
            except Exception:  # noqa: BLE001
                requested_cfg = None
        try:
            if image_urls:
                # 图片问答要求对话模型支持视觉（supports_vision）
                try:
                    from api.models.model import KbModel
                    from api.services.chat import load_chat_config

                    chat_cfg = requested_cfg or load_chat_config(
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
            # 多 KB 时均摊工具迭代轮数，避免「6 库 × 每库 10 轮」工具循环爆炸（实测 120s+ 无 done）
            kbs_effective = kbs or []
            per_kb_iterations = max(1, int(agent_cfg.max_iterations or 10) // max(1, len(kbs_effective)))
            for idx, kb in enumerate(kbs_effective):
                last_kb_id = kb.id
                extra = attachment_ctx if idx == 0 else ""
                # 知识库工具（对齐 WeKnora 全套：RAG/图谱/Wiki 读写/数据分析；按 kb 绑定）
                kb_tools: list[dict] = []
                kb_executor = None
                try:
                    from api.services.agent_tools_full import build_full_tool_context

                    kb_tools, kb_executor = build_full_tool_context(
                        db,
                        agent,
                        kb=kb,
                        caller_user_id=ctx_user_id,
                        caller_team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                    )
                except Exception:  # noqa: BLE001 — 工具装配失败不阻断问答
                    logger.exception("agent full tools resolution failed; tools disabled for this call")
                    kb_tools, kb_executor = [], None
                # allowed_tools 白名单裁剪（非空仅注入白名单内工具；空=全部允许）
                per_kb_tools = filter_by_whitelist((merged_tools or []) + kb_tools)

                def per_kb_executor(tool_call: dict) -> str:
                    name = str(tool_call.get("name") or "")
                    from api.services.agent_tools_full import EXECUTOR_NAMES

                    # 动态工具（web_search / run_skill_script）执行器在 merged_executor，
                    # 不能走 kb_executor（其 executors 仅含目录工具）
                    if name in ("web_search", "run_skill_script") and merged_executor is not None:
                        return merged_executor(tool_call)
                    if name in EXECUTOR_NAMES and kb_executor is not None:
                        return kb_executor(tool_call)
                    if merged_executor is not None:
                        return merged_executor(tool_call)
                    return f"工具不可用: 未配置执行器（{name or '?'}）"

                for event in answer_question(
                    kb_db=db,
                    kb=kb,
                    question=req.question,
                    query_embedding=query_embedding,
                    retrieval_overrides=overrides,
                    history=history,
                    chat_cfg=requested_cfg if req.model_id else None,  # 指定模型时复用解析结果
                    extra_context=extra,
                    image_urls=image_urls if idx == 0 else None,
                    agent_prompt=agent_prompt or "",
                    user_id=ctx_user_id,
                    team_name=ctx_team_name,
                    is_sys_admin=ctx_is_admin,
                    tools=per_kb_tools or None,
                    tool_executor=per_kb_executor,
                    max_tool_iterations=per_kb_iterations,
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
            assistant_message_id = ""
            if req.session_id and user_id:
                try:
                    from api.services.chat_sessions import append_message

                    saved = append_message(
                        db, user_id, req.session_id, "assistant", "".join(answer_parts), refs=refs
                    )
                    assistant_message_id = saved.get("id") or ""
                except Exception as e:  # noqa: BLE001
                    logger.warning("failed to persist agent assistant message: %s", e)
            # 2026-10-14 done 事件带回持久化消息 id：前端据此把本地占位 id 换成服务端 id，
            # 否则点赞/反馈按 id 查库会 404「消息不存在」。
            yield _sse(
                {
                    "type": "done",
                    "message_id": assistant_message_id,
                    "user_message_id": user_message_id,
                }
            )

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
