"""任务管理（cron）API 测试：列表/新建/更新/启停/删除 + 非法 JSON 校验。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, CronTask
from api.routers import cron as cron_mod
from api.services.identity import Identity


@pytest.fixture()
def cron_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    # Depends(get_db) 在路由定义时已绑定，需用 dependency_overrides 注入
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(cron_mod, "decode_identity_cookie", lambda cookie: Identity(user_id="u1"))
    # RBAC middleware：u1 为管理员（AUTH_ADMIN_USERS），放行菜单权限检查。
    # middleware 用独立 session（它会 close 自己的 session），不能与端点共用 db。
    import types

    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda Session=Session: type("SM", (), {"__call__": lambda s: Session()})(),
    )
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
    )
    from api.services.identity import encode_identity_cookie

    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    yield client, db
    app.dependency_overrides.clear()
    db.close()


def _seed(db, **kwargs):
    task = CronTask(
        id=kwargs.get("id", "cron-1"),
        name=kwargs.get("name", "cleanup"),
        label=kwargs.get("label", "清理任务"),
        cron_expression=kwargs.get("cron_expression", "0 2 * * *"),
        task_class=kwargs.get("task_class", "LogCleanupTask"),
        state=kwargs.get("state", "1"),
        fire_params=kwargs.get("fire_params", None),
        queue_name=kwargs.get("queue_name", "default"),
        next_fire_time=None,
    )
    db.add(task)
    db.commit()
    return task


def test_list_cron_tasks(cron_client) -> None:
    client, db = cron_client
    _seed(db)
    resp = client.get("/api/v1/cron?pageNum=1&pageSize=10")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["totalElements"] == 1
    assert data["content"][0]["taskClass"] == "LogCleanupTask"


def test_list_cron_tasks_keyword(cron_client) -> None:
    client, db = cron_client
    _seed(db, id="cron-1", name="cleanup", label="清理任务")
    _seed(db, id="cron-2", name="embed", label="向量化任务")
    resp = client.get("/api/v1/cron?keyWord=清理")
    assert resp.json()["data"]["totalElements"] == 1
    assert resp.json()["data"]["content"][0]["id"] == "cron-1"


def test_create_cron_task(cron_client) -> None:
    client, db = cron_client
    resp = client.post(
        "/api/v1/cron",
        json={
            "name": "embed-all",
            "label": "全量向量化",
            "cronExpression": "0 3 * * *",
            "taskClass": "KbDocumentEmbedTask",
            "state": "1",
            "fireParams": '{"force": true}',
            "queueName": "embedding",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    rows = db.execute(select(CronTask)).scalars().all()
    assert len(rows) == 1
    assert rows[0].task_class == "KbDocumentEmbedTask"


def test_create_cron_task_invalid_fire_params(cron_client) -> None:
    client, _db = cron_client
    resp = client.post(
        "/api/v1/cron",
        json={
            "name": "bad",
            "label": "坏参数",
            "cronExpression": "0 3 * * *",
            "taskClass": "KbDocumentEmbedTask",
            "fireParams": "not-json",
        },
    )
    assert resp.status_code == 400


def test_update_cron_task(cron_client) -> None:
    client, db = cron_client
    _seed(db)
    resp = client.put(
        "/api/v1/cron/cron-1",
        json={
            "name": "cleanup2",
            "label": "清理任务v2",
            "cronExpression": "0 4 * * *",
            "taskClass": "LogCleanupTask",
            "state": "0",
            "queueName": "default",
        },
    )
    assert resp.status_code == 200
    task = db.execute(select(CronTask)).scalars().first()
    assert task.name == "cleanup2"
    assert task.state == "0"
    assert task.cron_expression == "0 4 * * *"


def test_toggle_cron_task(cron_client) -> None:
    client, db = cron_client
    _seed(db)
    resp = client.post("/api/v1/cron/cron-1/toggle?state=0")
    assert resp.status_code == 200
    task = db.execute(select(CronTask)).scalars().first()
    assert task.state == "0"


def test_delete_cron_task(cron_client) -> None:
    client, db = cron_client
    _seed(db)
    resp = client.delete("/api/v1/cron/cron-1")
    assert resp.status_code == 200
    assert db.execute(select(CronTask)).scalars().all() == []
