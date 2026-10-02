"""Tests for the Worker monitor API (api/routers/workers.py) — Flower HTTP
proxy normalization (workers overview / worker tasks / registered tasks).
Flower calls are monkeypatched; no live Flower needed.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, CronTask

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def _make_session() -> Session:
    Base.metadata.create_all(engine)
    db = Session()
    return db


def _fake_flower_map() -> dict[str, Any]:
    """模拟 Flower /api/workers?refresh=true 的原始响应。"""
    return {
        "celery@host1": {
            "status": True,
            "timestamp": 1728000000,
            "active_queues": [{"name": "default"}, {"name": "embedding"}],
            "registered": [
                {"name": "worker.tasks.scheduler.scan_cron_tasks"},
                {"name": "worker.tasks.doc_process.KbDocumentProcessTask"},
                {"name": "worker.tasks.doc_process.KbDocumentEmbedTask"},
            ],
            "stats": {
                "total": {"KbDocumentProcessTask": 12, "KbDocumentEmbedTask": 3},
                "pool": {"max-concurrency": 2},
                "prefetch_count": 4,
            },
            "active": [
                {
                    "request": {
                        "uuid": "task-1",
                        "name": "worker.tasks.doc_process.KbDocumentProcessTask",
                        "type": "KbDocumentProcessTask",
                        "delivery_info": {"routing_key": "default"},
                    },
                    "state": "RUNNING",
                    "timestamp": 1728000060,
                }
            ],
            "reserved": [],
            "scheduled": [],
        },
        "celery@host2": {
            "status": False,
            "timestamp": 1727999000,
            "active_queues": [],
            "registered": [],
            "stats": {"total": {}, "pool": {}, "prefetch_count": 0},
            "active": [],
            "reserved": [],
            "scheduled": [],
        },
    }


def _fake_tasks_map() -> dict[str, Any]:
    """模拟 Flower /api/tasks 的最近任务响应。"""
    return {
        "task-recent-1": {
            "uuid": "task-recent-1",
            "name": "worker.tasks.scheduler.scan_cron_tasks",
            "state": "SUCCESS",
            "worker": "celery@host1",
            "delivery_info": {"routing_key": "default"},
            "received": 1728000100,
            "started": 1728000105,
            "succeeded": 1728000107,
            "runtime": 2.1,
            "result": {"success": True},
        }
    }


def test_workers_overview(monkeypatch) -> None:
    """GET /api/v1/workers → 归一化概览（online/offline 汇总）。"""
    db = _make_session()
    app.dependency_overrides[get_db] = lambda: db
    try:
        import api.routers.workers as workers_mod

        # flower 返回 workers 映射（含 status 字段），status?true 单独端点
        def fake_call_flower(path: str, allow_404: bool = False) -> Any:
            if path.startswith("/api/workers?status=true"):
                return {"celery@host1": True, "celery@host2": False}
            if path.startswith("/api/workers"):
                return _fake_flower_map()
            return None

        monkeypatch.setattr(workers_mod, "_call_flower", fake_call_flower)

        import api.routers.workers as w
        from api.services.identity import Identity

        real = w.decode_identity_cookie
        w.decode_identity_cookie = lambda cookie: Identity(
            user_id="u1", team_name="team1"
        )

        import types

        monkeypatch.setattr(
            "api.middleware.get_sessionmaker",
            lambda: type("SM", (), {"__call__": lambda s: db})(),
        )
        monkeypatch.setattr(
            "api.middleware.get_settings",
            lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
        )
        from api.services.identity import encode_identity_cookie

        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(
                Identity(user_id="u1", user_name="u1", team_name="team1")
            ),
        )

        r = client.get("/api/v1/workers")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["summary"] == {"total": 2, "online": 1, "offline": 1}
        by_name = {w_["workerName"]: w_ for w_ in data["workers"]}
        h1 = by_name["celery@host1"]
        assert h1["status"] == "ONLINE"
        assert h1["activeQueueNames"] == ["default", "embedding"]
        assert h1["registeredTaskCount"] == 3
        assert h1["processedTaskCount"] == 15  # 12 + 3
        assert h1["concurrency"] == 2
        assert h1["prefetchCount"] == 4
        assert h1["activeTaskCount"] == 1
        assert h1["lastHeartbeatAt"] is not None
        h2 = by_name["celery@host2"]
        assert h2["status"] == "OFFLINE"
        assert h2["registeredTaskCount"] == 0
    finally:
        app.dependency_overrides.clear()


def test_worker_tasks(monkeypatch) -> None:
    """GET /api/v1/workers/{name}/tasks → 快照 + 最近任务。"""
    db = _make_session()
    app.dependency_overrides[get_db] = lambda: db
    try:
        import api.routers.workers as workers_mod

        def fake_call_flower(path: str, allow_404: bool = False) -> Any:
            if path.startswith("/api/tasks"):
                return _fake_tasks_map()
            if path.startswith("/api/workers"):
                return _fake_flower_map()
            return None

        monkeypatch.setattr(workers_mod, "_call_flower", fake_call_flower)

        from api.services.identity import Identity

        real = workers_mod.decode_identity_cookie
        workers_mod.decode_identity_cookie = lambda cookie: Identity(
            user_id="u1", team_name="team1"
        )

        import types

        monkeypatch.setattr(
            "api.middleware.get_sessionmaker",
            lambda: type("SM", (), {"__call__": lambda s: db})(),
        )
        monkeypatch.setattr(
            "api.middleware.get_settings",
            lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
        )
        from api.services.identity import encode_identity_cookie

        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(
                Identity(user_id="u1", user_name="u1", team_name="team1")
            ),
        )

        r = client.get("/api/v1/workers/celery@host1/tasks")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["workerName"] == "celery@host1"
        assert data["summary"]["active"] == 1
        assert len(data["activeTasks"]) == 1
        assert data["activeTasks"][0]["taskId"] == "task-1"
        assert data["activeTasks"][0]["taskName"].endswith("KbDocumentProcessTask")
        assert data["summary"]["recent"] == 1
        assert data["recentTasks"][0]["resultText"] is not None
    finally:
        app.dependency_overrides.clear()


def test_worker_registered_tasks(monkeypatch) -> None:
    """GET /api/v1/workers/{name}/registered-tasks → 注册任务 + cron 对照。"""
    db = _make_session()
    db.query(CronTask).delete()
    db.add(
        CronTask(
            id="cron-1",
            name="scan",
            label="扫描定时任务",
            cron_expression="*/30 * * * *",
            state="1",
            queue_name="default",
            task_class="worker.tasks.scheduler.scan_cron_tasks",
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        import api.routers.workers as workers_mod

        def fake_call_flower(path: str, allow_404: bool = False) -> Any:
            if path.startswith("/api/workers"):
                return _fake_flower_map()
            return None

        monkeypatch.setattr(workers_mod, "_call_flower", fake_call_flower)

        from api.services.identity import Identity

        real = workers_mod.decode_identity_cookie
        workers_mod.decode_identity_cookie = lambda cookie: Identity(
            user_id="u1", team_name="team1"
        )

        import types

        monkeypatch.setattr(
            "api.middleware.get_sessionmaker",
            lambda: type("SM", (), {"__call__": lambda s: db})(),
        )
        monkeypatch.setattr(
            "api.middleware.get_settings",
            lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1"),
        )
        from api.services.identity import encode_identity_cookie

        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(
                Identity(user_id="u1", user_name="u1", team_name="team1")
            ),
        )

        r = client.get("/api/v1/workers/celery@host1/registered-tasks")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["summary"]["taskCount"] == 3
        by_class = {t["taskClass"]: t for t in data["tasks"]}
        scan = by_class["worker.tasks.scheduler.scan_cron_tasks"]
        assert scan["taskName"] == "扫描定时任务"  # 从 cron label 映射
        assert len(scan["cronConfigs"]) == 1
        assert scan["cronConfigs"][0]["cronExpression"] == "*/30 * * * *"
        doc = by_class["worker.tasks.doc_process.KbDocumentProcessTask"]
        assert doc["cronConfigs"] == []  # 无 cron 配置
    finally:
        app.dependency_overrides.clear()
