"""Image QA: attachment image storage, data-URL injection, upload gate.

Covers:
- service: image attachments stored with media_type=image + file_data,
  attachment_images() -> data URLs, attachment_contents() ignores images,
  build_messages() attaches images to the user message
- API gate: plain KB session rejects images, agent without the
  image_upload_enabled config rejects, agent with it accepts (200)
"""

from __future__ import annotations

import json
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.framework import TeamMember, User
from api.models.knowledge import KbAgent
from api.services import chat_sessions as svc

# StaticPool: :memory: with the default pool is a fresh empty DB per connection,
# so middleware queries (modo_menu etc.) would fail on their own connection.
_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=_ENGINE)
    session = sessionmaker(bind=_ENGINE, expire_on_commit=False)()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# service layer
# ---------------------------------------------------------------------------


def test_image_attachment_storage_and_urls(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    svc.create_attachment(
        db, "user1", s["id"], "pic.png", ".png", 4, "",
        media_type="image", file_data=b"\x89PNG",
    )
    atts = svc.list_attachments(db, "user1", s["id"])
    assert atts[0]["media_type"] == "image"

    urls = svc.attachment_images(db, s["id"])
    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")

    # text attachments do not appear as images
    svc.create_attachment(db, "user1", s["id"], "a.txt", ".txt", 3, "hello")
    assert len(svc.attachment_images(db, s["id"])) == 1


def test_attachment_contents_ignores_images(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    svc.create_attachment(
        db, "user1", s["id"], "pic.png", ".png", 4, "",
        media_type="image", file_data=b"\x89PNG",
    )
    svc.create_attachment(db, "user1", s["id"], "a.txt", ".txt", 3, "hello world")
    ctx = svc.attachment_contents(db, s["id"])
    assert "hello world" in ctx
    assert "pic.png" not in ctx


def test_build_messages_attaches_image_urls(db) -> None:
    from api.services.chat import build_messages

    msgs = build_messages(
        "图里是什么",
        hits=[],
        image_urls=["data:image/png;base64,AAAA"],
    )
    assert msgs[-1].role == "user"
    assert msgs[-1].images == ["data:image/png;base64,AAAA"]
    # without images the field stays None (string content serialization)
    msgs2 = build_messages("普通问题", hits=[])
    assert msgs2[-1].images is None


# ---------------------------------------------------------------------------
# API layer — upload gate
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, db):
    # StaticPool shared engine across tests — clear rows from previous tests
    from api.models import chat_session as cs_mod

    db.query(KbAgent).delete()
    db.query(TeamMember).delete()
    db.query(User).delete()
    db.query(cs_mod.ChatSession).delete()
    db.query(cs_mod.ChatAttachment).delete()
    db.commit()
    db.add_all(
        [
            User(id="u1", user_id="alice", user_name="Alice", state="1", default_team="T1"),
            TeamMember(member_id="m1", team_name="T1", user_id="alice", role_name="", state="1"),
        ]
    )
    db.commit()

    def _make_agent(name: str, image_upload_enabled: bool) -> str:
        agent = KbAgent(
            id=name,
            name=name,
            config={"agent_mode": "quick-answer", "image_upload_enabled": image_upload_enabled},
            state="1",
        )
        db.add(agent)
        return agent.id

    _make_agent("agent-imgs", True)
    _make_agent("agent-noimgs", False)
    db.commit()

    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    monkeypatch.setattr(
        "api.middleware.get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1")
    )
    app.dependency_overrides[get_db] = lambda: db
    # docreader 解析不依赖真实库：文档附件走 mock
    monkeypatch.setattr(
        "api.services.ingest.parse_document",
        lambda name, ext, data: f"parsed-{name}",
    )
    try:
        from api.services.identity import Identity, encode_identity_cookie

        c = TestClient(app)
        c.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id="alice", user_name="Alice", team_name="T1")),
        )
        yield c
    finally:
        app.dependency_overrides.clear()


def _session_id(client, db, agent_id=None) -> str:
    if agent_id:
        s = svc.create_agent_session(db, "alice", agent_id)
        return s["id"]
    s = svc.create_session(db, "alice", "kb1", None)
    return s["id"]


def test_upload_image_plain_session_rejected(client, db) -> None:
    sid = _session_id(client, db)
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 400
    assert "仅智能体会话支持图片上传" in r.json()["detail"]


def test_upload_image_agent_without_gate_rejected(client, db) -> None:
    sid = _session_id(client, db, agent_id="agent-noimgs")
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 400
    assert "未开启图片上传" in r.json()["detail"]


def test_upload_image_agent_with_gate_accepted(client, db) -> None:
    sid = _session_id(client, db, agent_id="agent-imgs")
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["media_type"] == "image"

    # document attachment still lands as text
    r2 = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("doc.md", b"# hello", "text/markdown")},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["media_type"] == "text"

    listed = client.get(f"/api/v1/sessions/{sid}/attachments").json()["data"]["items"]
    assert {a["media_type"] for a in listed} == {"image", "text"}