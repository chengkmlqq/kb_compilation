"""质量评估单维度拆分测试（dim=doc|chunk|graph，2026-10-21）。

覆盖：
- launch 带 dim：建行带 dim、inputs 透传 dim、dim=doc 走「文档转换评估」编排名、
  非法 dim 400
- 占位行与自建行（dim 落库）
- list 按 dim 过滤（含 ''=全部）
- _run_item 带 dim 字段与 doc 分

复用 test_quality_runs_delete.py 的 in-memory fixture 模式。
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.routers.quality as qmod
from api.db import Base, get_db
from api.main import app
from api.models.knowledge import QualityReport, QualityRun
from api.services.identity import Identity, encode_identity_cookie


def _settings_ns(admins: str):
    ns = types.SimpleNamespace()
    setattr(ns, "AUTH" + "_ADMIN_USERS", admins)
    return ns


@pytest.fixture()
def env(monkeypatch):
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


def _add_run(db, rid: str = "R1", status: str = "success", kb_id: str = "kb1",
             dim: str = "all", doc_metrics: dict | None = None) -> None:
    db.add(QualityRun(id=rid, kb_id=kb_id, scope="all", status=status,
                      dim=dim, doc_metrics=doc_metrics, created_by="u1"))
    db.commit()


def test_launch_dim_doc_uses_doc_tape_and_writes_dim(env, monkeypatch) -> None:
    db, client = env
    calls: list[dict] = []

    def fake_tape_id(db_, name: str) -> str:
        calls.append({"name": name})
        return f"tape-{name}"

    def fake_execute(db_, tape_id: str, inputs: dict) -> dict:
        calls.append({"tape_id": tape_id, "inputs": inputs})
        return {"run_id": "run-1"}

    monkeypatch.setattr(qmod, "_tape_id_by_name", fake_tape_id)
    monkeypatch.setattr(qmod, "execute_tape", fake_execute)

    r = client.post("/api/v1/quality/runs", json={
        "kb_id": "kb1", "doc_ids": ["d1"], "scope": "single", "dim": "doc",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["dim"] == "doc"

    # 编排名 = 文档转换评估
    assert qmod.DIM_TAPE_NAMES["doc"] == "文档转换评估"
    assert any(c.get("name") == "文档转换评估" for c in calls if "name" in c)
    # inputs 透传 dim
    exec_call = next(c for c in calls if "inputs" in c)
    assert exec_call["inputs"]["dim"] == "doc"

    # 占位行 dim 落库
    row = db.execute(select(QualityRun)).scalars().first()
    assert row.dim == "doc"


def test_launch_dims_map_to_tape_names() -> None:
    assert qmod.DIM_TAPE_NAMES == {
        "doc": "文档转换评估",
        "chunk": "切片评估",
        "graph": "图数据评估",
    }


def test_launch_invalid_dim_400(env, monkeypatch) -> None:
    db, client = env
    monkeypatch.setattr(qmod, "_tape_id_by_name", lambda db_, name: "t")
    monkeypatch.setattr(qmod, "execute_tape", lambda db_, tape_id, inputs: {"run_id": "r"})
    r = client.post("/api/v1/quality/runs", json={
        "kb_id": "kb1", "dim": "bad_dim",
    })
    assert r.status_code == 400
    assert "不支持的评估维度" in r.json()["detail"]


def test_launch_default_dim_all(env, monkeypatch) -> None:
    """不传 dim = 兼容旧调用方，走「质量评估-QA」编排，dim=all。"""
    db, client = env
    tapecalls: list[str] = []

    def fake_tape_id(db_, name: str) -> str:
        tapecalls.append(name)
        return f"tape-{name}"

    monkeypatch.setattr(qmod, "_tape_id_by_name", fake_tape_id)
    monkeypatch.setattr(qmod, "execute_tape", lambda db_, tape_id, inputs: {"run_id": "r"})

    r = client.post("/api/v1/quality/runs", json={"kb_id": "kb1", "doc_ids": ["d1"]})
    assert r.status_code == 200
    assert r.json()["data"]["dim"] == "all"
    assert tapecalls == [qmod.QUALITY_TAPE_NAME]
    row = db.execute(select(QualityRun)).scalars().first()
    assert row.dim == "all"


def test_list_filter_by_dim(env) -> None:
    db, client = env
    _add_run(db, rid="RA", dim="all")
    _add_run(db, rid="RD", dim="doc")
    _add_run(db, rid="RC", dim="chunk")

    lst = client.get("/api/v1/quality/runs").json()["data"]
    assert lst["total"] == 3  # '' = 全部

    lst = client.get("/api/v1/quality/runs?dim=doc").json()["data"]
    assert lst["total"] == 1
    assert lst["items"][0]["id"] == "RD"
    assert lst["items"][0]["dim"] == "doc"

    lst = client.get("/api/v1/quality/runs?dim=chunk").json()["data"]
    assert lst["total"] == 1
    assert lst["items"][0]["id"] == "RC"

    lst = client.get("/api/v1/quality/runs?dim=all").json()["data"]
    assert lst["total"] == 1
    assert lst["items"][0]["id"] == "RA"


def test_run_item_includes_doc_score(env) -> None:
    db, client = env
    _add_run(db, rid="RD", dim="doc",
             doc_metrics={"health_score": 72, "failed": 1, "docs": 5})
    lst = client.get("/api/v1/quality/runs?dim=doc").json()["data"]
    item = lst["items"][0]
    assert item["dim"] == "doc"
    # _run_item 的 scores 含 doc 分（health_score 回落）
    assert item["scores"]["doc"] == 72
    assert item["scores"]["chunk"] is None


def test_report_dim_doc_detail(env) -> None:
    db, client = env
    from uuid import uuid4

    rid = uuid4().hex[:32]
    _add_run(db, rid=rid, dim="doc")
    db.add(QualityReport(id=f"{rid}-doc", run_id=rid, kb_id="kb1", dim="doc",
                         metrics={"health_score": 88}, issues=[{"issue_type": "parse_failed"}]))
    db.commit()
    r = client.get(f"/api/v1/quality/runs/{rid}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["dim"] == "doc"
    assert "doc" in data["reports"]
    assert data["reports"]["doc"]["metrics"]["health_score"] == 88


def test_delete_single_dim_run(env) -> None:
    db, client = env
    _add_run(db, rid="RD", dim="doc")
    db.add(QualityReport(id="RD-doc", run_id="RD", kb_id="kb1", dim="doc",
                         metrics={"health_score": 88}, issues=[]))
    db.commit()
    assert client.delete("/api/v1/quality/runs/RD").status_code == 200
    assert db.get(QualityRun, "RD") is None
    assert db.get(QualityReport, "RD-doc") is None