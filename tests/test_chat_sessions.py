"""Chat session service tests — conversation CRUD + message persistence.

Uses an in-memory SQLite engine on the framework Base (same pattern as
test_framework_models / test_retrieval).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models import chat_session  # noqa: F401  (register tables)
from api.services import chat_sessions as svc


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_create_and_list_sessions(db) -> None:
    s1 = svc.create_session(db, "user1", "kb1", None)
    s2 = svc.create_session(db, "user1", "kb2", title="我的会话")
    assert s1["title"] == "新会话"
    assert s2["title"] == "我的会话"

    listed = svc.list_sessions(db, "user1")
    assert listed["total"] == 2


def test_session_scoped_to_user(db) -> None:
    svc.create_session(db, "user1", "kb1", None)
    svc.create_session(db, "user2", "kb1", None)
    assert svc.list_sessions(db, "user1")["total"] == 1


def test_update_and_pin(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    updated = svc.update_session(db, "user1", s["id"], title="新标题", pinned=True)
    assert updated["title"] == "新标题"
    assert updated["pinned"] is True

    listed = svc.list_sessions(db, "user1")
    assert listed["items"][0]["id"] == s["id"]  # pinned first


def test_append_and_load_messages(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    svc.append_message(db, "user1", s["id"], "user", "问题一")
    svc.append_message(db, "user1", s["id"], "assistant", "回答一", refs=[{"chunk_id": "c1", "score": 0.9}])
    msgs = svc.load_messages(db, "user1", s["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["refs"] == [{"chunk_id": "c1", "score": 0.9}]
    assert msgs[0]["content"] == "问题一"


def test_get_context_messages_limited(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    for i in range(30):
        svc.append_message(db, "user1", s["id"], "user" if i % 2 == 0 else "assistant", f"msg{i}")
    ctx = svc.get_context_messages(db, s["id"], limit=10)
    assert len(ctx) == 10
    # 有 limit 上限，且包含最近消息
    assert ctx[-1]["content"] == "msg29"


def test_clear_and_delete(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    svc.append_message(db, "user1", s["id"], "user", "hi")
    svc.clear_session_messages(db, "user1", s["id"])
    assert svc.load_messages(db, "user1", s["id"]) == []

    s2 = svc.create_session(db, "user1", "kb1", None)
    svc.append_message(db, "user1", s2["id"], "user", "hi")
    svc.delete_session(db, "user1", s2["id"])
    assert svc.list_sessions(db, "user1")["total"] == 1


def test_batch_delete(db) -> None:
    s1 = svc.create_session(db, "user1", "kb1", None)
    s2 = svc.create_session(db, "user1", "kb1", None)
    n = svc.batch_delete_sessions(db, "user1", [s1["id"], s2["id"]])
    assert n == 2
    assert svc.list_sessions(db, "user1")["total"] == 0


def test_delete_message(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    m1 = svc.append_message(db, "user1", s["id"], "user", "a")
    m2 = svc.append_message(db, "user1", s["id"], "user", "b")
    svc.delete_message(db, "user1", s["id"], m1["id"])
    left = svc.load_messages(db, "user1", s["id"])
    assert [m["id"] for m in left] == [m2["id"]]