"""MCP server tests — tool registration + tool handler logic."""

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
)
from api.models.knowledge import KbDatasource


@pytest.fixture()
def mcp_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            KbDatasource(id="kb1", name="制度库", team_name="T1", state="1"),
            KbDatasource(id="kb2", name="技术库", team_name="T2", state="1"),
            KbDatasource(id="kb3", name="已删库", team_name="T1", state="0"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_mcp_tools_registered() -> None:
    tools = asyncio.run(create_mcp_server().list_tools())
    names = {t.name for t in tools}
    assert {"kb_list", "kb_search", "kb_answer"} <= names


def test_handle_kb_list_all(mcp_db) -> None:
    result = handle_kb_list(mcp_db)
    assert result["success"] is True
    # state=0 excluded
    assert {kb["id"] for kb in result["items"]} == {"kb1", "kb2"}


def test_handle_kb_list_filter_team(mcp_db) -> None:
    result = handle_kb_list(mcp_db, team_name="T1")
    assert {kb["id"] for kb in result["items"]} == {"kb1"}


def test_handle_kb_search_missing_kb(mcp_db) -> None:
    result = handle_kb_search(mcp_db, kb_id="nope", query="x")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_handle_kb_search_existing_kb_returns_items(mcp_db) -> None:
    # empty KB -> success with empty items (no LLM needed)
    result = handle_kb_search(mcp_db, kb_id="kb1", query="任意查询")
    assert result["success"] is True
    assert result["items"] == []


def test_handle_kb_answer_missing_kb(mcp_db) -> None:
    result = handle_kb_answer(mcp_db, kb_id="nope", question="问题")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_mcp_server_instantiable() -> None:
    server = create_mcp_server()
    assert server.name == "kb-tools"
