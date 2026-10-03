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
    """Mirrors WeKnora CustomAgentConfig (kept as a typed view over JSON)."""

    agent_mode: str = AGENT_MODE_QUICK_ANSWER
    system_prompt: str = ""
    model_id: str = ""
    temperature: float = 0.7
    max_completion_tokens: int = 2048
    citation_enabled: bool = True
    kb_selection_mode: str = KB_SELECTION_ALL
    knowledge_bases: list[str] = field(default_factory=list)
    retrieve_kb_only_when_mentioned: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    mcp_selection_mode: str = "none"
    mcp_services: list[str] = field(default_factory=list)
    top_k: int = 5
    threshold: float = 0.2
    embed_query: bool = True
    # 图片问答门禁（对齐 WeKnora CustomAgentConfig.ImageUploadEnabled）：
    # 仅 agent 会话且开启时允许上传图片，图片随 QA 挂到 user 消息 images
    image_upload_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "agent_mode": self.agent_mode,
            "system_prompt": self.system_prompt,
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
            "citation_enabled": self.citation_enabled,
            "kb_selection_mode": self.kb_selection_mode,
            "knowledge_bases": list(self.knowledge_bases),
            "retrieve_kb_only_when_mentioned": self.retrieve_kb_only_when_mentioned,
            "allowed_tools": list(self.allowed_tools),
            "mcp_selection_mode": self.mcp_selection_mode,
            "mcp_services": list(self.mcp_services),
            "top_k": self.top_k,
            "threshold": self.threshold,
            "embed_query": self.embed_query,
            "image_upload_enabled": self.image_upload_enabled,
        }


def config_from_dict(data: dict | None) -> AgentConfig:
    """Build AgentConfig from a (possibly partial) JSON dict."""
    data = data or {}
    allowed = AgentConfig()
    return AgentConfig(
        agent_mode=str(data.get("agent_mode") or allowed.agent_mode),
        system_prompt=str(data.get("system_prompt") or ""),
        model_id=str(data.get("model_id") or ""),
        temperature=float(data.get("temperature") or allowed.temperature),
        max_completion_tokens=int(data.get("max_completion_tokens") or allowed.max_completion_tokens),
        citation_enabled=bool(data.get("citation_enabled", allowed.citation_enabled)),
        kb_selection_mode=str(data.get("kb_selection_mode") or allowed.kb_selection_mode),
        knowledge_bases=list(data.get("knowledge_bases") or []),
        retrieve_kb_only_when_mentioned=bool(data.get("retrieve_kb_only_when_mentioned") or False),
        allowed_tools=list(data.get("allowed_tools") or []),
        mcp_selection_mode=str(data.get("mcp_selection_mode") or "none"),
        mcp_services=list(data.get("mcp_services") or []),
        top_k=int(data.get("top_k") or allowed.top_k),
        threshold=float(data.get("threshold") or allowed.threshold),
        embed_query=bool(data.get("embed_query", allowed.embed_query)),
        image_upload_enabled=bool(data.get("image_upload_enabled") or False),
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
    rows = db.execute(stmt.order_by(KbAgent.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
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
