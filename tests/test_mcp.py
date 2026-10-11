"""MCP server tests — tool registration + handler logic + visibility (RBAC)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.mcp_server import (
    create_mcp_server,
    handle_kb_answer,
    handle_kb_list,
    handle_kb_search,
    resolve_caller,
)
from api.models.chat_session import ChatSession
from api.models.knowledge import KbDatasource


@pytest.fixture()
def mcp_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            # team=T1 owned-by alice (personal scope)
            KbDatasource(id="kb1", name="制度库", team_name="T1", state="1",
                         owner_user_id="alice", owner_team_name="T1", scope="personal"),
            # team=T2 (team scope)
            KbDatasource(id="kb2", name="技术库", team_name="T2", state="1",
                         owner_user_id="bob", owner_team_name="T2", scope="team"),
            # system scope (platform preset, visible to everyone)
            KbDatasource(id="kb3", name="公共库", team_name="ROOT", state="1",
                         owner_user_id="", owner_team_name="", scope="system"),
            # soft-deleted (state=0) — never visible
            KbDatasource(id="kb4", name="已删库", team_name="T1", state="0",
                         owner_user_id="alice", owner_team_name="T1", scope="personal"),
            # alice's chat session (for sessionId -> user resolution)
            ChatSession(id="sess-alice", user_id="alice", kb_id="kb1", title="alice 会话"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_mcp_tools_registered() -> None:
    tools = asyncio.run(create_mcp_server().list_tools())
    names = {t.name for t in tools}
    assert {"kb_list", "kb_search", "kb_answer"} <= names


# ---- sessionId -> caller resolution -------------------------------------


def test_resolve_caller_valid_session(mcp_db) -> None:
    caller = resolve_caller(mcp_db, "sess-alice")
    assert caller is not None
    assert caller["user_id"] == "alice"


def test_resolve_caller_missing_or_unknown(mcp_db) -> None:
    assert resolve_caller(mcp_db, "") is None
    assert resolve_caller(mcp_db, "   ") is None
    assert resolve_caller(mcp_db, "sess-nobody") is None


# ---- kb_list visibility -------------------------------------------------


def test_handle_kb_list_owner_sees_own_plus_system(mcp_db) -> None:
    # alice: personal kb1 (owner) + system kb3; NOT bob's team kb2
    result = handle_kb_list(mcp_db, caller={"user_id": "alice", "team_name": "T1", "is_admin": False})
    assert result["success"] is True
    assert {kb["id"] for kb in result["items"]} == {"kb1", "kb3"}


def test_handle_kb_list_team_member_sees_team_plus_system(mcp_db) -> None:
    # bob: team kb2 + system kb3
    result = handle_kb_list(mcp_db, caller={"user_id": "bob", "team_name": "T2", "is_admin": False})
    assert {kb["id"] for kb in result["items"]} == {"kb2", "kb3"}


def test_handle_kb_list_admin_no_extra_visibility(mcp_db) -> None:
    # 平台 visible_clauses 对列表不过滤 is_admin：admin 同样只看到
    # own/team/system——root 不拥有任何库、team ROOT 无匹配，只见 system kb3
    result = handle_kb_list(mcp_db, caller={"user_id": "root", "team_name": "ROOT", "is_admin": True})
    assert {kb["id"] for kb in result["items"]} == {"kb3"}


def test_handle_kb_list_no_caller_all_active(mcp_db) -> None:
    # no caller = legacy behaviour (testing only); state=0 still excluded
    result = handle_kb_list(mcp_db)
    assert {kb["id"] for kb in result["items"]} == {"kb1", "kb2", "kb3"}


# ---- kb_search visibility ------------------------------------------------


def test_handle_kb_search_visible(mcp_db) -> None:
    caller = {"user_id": "alice", "team_name": "T1", "is_admin": False}
    result = handle_kb_search(mcp_db, "kb1", "任意查询", caller=caller)
    assert result["success"] is True  # empty KB → items []
    assert result["items"] == []


def test_handle_kb_search_not_visible_404(mcp_db) -> None:
    # alice cannot see bob's team kb2
    caller = {"user_id": "alice", "team_name": "T1", "is_admin": False}
    result = handle_kb_search(mcp_db, "kb2", "任意查询", caller=caller)
    assert result["success"] is False
    assert "not found or not visible" in result["error"]


def test_handle_kb_search_deleted_404(mcp_db) -> None:
    result = handle_kb_search(mcp_db, "kb4", "x", caller={"user_id": "alice", "team_name": "T1", "is_admin": False})
    assert result["success"] is False


# ---- kb_answer visibility ------------------------------------------------


def test_handle_kb_answer_visible_kb_no_llm_crash(mcp_db) -> None:
    caller = {"user_id": "alice", "team_name": "T1", "is_admin": False}
    # empty KB → hybrid_search returns [] and build_messages handles empty hits;
    # ChatClient only runs when hits exist, so no LLM call here.
    result = handle_kb_answer(mcp_db, "kb1", "问题", caller=caller)
    assert result["success"] is True


def test_handle_kb_answer_not_visible_404(mcp_db) -> None:
    caller = {"user_id": "alice", "team_name": "T1", "is_admin": False}
    result = handle_kb_answer(mcp_db, "kb2", "问题", caller=caller)
    assert result["success"] is False
    assert "not found or not visible" in result["error"]


def test_mcp_server_instantiable() -> None:
    server = create_mcp_server()
    assert server.name == "kb-tools"
