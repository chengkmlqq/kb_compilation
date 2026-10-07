"""Chat session fork / rewind (WeKnora branching alignment).

Covers fork_session (copy session up to a message, parent_session_id link)
and rewind_session (truncate after a message).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base
from api.models.chat_session import ChatMessage, ChatSession
from api.services.chat_sessions import fork_session, rewind_session


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


def _mk_session(db, sid="sess-1", user="u1"):
    db.add(ChatSession(id=sid, user_id=user, kb_id=None, title="t", pinned=0))
    for i, (role, content) in enumerate(
        [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    ):
        db.add(
            ChatMessage(
                id=f"msg-{i}",
                session_id=sid,
                seq=i,
                role=role,
                content=content,
            )
        )
    db.commit()


def test_fork_at_message_boundary(db):
    _mk_session(db)
    data = fork_session(db, "u1", "sess-1", message_id="msg-1")
    assert data["parent_session_id"] == "sess-1"
    assert data["message_count"] == 2
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == data["id"])
        .order_by(ChatMessage.seq)
    ).scalars().all()
    assert [r.content for r in rows] == ["q1", "a1"]
    assert [r.seq for r in rows] == [0, 1]


def test_fork_default_copies_all(db):
    _mk_session(db)
    data = fork_session(db, "u1", "sess-1")
    assert data["message_count"] == 4
    assert data["parent_session_id"] == "sess-1"


def test_fork_unknown_message_404(db):
    _mk_session(db)
    with pytest.raises(HTTPException) as e:
        fork_session(db, "u1", "sess-1", message_id="msg-nope")
    assert e.value.status_code == 404


def test_fork_other_user_404(db):
    _mk_session(db)
    with pytest.raises(HTTPException) as e:
        fork_session(db, "u2", "sess-1")
    assert e.value.status_code == 404


def test_rewind_truncates_after_anchor(db):
    _mk_session(db)
    data = rewind_session(db, "u1", "sess-1", message_id="msg-1")
    assert data["removed"] == 2
    assert data["remaining"] == 2
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == "sess-1")
        .order_by(ChatMessage.seq)
    ).scalars().all()
    assert [r.content for r in rows] == ["q1", "a1"]


def test_rewind_keeps_anchor(db):
    _mk_session(db)
    rewind_session(db, "u1", "sess-1", message_id="msg-0")
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == "sess-1")
        .order_by(ChatMessage.seq)
    ).scalars().all()
    assert [r.content for r in rows] == ["q1"]


def test_rewind_unknown_message_404(db):
    _mk_session(db)
    with pytest.raises(HTTPException) as e:
        rewind_session(db, "u1", "sess-1", message_id="msg-nope")
    assert e.value.status_code == 404