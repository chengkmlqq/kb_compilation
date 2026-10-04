"""数据源管理 API 测试：CRUD + 元数据 + 团队授权 + 正反例。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, Datasource, DsCategory, DsType, TeamDsMap
from api.services.identity import Identity, encode_identity_cookie
import api.middleware as mw
import api.routers.datasources as ds_mod
import types


@pytest.fixture()
def ds_client(monkeypatch):
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
    monkeypatch.setattr(ds_mod, "decode_identity_cookie", lambda cookie: Identity(user_id="admin"))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    yield client, db
    app.dependency_overrides.clear()
    db.close()


def _seed_meta(db) -> None:
    db.add(DsCategory(id="c1", category_name="relational", category_label="关系型", sorted=1))
    db.add(
        DsType(
            id="t1", ds_type="mysql", ds_type_label="MySQL",
            ds_category="relational", sorted=1, is_support="1",
        )
    )
    db.add(
        DsType(
            id="t2", ds_type="vector_es", ds_type_label="Elasticsearch",
            ds_category="vector", sorted=2, is_support="1",
        )
    )
    db.commit()


def test_datasource_meta_endpoints(ds_client) -> None:
    client, db = ds_client
    _seed_meta(db)
    r = client.get("/api/v1/open/datasources/categories")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1
    r = client.get("/api/v1/open/datasources/types?dsCategory=relational")
    assert r.status_code == 200
    types = r.json()["data"]
    assert len(types) == 1 and types[0]["dsType"] == "mysql"
    r = client.get("/api/v1/open/datasources/types/mysql")
    assert r.status_code == 200 and r.json()["data"]["dsTypeLabel"] == "MySQL"
    r = client.get("/api/v1/open/datasources/types/NOPE")
    assert r.status_code == 404
    r = client.get("/api/v1/open/datasources/versions?dsType=mysql")
    assert r.status_code == 200
    r = client.get("/api/v1/open/datasources/form-fields?dsType=mysql")
    assert r.status_code == 200


def test_datasource_create_update_delete(ds_client) -> None:
    client, db = ds_client
    _seed_meta(db)
    payload = {
        "name": "test-ds",
        "label": "测试数据源",
        "dsType": "mysql",
        "dsAcct": "root",
        "dsAuth": "secret-pass",
        "url": "jdbc:mysql://127.0.0.1:3306/test",
    }
    r = client.post("/api/v1/open/datasources", json=payload)
    assert r.status_code == 200, r.text
    ds_id = r.json()["data"]["id"]
    assert ds_id
    # 口令应加密存储（非明文）
    row = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
    assert row.ds_auth != "secret-pass"
    # 详情不回显口令
    r = client.get(f"/api/v1/open/datasources/{ds_id}")
    assert r.json()["data"]["dsAuth"] is None
    # 列表可见
    r = client.get("/api/v1/open/datasources?keyword=test-ds")
    assert r.json()["data"]["total"] == 1
    # 更新
    r = client.put(f"/api/v1/open/datasources/{ds_id}", json={**payload, "label": "改标签"})
    assert r.status_code == 200
    row = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
    assert row.label == "改标签"
    # 删除时级联清理团队授权映射
    db.add(TeamDsMap(id="m1", ds_name="test-ds", schema_name="s", team_name="ROOT", is_prod="0"))
    db.commit()
    r = client.delete(f"/api/v1/open/datasources/{ds_id}")
    assert r.status_code == 200
    assert db.execute(select(TeamDsMap)).scalars().all() == []
    r = client.delete(f"/api/v1/open/datasources/{ds_id}")
    assert r.status_code == 404


def test_datasource_vector_es_delete_protected(ds_client) -> None:
    client, db = ds_client
    _seed_meta(db)
    # 种子里的向量库类型是 elasticsearch（不是 vector_es）
    for ds_type in ("elasticsearch", "vector_es"):
        r = client.post(
            "/api/v1/open/datasources",
            json={"name": f"vec-{ds_type}", "label": "向量", "dsType": ds_type},
        )
        ds_id = r.json()["data"]["id"]
        r = client.delete(f"/api/v1/open/datasources/{ds_id}")
        assert r.status_code == 404, ds_type  # ValueError → 404（保护提示）
        assert "不可删除" in r.json()["detail"]
    assert len(db.execute(select(Datasource)).scalars().all()) == 2  # 行仍在


def test_datasource_create_missing_type(ds_client) -> None:
    client, _db = ds_client
    r = client.post("/api/v1/open/datasources", json={"name": "no-type"})
    assert r.status_code == 422  # dsType 必填（Pydantic）


def test_team_ds_auth_roundtrip(ds_client) -> None:
    client, db = ds_client
    r = client.put(
        "/api/v1/open/datasources/team-auth/ROOT",
        json=[{"dsName": "mysql-ds", "schemaName": "test_schema", "isProd": "1"}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["added"] == 1
    r = client.get("/api/v1/open/datasources/team-auth/ROOT")
    items = r.json()["data"]
    assert len(items) == 1 and items[0]["dsName"] == "mysql-ds"
    # 全量覆盖：去掉旧授权
    r = client.put("/api/v1/open/datasources/team-auth/ROOT", json=[])
    assert r.json()["data"]["removed"] == 1
    r = client.get("/api/v1/open/datasources/team-auth/ROOT")
    assert r.json()["data"] == []
