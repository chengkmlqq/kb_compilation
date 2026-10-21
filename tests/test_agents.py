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


# ── 2026-10-20 对齐 WeKnora：有效系统提示词「非空即用」 ─────────────────


def _agent(**cfg_kwargs) -> KbAgent:
    return KbAgent(id="a1", name="测试", team_name="T1", config=cfg_kwargs or None)


def test_effective_system_prompt_prefers_custom() -> None:
    from api.services.agents import effective_system_prompt

    agent = _agent(system_prompt="你是制度专家。")
    assert effective_system_prompt(agent, default="默认提示词") == "你是制度专家。"


def test_effective_system_prompt_falls_back_to_default() -> None:
    from api.services.agents import effective_system_prompt

    agent = _agent(system_prompt="   ")
    assert effective_system_prompt(agent, default="默认提示词") == "默认提示词"


def test_effective_system_prompt_ignores_switch_flag() -> None:
    """对齐 WeKnora：use_custom_system_prompt 开关不参与判断，非空即用。
    旧配置开关为 False 但填了内容时依然生效（避免语义陷阱）。"""
    from api.services.agents import effective_system_prompt

    agent = _agent(use_custom_system_prompt=False, system_prompt="填了就用。")
    assert effective_system_prompt(agent, default="默认提示词") == "填了就用。"


def test_agents_prompt_defaults_route() -> None:
    """GET /agents/prompt-defaults 返回平台默认提示词（新建/编辑展示用）。"""
    from api.routers.agents import prompt_defaults_route
    from api.services.chat import SYSTEM_PROMPT

    body = prompt_defaults_route()  # 端点无 DB/鉴权依赖，直接调 handler 验证
    assert body["success"] is True
    assert body["data"]["system_prompt"] == SYSTEM_PROMPT
    assert "contexts" in body["data"]["placeholders"]


def test_agents_prompt_templates_route() -> None:
    """GET /agents/prompt-templates 返回模板库（2026-10-21 对齐 WeKnora）。

    校验加载器形状与默认标记语义：system_prompt/context_template 两类、
    每个模板含 id/name/description/content、有且仅有一个 default 模板。
    """
    from api.routers.agents import prompt_templates_route

    body = prompt_templates_route()
    assert body["success"] is True
    data = body["data"]
    assert set(data.keys()) == {"system_prompt", "context_template"}
    for key in ("system_prompt", "context_template"):
        templates = data[key]
        assert len(templates) >= 1, key
        for t in templates:
            assert t["id"] and t["name"] and t["description"] and t["content"]
            assert "i18n" not in t  # 已本地化剥离
        defaults = [t for t in templates if t.get("default")]
        assert len(defaults) == 1, key


def test_prompt_templates_localized_zh_cn() -> None:
    """i18n.zh-CN 的名称/描述优先生效（对齐 WeKnora LocalizeTemplates）。"""
    from api.services.prompt_templates import get_prompt_templates

    templates = get_prompt_templates()["system_prompt"]
    kb_default = next(t for t in templates if t.get("default"))
    assert kb_default["name"] == "知识库问答助手"  # zh-CN 覆盖英文名
    assert "{{contexts}}" in kb_default["content"]  # 正文保持模板占位符


def test_prompt_templates_default_template_fallback() -> None:
    """无 default 标记的列表取第一个（对齐 WeKnora DefaultTemplate）。"""
    from api.services.prompt_templates import get_default_template

    lst = [{"id": "a"}, {"id": "b", "default": True}]
    assert get_default_template(lst) is not None
    assert get_default_template(lst)["id"] == "b"  # type: ignore[index]
    assert get_default_template([{"id": "x"}]) is not None
    assert get_default_template([{"id": "x"}])["id"] == "x"  # type: ignore[index]
    assert get_default_template([]) is None


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
    # 2026-10-08 fix(agent) 键映射修正：编辑器存 embedding_top_k / vector_threshold，
    # resolve_agent_qa_overrides 读的就是这两个键（原用例用旧键 top_k/threshold，
    # 与编辑器实际写入不符，读不到即回落默认值）。
    agent = create_agent(agent_db, "助手", team_name="T1", created_by="admin")
    update_agent(agent_db, agent.id, {"config": {"embedding_top_k": 3, "vector_threshold": 0.5}})
    agent = agent_db.get(KbAgent, agent.id)
    overrides = resolve_agent_qa_overrides(agent)
    assert overrides["top_k"] == 3
    assert overrides["threshold"] == 0.5


def test_agent_qa_request_accepts_model_id() -> None:
    """2026-10-12: 前端在 agent 模式也发送 model_id，但 AgentQARequest 此前
    没有该字段 → pydantic 静默丢弃，「选了模型不生效」。断言字段存在且透传。"""
    from api.routers.agents import AgentQARequest

    req = AgentQARequest(question="你好", model_id="m-abc")
    assert req.model_id == "m-abc"
    # 不传时默认 None（用默认模型），保持旧行为
    assert AgentQARequest(question="你好").model_id is None
