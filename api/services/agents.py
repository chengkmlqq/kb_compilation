"""Agent (QA assistant) configuration service.

Agent = a named assistant with a config (mirroring WeKnora CustomAgentConfig)
that determines how it answers: which KBs it retrieves from, which model and
prompt it uses, and which extra capabilities (tools / MCP / skills) it can
call. This service owns:

- default config (so the frontend gets sensible quick-answer behaviour)
- CRUD on kb_agent
- resolving an agent's effective config at runtime, which the QA router uses
  to build retrieval overrides + the chat system prompt
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models.knowledge import KbAgent, KbDatasource
from api.services.chat import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

AGENT_MODE_QUICK_ANSWER = "quick-answer"
AGENT_MODE_SMART_REASONING = "smart-reasoning"

KB_SELECTION_ALL = "all"
KB_SELECTION_SELECTED = "selected"
KB_SELECTION_NONE = "none"


@dataclass
class AgentConfig:
    """Mirrors WeKnora CustomAgentConfig (kept as a typed view over JSON).

    P0 全字段对齐（2026-10-04）：覆盖 WeKnora AgentEditorModal 13 分区全部
    配置项（基础/提示词/模型/对话/建议/知识库/检索/联网/多模态/工具/MCP/技能）。
    """

    agent_mode: str = AGENT_MODE_QUICK_ANSWER
    system_prompt: str = ""
    context_template: str = ""
    use_custom_system_prompt: bool = False
    system_prompt_id: str = ""
    agent_type: str = "hybrid-rag-wiki"
    model_id: str = ""
    rerank_model_id: str = ""
    temperature: float = 0.7
    max_completion_tokens: int = 2048
    thinking: bool = False
    citation_enabled: bool = True
    max_iterations: int = 10
    llm_call_timeout: int = 120
    reflection_enabled: bool = False
    multi_turn_enabled: bool = False
    history_turns: int = 5
    kb_selection_mode: str = KB_SELECTION_ALL
    knowledge_bases: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)
    retrieve_kb_only_when_mentioned: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    mcp_selection_mode: str = "none"
    mcp_services: list[str] = field(default_factory=list)
    mcp_auth_wait_timeout: int = 600
    skills_enabled: bool = False
    skills_selection_mode: str = "none"
    selected_skills: list[str] = field(default_factory=list)
    top_k: int = 5
    threshold: float = 0.2
    embed_query: bool = True
    # 检索细化（WeKnora retrieval 分区）
    embedding_top_k: int = 10
    keyword_threshold: float = 0.3
    vector_threshold: float = 0.5
    rerank_top_k: int = 5
    rerank_threshold: float = 0.5
    enable_query_expansion: bool = True
    enable_rewrite: bool = True
    query_understand_model_id: str = ""
    rewrite_prompt_system: str = ""
    rewrite_prompt_user: str = ""
    fallback_strategy: str = "model"
    fallback_response: str = ""
    fallback_prompt: str = ""
    # 联网搜索（P1：websearch provider 注册表）
    web_search_enabled: bool = False
    web_search_max_results: int = 5
    web_search_provider_id: str = ""
    # 图片问答门禁（对齐 WeKnora CustomAgentConfig.ImageUploadEnabled）：
    # 仅 agent 会话且开启时允许上传图片，图片随 QA 挂到 user 消息 images
    image_upload_enabled: bool = False
    vlm_model_id: str = ""
    image_storage_provider: str = ""
    attachment_image_understanding: bool = False
    attachment_ocr_max_pages: int = 0
    attachment_parse_wait_timeout_sec: int = 0
    supported_file_types: list[str] = field(default_factory=list)
    chat_parser_engine_rules: list[dict] = field(default_factory=list)
    data_analysis_enabled: bool = False
    faq_priority_enabled: bool = True
    faq_direct_answer_threshold: float = 0.9
    faq_score_boost: float = 1.2
    question_suggestions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_mode": self.agent_mode,
            "system_prompt": self.system_prompt,
            "context_template": self.context_template,
            "use_custom_system_prompt": self.use_custom_system_prompt,
            "system_prompt_id": self.system_prompt_id,
            "agent_type": self.agent_type,
            "model_id": self.model_id,
            "rerank_model_id": self.rerank_model_id,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
            "thinking": self.thinking,
            "citation_enabled": self.citation_enabled,
            "max_iterations": self.max_iterations,
            "llm_call_timeout": self.llm_call_timeout,
            "reflection_enabled": self.reflection_enabled,
            "multi_turn_enabled": self.multi_turn_enabled,
            "history_turns": self.history_turns,
            "kb_selection_mode": self.kb_selection_mode,
            "knowledge_bases": list(self.knowledge_bases),
            "knowledge_ids": list(self.knowledge_ids),
            "retrieve_kb_only_when_mentioned": self.retrieve_kb_only_when_mentioned,
            "allowed_tools": list(self.allowed_tools),
            "mcp_selection_mode": self.mcp_selection_mode,
            "mcp_services": list(self.mcp_services),
            "mcp_auth_wait_timeout": self.mcp_auth_wait_timeout,
            "skills_enabled": self.skills_enabled,
            "skills_selection_mode": self.skills_selection_mode,
            "selected_skills": list(self.selected_skills),
            "top_k": self.top_k,
            "threshold": self.threshold,
            "embed_query": self.embed_query,
            "embedding_top_k": self.embedding_top_k,
            "keyword_threshold": self.keyword_threshold,
            "vector_threshold": self.vector_threshold,
            "rerank_top_k": self.rerank_top_k,
            "rerank_threshold": self.rerank_threshold,
            "enable_query_expansion": self.enable_query_expansion,
            "enable_rewrite": self.enable_rewrite,
            "query_understand_model_id": self.query_understand_model_id,
            "rewrite_prompt_system": self.rewrite_prompt_system,
            "rewrite_prompt_user": self.rewrite_prompt_user,
            "fallback_strategy": self.fallback_strategy,
            "fallback_response": self.fallback_response,
            "fallback_prompt": self.fallback_prompt,
            "web_search_enabled": self.web_search_enabled,
            "web_search_max_results": self.web_search_max_results,
            "web_search_provider_id": self.web_search_provider_id,
            "image_upload_enabled": self.image_upload_enabled,
            "vlm_model_id": self.vlm_model_id,
            "image_storage_provider": self.image_storage_provider,
            "attachment_image_understanding": self.attachment_image_understanding,
            "attachment_ocr_max_pages": self.attachment_ocr_max_pages,
            "attachment_parse_wait_timeout_sec": self.attachment_parse_wait_timeout_sec,
            "supported_file_types": list(self.supported_file_types),
            "chat_parser_engine_rules": list(self.chat_parser_engine_rules),
            "data_analysis_enabled": self.data_analysis_enabled,
            "faq_priority_enabled": self.faq_priority_enabled,
            "faq_direct_answer_threshold": self.faq_direct_answer_threshold,
            "faq_score_boost": self.faq_score_boost,
            "question_suggestions": dict(self.question_suggestions),
        }


def _as_bool(v, default: bool = False) -> bool:
    return bool(v) if isinstance(v, bool) else default


def config_from_dict(data: dict | None) -> AgentConfig:
    """Build AgentConfig from a (possibly partial) JSON dict."""
    data = data or {}
    allowed = AgentConfig()
    return AgentConfig(
        agent_mode=str(data.get("agent_mode") or allowed.agent_mode),
        system_prompt=str(data.get("system_prompt") or ""),
        context_template=str(data.get("context_template") or ""),
        use_custom_system_prompt=_as_bool(data.get("use_custom_system_prompt")),
        system_prompt_id=str(data.get("system_prompt_id") or ""),
        agent_type=str(data.get("agent_type") or "hybrid-rag-wiki"),
        model_id=str(data.get("model_id") or ""),
        rerank_model_id=str(data.get("rerank_model_id") or ""),
        temperature=float(data.get("temperature") or allowed.temperature),
        max_completion_tokens=int(data.get("max_completion_tokens") or allowed.max_completion_tokens),
        thinking=_as_bool(data.get("thinking")),
        citation_enabled=bool(data.get("citation_enabled", allowed.citation_enabled)),
        max_iterations=int(data.get("max_iterations") or allowed.max_iterations),
        llm_call_timeout=int(data.get("llm_call_timeout") or allowed.llm_call_timeout),
        reflection_enabled=_as_bool(data.get("reflection_enabled")),
        multi_turn_enabled=_as_bool(data.get("multi_turn_enabled")),
        history_turns=int(data.get("history_turns") or allowed.history_turns),
        kb_selection_mode=str(data.get("kb_selection_mode") or allowed.kb_selection_mode),
        knowledge_bases=list(data.get("knowledge_bases") or []),
        knowledge_ids=list(data.get("knowledge_ids") or []),
        retrieve_kb_only_when_mentioned=_as_bool(data.get("retrieve_kb_only_when_mentioned")),
        allowed_tools=list(data.get("allowed_tools") or []),
        mcp_selection_mode=str(data.get("mcp_selection_mode") or "none"),
        mcp_services=list(data.get("mcp_services") or []),
        mcp_auth_wait_timeout=int(data.get("mcp_auth_wait_timeout") or 600),
        skills_enabled=_as_bool(data.get("skills_enabled")),
        skills_selection_mode=str(data.get("skills_selection_mode") or "none"),
        selected_skills=list(data.get("selected_skills") or []),
        top_k=int(data.get("top_k") or allowed.top_k),
        threshold=float(data.get("threshold") or allowed.threshold),
        embed_query=bool(data.get("embed_query", allowed.embed_query)),
        embedding_top_k=int(data.get("embedding_top_k") or 10),
        keyword_threshold=float(data.get("keyword_threshold") or 0.3),
        vector_threshold=float(data.get("vector_threshold") or 0.5),
        rerank_top_k=int(data.get("rerank_top_k") or 5),
        rerank_threshold=float(data.get("rerank_threshold") or 0.5),
        enable_query_expansion=bool(data.get("enable_query_expansion", True)),
        enable_rewrite=bool(data.get("enable_rewrite", True)),
        query_understand_model_id=str(data.get("query_understand_model_id") or ""),
        rewrite_prompt_system=str(data.get("rewrite_prompt_system") or ""),
        rewrite_prompt_user=str(data.get("rewrite_prompt_user") or ""),
        fallback_strategy=str(data.get("fallback_strategy") or "model"),
        fallback_response=str(data.get("fallback_response") or ""),
        fallback_prompt=str(data.get("fallback_prompt") or ""),
        web_search_enabled=_as_bool(data.get("web_search_enabled")),
        web_search_max_results=int(data.get("web_search_max_results") or 5),
        web_search_provider_id=str(data.get("web_search_provider_id") or ""),
        image_upload_enabled=_as_bool(data.get("image_upload_enabled")),
        vlm_model_id=str(data.get("vlm_model_id") or ""),
        image_storage_provider=str(data.get("image_storage_provider") or ""),
        attachment_image_understanding=_as_bool(data.get("attachment_image_understanding")),
        attachment_ocr_max_pages=int(data.get("attachment_ocr_max_pages") or 0),
        attachment_parse_wait_timeout_sec=int(data.get("attachment_parse_wait_timeout_sec") or 0),
        supported_file_types=list(data.get("supported_file_types") or []),
        chat_parser_engine_rules=list(data.get("chat_parser_engine_rules") or []),
        data_analysis_enabled=_as_bool(data.get("data_analysis_enabled")),
        faq_priority_enabled=bool(data.get("faq_priority_enabled", True)),
        faq_direct_answer_threshold=float(data.get("faq_direct_answer_threshold") or 0.9),
        faq_score_boost=float(data.get("faq_score_boost") or 1.2),
        question_suggestions=dict(data.get("question_suggestions") or {}),
    )


def create_agent(
    db: Session,
    name: str,
    team_name: str | None,
    created_by: str | None,
    description: str | None = None,
    avatar: str | None = None,
    config: dict | None = None,
) -> KbAgent:
    agent = KbAgent(
        id=uuid.uuid4().hex[:36],
        name=name,
        description=description,
        avatar=avatar,
        team_name=team_name,
        created_by=created_by,
        config=config_from_dict(config).to_dict(),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def update_agent(db: Session, agent_id: str, fields: dict) -> KbAgent | None:
    agent = db.execute(select(KbAgent).where(KbAgent.id == agent_id)).scalars().first()
    if not agent:
        return None
    if "name" in fields:
        agent.name = str(fields["name"])
    if "description" in fields:
        agent.description = str(fields.get("description") or "")
    if "avatar" in fields:
        agent.avatar = str(fields.get("avatar") or "")
    if "config" in fields and isinstance(fields["config"], dict):
        merged = config_from_dict(agent.config or {}).to_dict()
        merged.update(fields["config"])
        agent.config = merged
    db.commit()
    db.refresh(agent)
    return agent


def delete_agent(db: Session, agent_id: str) -> bool:
    agent = db.execute(select(KbAgent).where(KbAgent.id == agent_id)).scalars().first()
    if not agent:
        return False
    agent.state = "0"
    db.commit()
    return True


def list_agents(db: Session, team_name: str | None = None, page: int = 1, page_size: int = 10) -> dict:
    stmt = select(KbAgent).where(KbAgent.state == "1")
    if team_name:
        stmt = stmt.where(KbAgent.team_name == team_name)
    total = len(db.execute(stmt).scalars().all())
    rows = db.execute(stmt.order_by(KbAgent.created_at.desc(), KbAgent.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    items = [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "avatar": a.avatar,
            "is_builtin": a.is_builtin,
            "config": a.config,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


# ---------------------------------------------------------------------------
# Runtime resolution: agent -> KB scope + retrieval config + system prompt
# ---------------------------------------------------------------------------


def resolve_kb_scope(db: Session, agent: KbAgent) -> list[KbDatasource]:
    """Which KBs an agent may retrieve from, per kb_selection_mode."""
    cfg = config_from_dict(agent.config or {})
    if cfg.kb_selection_mode == KB_SELECTION_SELECTED:
        kb_ids = cfg.knowledge_bases
        return list(db.execute(select(KbDatasource).where(KbDatasource.id.in_(kb_ids))).scalars().all())
    if cfg.kb_selection_mode == KB_SELECTION_NONE:
        return []
    # all: agent's team KBs (or all if no team scope)
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    if agent.team_name:
        stmt = stmt.where(KbDatasource.team_name == agent.team_name)
    return list(db.execute(stmt).scalars().all())


def resolve_agent_qa_overrides(agent: KbAgent) -> dict:
    """Retrieval overrides from the agent config (top_k / threshold / embed)."""
    cfg = config_from_dict(agent.config or {})
    return {
        "top_k": cfg.top_k,
        "threshold": cfg.threshold,
        "vector_enabled": cfg.embed_query,
    }


def effective_system_prompt(agent: KbAgent, default: str = SYSTEM_PROMPT) -> str:
    """Agent's system prompt, falling back to the default RAG prompt."""
    cfg = config_from_dict(agent.config or {})
    return cfg.system_prompt.strip() or default
