"""Agent config service tests — defaults, merge, CRUD, KB scope."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import KbAgent, KbDatasource
from api.services.agents import (
    AgentConfig,
    config_from_dict,
    create_agent,
    delete_agent,
    list_agents,
    resolve_agent_qa_overrides,
    resolve_kb_scope,
    update_agent,
)


def test_config_defaults() -> None:
    cfg = config_from_dict(None)
    assert cfg.agent_mode == "quick-answer"
    assert cfg.kb_selection_mode == "all"
    assert cfg.temperature == 0.7
    assert cfg.top_k == 5
    assert cfg.citation_enabled is True


def test_config_partial_merge_keeps_defaults() -> None:
    cfg = config_from_dict({"temperature": 0.2, "knowledge_bases": ["kb1"]})
    assert cfg.temperature == 0.2
    assert cfg.knowledge_bases == ["kb1"]
    assert cfg.agent_mode == "quick-answer"  # untouched default
    assert cfg.top_k == 5


def test_config_to_dict_roundtrip() -> None:
    cfg = config_from_dict({"top_k": 8, "threshold": 0.3, "agent_mode": "smart-reasoning"})
    cfg2 = config_from_dict(cfg.to_dict())
    assert cfg2.top_k == 8
    assert cfg2.threshold == 0.3
    assert cfg2.agent_mode == "smart-reasoning"


@pytest.fixture()
def agent_db():
    engine = create_engine("sqlite:///:memory:")
    # Business tables live on the framework base (portable relational types).
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            KbDatasource(id="kb1", name="制度库", team_name="T1", state="1"),
            KbDatasource(id="kb2", name="技术库", team_name="T1", state="1"),
            KbDatasource(id="kb3", name="其他团队库", team_name="T2", state="1"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_create_and_list(agent_db) -> None:
    agent = create_agent(agent_db, "制度助手", team_name="T1", created_by="admin", description="问答制度")
    items = list_agents(agent_db, team_name="T1")["items"]
    assert len(items) == 1
    assert items[0]["name"] == "制度助手"
    assert items[0]["config"]["agent_mode"] == "quick-answer"


def test_update_merges_config(agent_db) -> None:
    agent = create_agent(agent_db, "助手", team_name="T1", created_by="admin")
    updated = update_agent(agent_db, agent.id, {"config": {"top_k": 10}})
    assert updated is not None
    cfg = config_from_dict(updated.config)
    assert cfg.top_k == 10
    assert cfg.agent_mode == "quick-answer"  # preserved


def test_delete_soft(agent_db) -> None:
    agent = create_agent(agent_db, "临时", team_name="T1", created_by="admin")
    assert delete_agent(agent_db, agent.id) is True
    assert list_agents(agent_db, team_name="T1")["total"] == 0
    # hard row still exists (soft delete)
    assert agent_db.get(KbAgent, agent.id) is not None


def test_resolve_kb_scope_all_for_team(agent_db) -> None:
    agent = create_agent(agent_db, "助手", team_name="T1", created_by="admin")
    kbs = resolve_kb_scope(agent_db, agent)
    assert {kb.id for kb in kbs} == {"kb1", "kb2"}  # team T1 only


def test_resolve_kb_scope_selected(agent_db) -> None:
    agent = create_agent(agent_db, "助手", team_name="T1", created_by="admin")
    update_agent(agent_db, agent.id, {"config": {"kb_selection_mode": "selected", "knowledge_bases": ["kb2"]}})
    agent = agent_db.get(KbAgent, agent.id)
    kbs = resolve_kb_scope(agent_db, agent)
    assert {kb.id for kb in kbs} == {"kb2"}


def test_resolve_kb_scope_none(agent_db) -> None:
    agent = create_agent(agent_db, "助手", team_name="T1", created_by="admin")
    update_agent(agent_db, agent.id, {"config": {"kb_selection_mode": "none"}})
    agent = agent_db.get(KbAgent, agent.id)
    assert resolve_kb_scope(agent_db, agent) == []


def test_resolve_qa_overrides(agent_db) -> None:
    agent = create_agent(agent_db, "助手", team_name="T1", created_by="admin")
    update_agent(agent_db, agent.id, {"config": {"top_k": 3, "threshold": 0.5}})
    agent = agent_db.get(KbAgent, agent.id)
    overrides = resolve_agent_qa_overrides(agent)
    assert overrides["top_k"] == 3
    assert overrides["threshold"] == 0.5
