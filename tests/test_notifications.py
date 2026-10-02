"""System message (notification) API tests — unread count / list / mark read.

Covers the notification router against a sqlite in-memory framework DB.
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.main import app
from api.models.framework import SystemMessage, User
from api.services.identity import Identity, encode_identity_cookie


@pytest.fixture()
def client(monkeypatch):
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            User(id="u1", user_id="zhang_san", user_name="张三", state="1"),
            SystemMessage(
                id="n1",
                user_id="zhang_san",
                title="欢迎使用",
                content="欢迎使用知识库平台",
                type="INFO",
                is_read="0",
                status="ACTIVE",
            ),
            SystemMessage(
                id="n2",
                user_id="zhang_san",
                title="已读消息",
                content="旧消息",
                type="WARNING",
                is_read="1",
                status="ACTIVE",
            ),
            SystemMessage(
                id="n3",
                user_id="other",
                title="别人消息",
                content="不应看到",
                type="INFO",
                is_read="0",
                status="ACTIVE",
            ),
        ]
    )
    db.commit()
    db.close()

    def _sessionmaker():
        return Session

    monkeypatch.setattr("api.middleware.get_sessionmaker", _sessionmaker)
    monkeypatch.setattr("api.db.get_sessionmaker", _sessionmaker)
    monkeypatch.setattr("api.db.get_knowledge_sessionmaker", _sessionmaker)
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="zhang_san"),
    )
    return TestClient(app)


def _cookie(user_id: str = "zhang_san") -> dict:
    ident = Identity(user_id=user_id, user_name=user_id)
    return {"x-next-identity": encode_identity_cookie(ident)}


def test_unread_count(client) -> None:
    r = client.get("/api/v1/system/notifications/unread-count", cookies=_cookie())
    assert r.status_code == 200
    assert r.json()["data"]["unread_count"] == 1


def test_unread_count_requires_login(client) -> None:
    # /api/v1/system/* maps to the controlled /system page route
    r = client.get("/api/v1/system/notifications/unread-count")
    assert r.status_code == 401  # fail-closed: no identity on a controlled path


def test_list_unread(client) -> None:
    r = client.get("/api/v1/system/notifications", params={"tab": "unread"}, cookies=_cookie())
    body = r.json()
    assert body["success"] is True
    items = body["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == "n1"


def test_list_all_excludes_other_users(client) -> None:
    r = client.get("/api/v1/system/notifications", params={"tab": "all"}, cookies=_cookie())
    body = r.json()
    ids = {i["id"] for i in body["data"]["items"]}
    assert ids == {"n1", "n2"}
    assert "n3" not in ids


def test_mark_read(client) -> None:
    r = client.post("/api/v1/system/notifications/n1/read", cookies=_cookie())
    assert r.json()["success"] is True
    cnt = client.get("/api/v1/system/notifications/unread-count", cookies=_cookie()).json()
    assert cnt["data"]["unread_count"] == 0


def test_mark_all_read(client) -> None:
    r = client.post("/api/v1/system/notifications/read-all", cookies=_cookie())
    assert r.json()["success"] is True
    cnt = client.get("/api/v1/system/notifications/unread-count", cookies=_cookie()).json()
    assert cnt["data"]["unread_count"] == 0
