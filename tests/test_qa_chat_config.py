"""智能问答页联网搜索按钮显隐参数（PLATFORM_CONFIG.CHAT_WEB_SEARCH_ENABLED）测试。

- validate_platform_value 对 bool 型参数规范化 1/0 并拒绝非法值；
- GET /api/v1/qa/config 返回 web_search_enabled：DB 参数 > 默认值（默认显示）。
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, Dim
from api.services.identity import Identity, encode_identity_cookie
from api.services.system_config import validate_platform_value
import api.middleware as mw


@pytest.fixture()
def qa_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        mw, "get_sessionmaker", lambda Session=Session: type("SM", (), {"__call__": lambda s: Session()})()
    )
    monkeypatch.setattr(mw, "get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    yield client, db
    app.dependency_overrides.clear()
    db.close()


def _set_websearch_param(db, value: str) -> None:
    db.add(
        Dim(
            id="dim-chat-websearch",
            dim_group="PLATFORM_CONFIG",
            dim_code="CHAT_WEB_SEARCH_ENABLED",
            dim_value=value,
            state="1",
        )
    )
    db.commit()


def test_validate_bool_param_normalizes() -> None:
    assert validate_platform_value("CHAT_WEB_SEARCH_ENABLED", "1") == "1"
    assert validate_platform_value("CHAT_WEB_SEARCH_ENABLED", "true") == "1"
    assert validate_platform_value("CHAT_WEB_SEARCH_ENABLED", "on") == "1"
    assert validate_platform_value("CHAT_WEB_SEARCH_ENABLED", "0") == "0"
    assert validate_platform_value("CHAT_WEB_SEARCH_ENABLED", "false") == "0"
    with pytest.raises(ValueError):
        validate_platform_value("CHAT_WEB_SEARCH_ENABLED", "banana")


def test_chat_config_default_shows_websearch(qa_client) -> None:
    """未配置参数时默认显示联网搜索按钮（保持原有行为）。"""
    client, _ = qa_client
    r = client.get("/api/v1/qa/config")
    assert r.status_code == 200
    assert r.json()["data"]["web_search_enabled"] is True


def test_chat_config_param_hides_websearch(qa_client) -> None:
    """参数置 0 → 按钮隐藏。"""
    client, db = qa_client
    _set_websearch_param(db, "0")
    r = client.get("/api/v1/qa/config")
    assert r.status_code == 200
    assert r.json()["data"]["web_search_enabled"] is False


def test_chat_config_param_true_str(qa_client) -> None:
    """参数为 'true'（检索参数组的写法）也能识别为显示。"""
    client, db = qa_client
    _set_websearch_param(db, "true")
    r = client.get("/api/v1/qa/config")
    assert r.json()["data"]["web_search_enabled"] is True