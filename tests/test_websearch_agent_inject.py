"""Agent 联网搜索工具注入测试（P1 消费点正反例）。

覆盖 agent_websearch_tool_context：启用/未启用/无 provider/可见性/
executor 派发（未授权工具、缺参数、正常搜索）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.websearch import WebSearchProvider
from api.services.agents import AgentConfig
from api.services.websearch import (
    WEB_SEARCH_TOOL,
    agent_websearch_tool_context,
    build_websearch_tool,
)


@pytest.fixture()
def ws_db(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def _fake_agent(config: dict) -> object:
    class _A:
        def __init__(self, cfg):
            self.config = cfg

    return _A(config)


def test_disabled_returns_empty(ws_db) -> None:
    tools, ex = agent_websearch_tool_context(ws_db, _fake_agent({"web_search_enabled": False}))
    assert tools == []
    assert ex is None


def test_enabled_without_provider_returns_empty(ws_db) -> None:
    # 无任何 provider → 不注入（不炸）
    tools, ex = agent_websearch_tool_context(ws_db, _fake_agent({"web_search_enabled": True}))
    assert tools == []
    assert ex is None


def test_enabled_with_system_provider_injects(ws_db) -> None:
    ws_db.add(
        WebSearchProvider(
            id="p1",
            name="tavily-sys",
            provider_type="tavily",
            api_key="k",
            scope="system",
            state="1",
        )
    )
    ws_db.commit()
    tools, ex = agent_websearch_tool_context(
        ws_db,
        _fake_agent({"web_search_enabled": True, "web_search_max_results": 3}),
        caller_user_id="huqiang",
        caller_team_name="默认团队",
        is_sys_admin=True,
    )
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == WEB_SEARCH_TOOL
    assert ex is not None
    # 未授权工具调用被拒
    assert "未授权的工具调用" in ex({"name": "run_skill_script", "arguments": "{}"})
    # 缺 query 参数
    assert "缺少 query" in ex({"name": WEB_SEARCH_TOOL, "arguments": "{}"})


def test_other_user_cannot_see_personal_provider(ws_db) -> None:
    ws_db.add(
        WebSearchProvider(
            id="p2",
            name="mine",
            provider_type="tavily",
            api_key="k",
            scope="personal",
            owner_user_id="someone-else",
            state="1",
        )
    )
    ws_db.commit()
    # 显式指定他人 personal provider → 不可见 → 不注入
    tools, ex = agent_websearch_tool_context(
        ws_db,
        _fake_agent({"web_search_enabled": True, "web_search_provider_id": "p2"}),
        caller_user_id="huqiang",
        caller_team_name="默认团队",
        is_sys_admin=False,
    )
    assert tools == []
    assert ex is None


def test_executor_visible_provider_search_path(ws_db, monkeypatch) -> None:
    ws_db.add(
        WebSearchProvider(
            id="p3",
            name="sys-serper",
            provider_type="serper",
            api_key="k",
            scope="system",
            state="1",
        )
    )
    ws_db.commit()

    captured = {}

    def fake_search(db, provider_id, query, max_results=5, **kw):
        captured.update({"provider_id": provider_id, "query": query, "max": max_results})
        return [{"title": "t", "url": "u", "snippet": "s"}]

    monkeypatch.setattr("api.services.websearch.search", fake_search)
    tools, ex = agent_websearch_tool_context(
        ws_db,
        _fake_agent({"web_search_enabled": True, "web_search_provider_id": "p3"}),
        caller_user_id="huqiang",
        caller_team_name="默认团队",
        is_sys_admin=True,
    )
    assert ex is not None
    out = ex({"name": WEB_SEARCH_TOOL, "arguments": '{"query": "测试"}'})
    assert captured["provider_id"] == "p3"
    assert captured["query"] == "测试"
    assert "u" in out and "t" in out


def test_build_tool_shape() -> None:
    t = build_websearch_tool(5)[0]
    assert t["type"] == "function"
    assert "query" in t["function"]["parameters"]["required"]
