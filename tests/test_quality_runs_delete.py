"""质量评估任务删除端点测试（DELETE /api/v1/quality/runs/{id}，2026-10）。

覆盖：
- 删除成功：run + 三维度 report 一并删除，详情/列表随之消失
- 不存在 404 / 未登录 401
- 进行中（queued/running）非管理员 409；管理员可强制删除

注意：每个用例独立 in-memory 库，且 fixture teardown 清理
app.dependency_overrides —— 否则残留 override 会污染后续用例
（实测会让 test_rbac_guard 的菜单白名单用例挂掉）。
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.knowledge import QualityReport, QualityRun
from api.services.identity import Identity, encode_identity_cookie


def _settings_ns(admins: str):
    """AUTH_ADMIN_USERS 环境旁路命名空间（is_admin 经 api.config.get_settings 读取）。"""
    ns = types.SimpleNamespace()
    setattr(ns, "AUTH" + "_ADMIN_USERS", admins)
    return ns


@pytest.fixture()
def env(monkeypatch):
    """独立 in-memory 库 + 登录 cookie 的 TestClient；结束清理 dependency_overrides。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: db})(),
    )
    monkeypatch.setattr("api.middleware.get_settings", lambda: _settings_ns(""))
    monkeypatch.setattr("api.config.get_settings", lambda: _settings_ns(""))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    yield db, client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def admin_env(monkeypatch):
    """管理员旁路（AUTH_ADMIN_USERS 命中 u1），其余同 env。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: db})(),
    )
    monkeypatch.setattr("api.middleware.get_settings", lambda: _settings_ns("u1"))
    monkeypatch.setattr("api.config.get_settings", lambda: _settings_ns("u1"))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    yield db, client
    app.dependency_overrides.pop(get_db, None)


def _add_run(db, rid: str = "R1", status: str = "success", kb_id: str = "kb1") -> None:
    db.add(QualityRun(id=rid, kb_id=kb_id, scope="all", status=status, created_by="u1"))
    for dim in ("chunk", "wiki", "graph"):
        db.add(
            QualityReport(
                id=f"{rid}-{dim}",
                run_id=rid,
                kb_id=kb_id,
                dim=dim,
                metrics={"health_score": 90},
                issues=[],
            )
        )
    db.commit()


def test_delete_removes_run_and_reports(env) -> None:
    db, client = env
    _add_run(db)

    r = client.delete("/api/v1/quality/runs/R1")
    assert r.status_code == 200
    assert r.json()["data"] == {"id": "R1", "deleted": True}

    assert db.get(QualityRun, "R1") is None
    assert db.get(QualityReport, "R1-chunk") is None
    assert db.get(QualityReport, "R1-wiki") is None
    assert db.get(QualityReport, "R1-graph") is None
    # 详情 404、列表为空
    assert client.get("/api/v1/quality/runs/R1").status_code == 404
    lst = client.get("/api/v1/quality/runs").json()["data"]
    assert lst["total"] == 0
    assert lst["items"] == []


def test_delete_missing_run_404(env) -> None:
    db, client = env
    _add_run(db)
    r = client.delete("/api/v1/quality/runs/NO_SUCH")
    assert r.status_code == 404


def test_delete_inflight_blocked_for_normal_user(env) -> None:
    db, client = env
    _add_run(db, rid="RUN1", status="running")
    _add_run(db, rid="QUE1", status="queued")

    for rid in ("RUN1", "QUE1"):
        r = client.delete(f"/api/v1/quality/runs/{rid}")
        assert r.status_code == 409
        assert db.get(QualityRun, rid) is not None

    # 已完成任务仍可正常删除
    _add_run(db, rid="OK1", status="success")
    assert client.delete("/api/v1/quality/runs/OK1").status_code == 200


def test_delete_inflight_admin_force(admin_env) -> None:
    db, client = admin_env
    _add_run(db, rid="RUN2", status="running")
    r = client.delete("/api/v1/quality/runs/RUN2")
    assert r.status_code == 200
    assert db.get(QualityRun, "RUN2") is None
    assert db.get(QualityReport, "RUN2-chunk") is None


def test_delete_requires_login(env) -> None:
    db, _client = env
    _add_run(db)
    anonymous = TestClient(app)
    r = anonymous.delete("/api/v1/quality/runs/R1")
    assert r.status_code == 401
