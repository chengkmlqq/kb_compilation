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


# ============================================================================
# 管理端（对齐 ds notification-actions: send / users / test-alert-api）
# ============================================================================


def test_send_message_to_specific_user(client):
    """发送消息给指定用户（对齐 ds sendSystemMessageAction 指定 userIds）。"""
    r = client.post(
        "/api/v1/system/notifications/send",
        json={
            "userIds": ["zhang_san"],
            "title": "系统公告",
            "content": "今晚维护",
            "type": "WARNING",
            "priority": "HIGH",
        },
        cookies=_cookie(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["sent"] == 1
    lst = client.get(
        "/api/v1/system/notifications", params={"tab": "all", "page": 1, "page_size": 50},
        cookies=_cookie(),
    ).json()
    titles = [m["title"] for m in lst["data"]["items"]]
    assert "系统公告" in titles


def test_send_message_to_all_when_userids_empty(client):
    """userIds 为空 → 发给全部启用用户（对齐 ds 全量语义）。"""
    r = client.post(
        "/api/v1/system/notifications/send",
        json={"title": "全员通知", "content": "hello"},
        cookies=_cookie(),
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["data"]["sent"] >= 1


def test_list_notification_users(client):
    """通知目标用户列表：仅启用用户 + 关键词过滤（对齐 ds getUserListForNotificationAction）。"""
    r = client.get("/api/v1/system/notifications/users", cookies=_cookie())
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    uids = [u["user_id"] for u in body["data"]["items"]]
    assert "zhang_san" in uids

    r2 = client.get(
        "/api/v1/system/notifications/users", params={"keyWord": "zhang"}, cookies=_cookie()
    )
    uids2 = [u["user_id"] for u in r2.json()["data"]["items"]]
    assert "zhang_san" in uids2


def test_test_alert_api_validates_endpoint(client):
    """短信告警接口测试：非 http/https 地址与非法方法被拒（对齐 ds 校验）。"""
    r = client.post(
        "/api/v1/system/notifications/test-alert-api",
        json={"endpoint": "ftp://bad", "method": "POST", "alertTitle": "x"},
        cookies=_cookie(),
    )
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "http" in r.json()["message"]

    r2 = client.post(
        "/api/v1/system/notifications/test-alert-api",
        json={"endpoint": "http://127.0.0.1:1/x", "method": "DELETE", "alertTitle": "x"},
        cookies=_cookie(),
    )
    assert r2.json()["success"] is False
    assert "POST" in r2.json()["message"]


def test_send_message_unauthorized(client):
    """未登录发消息 → 401（受控路径 fail-closed，与接收端未登录一致）。"""
    r = client.post(
        "/api/v1/system/notifications/send",
        json={"title": "x", "content": "y"},
    )
    assert r.status_code == 401
