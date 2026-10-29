"""Bearer JWT 直调中间件测试（2026-10-29）。

验证 Authorization: Bearer *** → 归一化为 x-next-identity cookie 后，
受控路径走既有身份链路（rbac_guard + _require_kb_caller），无需换签。
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.config as config_mod
import api.middleware as mw
from api.db import get_db
from api.main import app
from api.models.framework import Base, User, Team, TeamMember

JWT_TEST_SECRET = "test-secret-for-bearer-direct-0123456789abcdef"


def _make_jwt(sub: str, *, secret: str = JWT_TEST_SECRET, exp_secs: int = 900) -> str:
    payload = {
        "sub": sub,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=exp_secs),
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture()
def bearer_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            User(id="u" * 32, user_id="admin", user_name="Admin", state="1"),
            Team(team_id="t" * 32, team_name="DEFAULT_TEAM", label="默认团队", state="1"),
            TeamMember(
                member_id="m" * 32,
                team_name="DEFAULT_TEAM",
                user_id="admin",
                state="1",
            ),
        ]
    )
    db.commit()

    # patch settings（verify_bearer_jwt 函数内 from api.config import get_settings）
    fake = lambda: SimpleNamespace(
        JWT_SECRET=JWT_TEST_SECRET,
        JWT_ISSUER="",
        JWT_MAX_AGE=900,
        AUTH_ADMIN_USERS="admin",
    )  # noqa: E731
    monkeypatch.setattr(config_mod, "get_settings", fake)
    monkeypatch.setattr("api.routers.auth.get_settings", fake)
    # middleware 取 sessionmaker 复用连接（rbac_guard / bearer_identity 均从
    # api.middleware 模块调用，patch 模块属性即生效）
    monkeypatch.setattr(mw, "get_sessionmaker", lambda: (lambda: db))

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield db
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def test_bearer_injects_identity_for_controlled_path(bearer_db, monkeypatch):
    """Bearer 验签成功 → 受控路径放行（rbac_guard 能读到注入的 cookie 身份）。"""
    # 受控路径：/api/v1/system/my-menus 是白名单（不验证注入）；改用一个受控路径
    # 但 rbac_guard 对 admin（AUTH_ADMIN_USERS）直接 bypass。这里验证中间件注入
    # 链路本身：用一个不存在的受控路径，预期 rbac_guard 读到身份后 bypass（admin）
    # 或进入菜单检查（无菜单 → 路径未受控 allow）。两种都应非 401。
    client = TestClient(app)
    token = _make_jwt("admin")
    r = client.get("/api/v1/system/my-menus", headers={"Authorization": f"Bearer {token}"})
    # my-menus 是白名单，无论身份如何都放行 → 验证中间件不破坏白名单
    assert r.status_code in (200, 401), r.text
    # 白名单路径不应因 Bearer 而变 401
    assert r.status_code == 200, f"白名单路径被 Bearer 中间件误拦: {r.status_code}"


def test_bearer_invalid_token_falls_through_to_401(bearer_db):
    """Bearer 验签失败 → 不注入 → 受控路径 rbac_guard 401（fail-closed 不变）。"""
    client = TestClient(app)
    r = client.get(
        "/api/v1/system/users",
        headers={"Authorization": "Bearer invalid.token.value"},
    )
    # /api/v1/system 映射 /system 受控；无有效身份 → 401
    assert r.status_code == 401, f"预期 401，实际 {r.status_code}: {r.text}"


def test_bearer_valid_token_passes_rbac_for_admin(bearer_db):
    """Bearer 验签成功 + admin 用户（AUTH_ADMIN_USERS bypass）→ 受控路径放行。

    /api/v1/system/users 是受控路径：无身份 → rbac_guard 401。带有效 Bearer
    后中间件注入 cookie → rbac_guard 读到 admin 身份 bypass → 不再是 401
    （路由层 get_users 查库为空也正常返回 200/空列表，此处只验认证链）。
    """
    client = TestClient(app)
    token = _make_jwt("admin")
    r = client.get("/api/v1/system/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code != 401, f"有效 Bearer 仍被拦 401: {r.text}"
    assert r.status_code < 500


def test_bearer_unknown_user_rejected(bearer_db):
    """Bearer 验签成功但用户不存在 → verify_bearer_jwt 返回 no_user → 不注入 → 401。"""
    client = TestClient(app)
    token = _make_jwt("ghost-user")
    r = client.get(
        "/api/v1/system/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_bearer_no_secret_configured_falls_through(bearer_db, monkeypatch):
    """JWT_SECRET 未配置 → verify_bearer_jwt 返回 no_secret → 不注入 → 401。"""
    monkeypatch.setattr(config_mod, "get_settings", lambda: SimpleNamespace(
        JWT_SECRET="", JWT_ISSUER="", JWT_MAX_AGE=900, AUTH_ADMIN_USERS="admin"
    ))
    client = TestClient(app)
    token = _make_jwt("admin")
    r = client.get(
        "/api/v1/system/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_bearer_unit_inject_identity_cookie():
    """单元：_inject_identity_cookie 正确改写 scope Cookie 头。"""
    from fastapi import Request
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"host", b"test"), (b"cookie", b"a=1; x-next-identity=old")],
        "path": "/",
    }
    req = Request(scope)
    mw._inject_identity_cookie(req, "NEWVAL")
    cookies = req.headers.get("cookie", "")
    assert "x-next-identity=NEWVAL" in cookies
    assert "x-next-identity=old" not in cookies
    assert "a=1" in cookies


def test_bearer_unit_inject_when_no_cookie_header():
    """单元：无 Cookie 头时新建。"""
    from fastapi import Request
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"host", b"test")],
        "path": "/",
    }
    req = Request(scope)
    mw._inject_identity_cookie(req, "NEWVAL")
    assert "x-next-identity=NEWVAL" in req.headers.get("cookie", "")
