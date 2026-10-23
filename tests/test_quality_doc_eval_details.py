"""文档转换评估-文件粒度明细接口测试（kb_quality_doc_eval，2026-10-23）。

覆盖：
- GET /quality/docs：按 kb_id 分页返回每文件一行（最新状态）
- 关键字过滤文件名
- 覆盖语义：同一知识库重新评估后旧文件行被替换（后端落库 DELETE+INSERT
  由评估脚本负责，此处验证接口读出的是当前状态）
- 路由不冲突：/docs 与 /runs/{id} 并存

复用 test_quality_dim_runs.py 的 in-memory fixture 模式。
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
from api.models.knowledge import QualityDocEval, QualityRun
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


def _mk_doc_eval(db, kb_id: str, doc_id: str, file_name: str, run_id: str = "R1",
                 parse_state: str = "READY", parse_engine: str = "mineru",
                 duration_ms: int | None = 1000, garbled: bool = False,
                 score: int | None = 100) -> None:
    db.add(QualityDocEval(id=f"{doc_id}-eval", kb_id=kb_id, doc_id=doc_id,
                          run_id=run_id, file_name=file_name, file_ext="pdf",
                          parse_state=parse_state, parse_engine=parse_engine,
                          duration_ms=duration_ms, garbled=garbled, score=score))
    db.commit()


def test_docs_list_by_kb(env) -> None:
    db, client = env
    _mk_doc_eval(db, "kb1", "d1", "a.pdf")
    _mk_doc_eval(db, "kb1", "d2", "b.pdf", parse_state="FAILED", score=0)
    _mk_doc_eval(db, "kb2", "d3", "c.pdf")

    r = client.get("/api/v1/quality/docs", params={"kb_id": "kb1"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 2
    # 按 file_name 升序：a.pdf 在前
    names = [i["file_name"] for i in data["items"]]
    assert names == ["a.pdf", "b.pdf"]
    first = data["items"][0]
    assert first["parse_engine"] == "mineru"
    assert first["duration_ms"] == 1000
    assert first["garbled"] is False
    assert first["doc_id"] == "d1"
    # kb_name 由后端回填（无 kb_datasource 行时为空串，前端兜底显示 id 前 8 位）
    assert "kb_name" in first

    # 不带 kb_id = 全量
    r2 = client.get("/api/v1/quality/docs")
    assert r2.json()["data"]["total"] == 3


def test_docs_keyword_filter(env) -> None:
    db, client = env
    _mk_doc_eval(db, "kb1", "d1", "合同模板.pdf")
    _mk_doc_eval(db, "kb1", "d2", "发票清单.xlsx")

    r = client.get("/api/v1/quality/docs", params={"kb_id": "kb1", "keyword": "合同"})
    body = r.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["file_name"] == "合同模板.pdf"


def test_docs_pagination(env) -> None:
    db, client = env
    for i in range(5):
        _mk_doc_eval(db, "kb1", f"d{i}", f"file{i}.pdf")

    r = client.get("/api/v1/quality/docs", params={"kb_id": "kb1", "page": 1, "page_size": 2})
    body = r.json()
    assert body["data"]["total"] == 5
    assert len(body["data"]["items"]) == 2

    r2 = client.get("/api/v1/quality/docs", params={"kb_id": "kb1", "page": 3, "page_size": 2})
    assert len(r2.json()["data"]["items"]) == 1


def test_docs_cover_semantics(env) -> None:
    """覆盖语义：接口读出的即当前最新状态——同一 doc 只保留一行（模型层唯一约束）。"""
    db, client = env
    _mk_doc_eval(db, "kb1", "d1", "a.pdf", run_id="R1", score=100)
    # 模拟重新评估：同 kb 整批 DELETE 后再 INSERT（评估脚本行为）
    from sqlalchemy import delete

    db.execute(delete(QualityDocEval).where(QualityDocEval.kb_id == "kb1"))
    _mk_doc_eval(db, "kb1", "d1", "a.pdf", run_id="R2", score=60)

    r = client.get("/api/v1/quality/docs", params={"kb_id": "kb1"})
    body = r.json()
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["run_id"] == "R2"
    assert item["score"] == 60


def test_docs_route_conflict_with_runs(env) -> None:
    """/docs 与 /runs/{id} 路由并存：runs 详情仍走原路由。"""
    db, client = env
    db.add(QualityRun(id="RUN1", kb_id="kb1", scope="all", status="success", dim="doc"))
    db.commit()

    r = client.get("/api/v1/quality/runs/RUN1")
    assert r.status_code == 200
    assert r.json()["success"] is True

    r2 = client.get("/api/v1/quality/runs/NOT_EXIST")
    assert r2.status_code == 404