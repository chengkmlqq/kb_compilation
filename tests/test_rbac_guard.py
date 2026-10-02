"""RBAC guard tests — middleware maps API paths to page routes and enforces
role-menu assignment (source proxy.ts behaviour)."""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.main import app
from api.middleware import is_whitelisted, resolve_page_route
from api.models.framework import Menu, RoleMenuRela, User, UserRole, UserRoleRela
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
            User(id="u2", user_id="li_si", user_name="李四", state="1"),
            UserRole(role_id="admin", role_name="平台管理员", role_type="plat-mgr", state="1"),
            UserRole(role_id="viewer", role_name="只读", role_type="team-role", state="1"),
            UserRoleRela(rela_id="x1", role_id="admin", user_id="zhang_san"),
            UserRoleRela(rela_id="x2", role_id="viewer", user_id="li_si"),
            Menu(menu_id="m1", menu_name="kbs", menu_label="知识库", route="/kbs", sort_num=1, state="1"),
            Menu(menu_id="m2", menu_name="chat", menu_label="智能问答", route="/chat", sort_num=2, state="1"),
            RoleMenuRela(rela_id="rm1", role_id="viewer", menu_id="m1"),
        ]
    )
    db.commit()
    db.close()

    def _sessionmaker():
        return Session

    # Guard middleware + route dependencies must both use the sqlite session.
    monkeypatch.setattr("api.middleware.get_sessionmaker", _sessionmaker)
    monkeypatch.setattr("api.db.get_sessionmaker", _sessionmaker)
    monkeypatch.setattr("api.db.get_knowledge_sessionmaker", _sessionmaker)
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="zhang_san"),
    )
    return TestClient(app)


def _cookie(user_id: str) -> dict:
    ident = Identity(user_id=user_id, user_name=user_id)
    return {"x-next-identity": encode_identity_cookie(ident)}


# ---- mapping / whitelist units ----


def test_resolve_page_route() -> None:
    assert resolve_page_route("/api/v1/kbs") == "/kbs"
    assert resolve_page_route("/api/v1/kbs/abc/documents") == "/kbs"
    assert resolve_page_route("/api/v1/qa/stream") == "/chat"
    assert resolve_page_route("/api/v1/system/roles") == "/system"
    assert resolve_page_route("/api/v1/jobs") == "/jobs"
    assert resolve_page_route("/api/v1/not-a-page") is None


def test_is_whitelisted() -> None:
    assert is_whitelisted("/health") is True
    assert is_whitelisted("/api/v1/auth/login") is True
    assert is_whitelisted("/api/v1/me") is True
    assert is_whitelisted("/api/v1/open/datasources") is True
    assert is_whitelisted("/api/v1/system/my-menus") is True
    assert is_whitelisted("/api/v1/kbs") is False
    assert is_whitelisted("/api/v1/system/roles") is False


# ---- guard behaviour through the app ----


def test_whitelisted_always_allowed(client) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 200
    assert r.json()["success"] is False  # no cookie -> not logged in, but not blocked


def test_controlled_path_without_identity_fail_closed(client) -> None:
    r = client.get("/api/v1/kbs")
    assert r.status_code == 401  # fail-closed since auth hardening
    body = r.json()
    assert body["error"] == "未登录"


def test_platform_admin_bypasses_guard(client) -> None:
    r = client.get("/api/v1/kbs", cookies=_cookie("zhang_san"))
    assert r.status_code == 200  # plat-mgr role bypass


def test_role_with_menu_permission_allowed(client) -> None:
    # li_si has viewer role linked to /kbs menu
    r = client.get("/api/v1/kbs", cookies=_cookie("li_si"))
    assert r.status_code == 200


def test_role_without_menu_permission_blocked(client) -> None:
    # li_si viewer role is NOT linked to /chat
    r = client.get("/api/v1/qa/stream", cookies=_cookie("li_si"))
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "访问被拒绝"
    assert body["reason"] == "无菜单权限"


def test_uncontrolled_path_passes_even_with_identity(client) -> None:
    # /api/v1/models maps to /models which has no menu row -> uncontrolled -> allow
    r = client.get("/api/v1/models", cookies=_cookie("li_si"))
    assert r.status_code == 200


def test_my_menus_whitelisted_for_any_logged_user(client) -> None:
    r = client.get("/api/v1/system/my-menus", cookies=_cookie("li_si"))
    assert r.status_code == 200
    assert r.json()["success"] is True
    ids = [m["menu_id"] for m in r.json()["data"]["items"]]
    assert ids == ["m1"]  # viewer role linked only to m1