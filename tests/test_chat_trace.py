"""Chat trace service tests — 智能问答过程追踪的收集/落库/查询（2026-10-23）。

Uses an in-memory SQLite engine on the framework Base (same pattern as
test_chat_sessions).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models import chat_session  # noqa: F401  (register tables)
from api.services import chat_trace as svc


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _feed(col: svc.TraceCollector) -> None:
    col.on_event({"type": "stage", "stage": "retrieval", "status": "running"})
    col.on_event(
        {"type": "context", "hits": [
            {"kb_id": "kb1", "chunk_id": "c1", "document_id": "d1", "score": 0.92,
             "content": "X" * 3000, "meta": {"file_name": "a.pdf"}},
            {"kb_id": "kb1", "chunk_id": "c2", "document_id": "d1", "score": 0.71, "content": "y"},
        ]}
    )
    col.on_event({"type": "thinking", "text": "思考段1"})
    col.on_event({"type": "thinking", "text": "思考段2"})
    col.on_event({"type": "tool_call", "tool_calls": [
        {"id": "call_1", "name": "knowledge_search", "arguments": "{\"query\":\"a\"}"},
    ]})
    col.on_event({"type": "tool", "tool_calls": [{"id": "call_1"}], "results": [
        {"id": "call_1", "name": "knowledge_search", "result": "命中 3 条…", "success": True, "error": ""},
    ], "durations": [0.85]})
    col.on_event({"type": "delta", "text": "回答内容"})
    col.on_event({"type": "done"})


def test_collector_event_convergence() -> None:
    col = svc.TraceCollector(
        session_id="s1", user_id="u1", team_name="t1", kb_id="kb1", kind="agent",
        question="问", model="m1",
    )
    _feed(col)
    assert col.thinking == "思考段1思考段2"
    assert len(col.hits) == 2
    assert col.hits[0]["chunk_id"] == "c1"
    # content 截断
    assert "截断" in col.hits[0]["content"]
    types = [e["t"] for e in col.events]
    assert types == ["stage", "context", "thinking", "thinking", "tool_call", "tool_result", "done"]
    tool_call = [e for e in col.events if e["t"] == "tool_call"][0]
    assert tool_call["name"] == "knowledge_search" and tool_call["round"] == 1
    tool_res = [e for e in col.events if e["t"] == "tool_result"][0]
    assert tool_res["success"] is True and tool_res["elapsed_ms"] == 850
    s = col.summary()
    assert s["rounds"] == 1 and s["tool_calls"] == 1 and s["retrieval_hits"] == 2
    assert s["dur_ms"] >= 0 and s["answer_chars"] == 4


def test_collector_tool_failure_recorded() -> None:
    col = svc.TraceCollector(session_id="s1", user_id="u1", kind="agent")
    col.on_event({"type": "tool_call", "tool_calls": [{"id": "c1", "name": "db_query"}]})
    col.on_event({"type": "tool", "results": [
        {"id": "c1", "name": "db_query", "result": "err", "success": False, "error": "boom"},
    ], "durations": [0.1]})
    res = [e for e in col.events if e["t"] == "tool_result"][0]
    assert res["success"] is False and res["error"] == "boom"


def test_save_and_query_roundtrip(db) -> None:
    col = svc.TraceCollector(
        session_id="s1", user_id="u1", kb_id="kb1", agent_id="ag1", kind="agent",
        question="问题", model="deepseek-v4-pro",
    )
    _feed(col)
    tid = svc.save_trace(db, col, message_id="msg1")
    assert tid.startswith("trace_")

    listed = svc.list_traces(db, user_id="u1")
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["kind"] == "agent" and item["kb_id"] == "kb1" and item["agent_id"] == "ag1"
    assert item["summary"]["tool_calls"] == 1
    # 列表不带大字段
    assert "events" not in item and "thinking" not in item

    detail = svc.get_trace(db, tid)
    assert detail is not None
    assert detail["thinking"] == "思考段1思考段2"
    assert len(detail["events"]) == 7 and len(detail["hits"]) == 2


def test_save_requires_session_and_user(db) -> None:
    col = svc.TraceCollector(session_id="", user_id="u1", kind="quick")
    _feed(col)
    assert svc.save_trace(db, col) == ""
    assert svc.list_traces(db)["total"] == 0


def test_save_trace_is_best_effort(db) -> None:
    # events 序列化异常等不应抛错（save_trace 内部吞掉）
    col = svc.TraceCollector(session_id="s1", user_id="u1")
    col.events = [{"t": "x"}]
    col.on_event({"type": "delta", "text": "hi"})
    tid = svc.save_trace(db, col, message_id="")
    assert tid.startswith("trace_")
    detail = svc.get_trace(db, tid)
    assert detail is not None and detail["message_id"] == ""


def test_trace_dict_never_exposes_raw_columns(db) -> None:
    col = svc.TraceCollector(session_id="s1", user_id="u1", kind="quick")
    col.on_event({"type": "thinking", "text": "t"})
    tid = svc.save_trace(db, col)
    d = svc.get_trace(db, tid)
    assert d is not None
    assert set(d) == {
        "id", "session_id", "message_id", "user_id", "team_name", "kb_id",
        "agent_id", "kind", "question", "model", "summary", "created_at",
        "thinking", "events", "hits",
    }