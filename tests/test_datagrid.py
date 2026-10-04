"""数据查询（datagrid）API 测试：SQL 执行 / 元数据 / 正反例 / 团队授权。

SQL 执行用内存 sqlite 模拟 mysql 路径（entry.url 传 sqlite://，绕过真实连接）。
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
from api.models.framework import Base, Datasource, TeamDsMap
from api.services.identity import Identity, encode_identity_cookie
import api.middleware as mw
import api.routers.datagrid as dg_mod


@pytest.fixture()
def dg(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        mw, "get_sessionmaker", lambda Session=Session: type("SM", (), {"__call__": lambda s: Session()})()
    )
    monkeypatch.setattr(mw, "get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"))
    # 解码桩：默认 admin/ROOT；测试内可用 make() 覆盖成别的用户/团队
    dg_identity = {"user_id": "admin", "team_name": "ROOT"}

    def make(user_id="admin", team="ROOT"):
        dg_identity["user_id"] = user_id
        dg_identity["team_name"] = team
        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id=user_id, user_name=user_id, team_name=team)),
        )
        return client

    monkeypatch.setattr(dg_mod, "decode_identity_cookie", lambda c: Identity(**dg_identity))

    yield make, db
    app.dependency_overrides.clear()
    db.close()


def _seed_ds(db, name="demo-mysql", ds_type="mysql", url="jdbc:mysql://127.0.0.1:3306/demo", acct="root", auth=None):
    db.add(
        Datasource(
            id=f"id-{name}",
            name=name,
            label="演示库",
            ds_type=ds_type,
            url=url,
            ds_acct=acct,
            ds_auth=auth,
            state="1",
        )
    )
    db.commit()


def test_build_uri_uses_pymysql_driver(dg):
    """回归：URI 必须带 mysql+pymysql 前缀（默认 MySQLdb 未安装会 500）。"""
    from api.services.datagrid import _build_uri
    from api.services.datasource import DatasourceEntry

    entry = DatasourceEntry(
        id="1", name="ds", ds_type="mysql",
        url="jdbc:mysql://10.0.0.5:3306/demo", ds_acct="kb", ds_auth="p@ss",
    )
    uri = _build_uri(entry)
    assert uri.startswith("mysql+pymysql://")
    assert "10.0.0.5:3306" in uri and "demo" in uri
    assert "kb:" in uri


def test_build_uri_postgres_driver(dg):
    from api.services.datagrid import _build_uri
    from api.services.datasource import DatasourceEntry

    entry = DatasourceEntry(
        id="2", name="pg", ds_type="postgresql",
        url="postgresql://pg-host:5432/db1", ds_acct="u", ds_auth="p",
    )
    uri = _build_uri(entry)
    assert uri.startswith("postgresql+psycopg2://")
    assert "pg-host:5432" in uri


def test_build_uri_strips_jdbc_only_params(dg):
    """回归：JDBC 专有参数（useUnicode 等）不能透传给 pymysql/psycopg2。

    真实数据源 URL 形如：
      jdbc:mysql://host:3306/db?useUnicode=true&characterEncoding=UTF-8&useSSL=false
    透传会抛 Connection.__init__() got an unexpected keyword argument 'useUnicode'。
    只保留编码类（转 charset）。
    """
    from api.services.datagrid import _build_uri
    from api.services.datasource import DatasourceEntry

    entry = DatasourceEntry(
        id="3", name="ds3", ds_type="mysql",
        url=(
            "jdbc:mysql://10.0.0.9:3306/db_x?useUnicode=true&characterEncoding=UTF-8"
            "&zeroDateTimeBehavior=convertToNull&allowMultiQueries=true&useSSL=false"
        ),
        ds_acct="u", ds_auth="p",
    )
    uri = _build_uri(entry)
    assert uri.startswith("mysql+pymysql://")
    assert "useUnicode" not in uri
    assert "zeroDateTimeBehavior" not in uri
    assert "allowMultiQueries" not in uri
    assert "useSSL" not in uri
    assert "?" not in uri  # 编码由 _connect 的 connect_args 指定，不走 URI query
    assert "db_x" in uri


def test_datagrid_datasources_admin(dg):
    make, db = dg
    client = make()
    _seed_ds(db)
    r = client.get("/api/v1/datagrid/datasources")
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) == 1 and items[0]["dsName"] == "demo-mysql"
    assert items[0]["schema"] == ""


def test_datagrid_requires_login(dg):
    make, _db = dg
    client = make()
    # 不带 cookie
    bare = TestClient(app)
    r = bare.get("/api/v1/datagrid/datasources")
    assert r.status_code == 401


def test_datagrid_team_auth(dg):
    make, db = dg
    client = make()
    _seed_ds(db)
    db.add(Datasource(id="id-other", name="other-ds", label="其他", ds_type="mysql", url="jdbc:mysql://x:3306/db", state="1"))
    db.commit()
    # 非管理员用户只看到团队授权数据源
    c2 = make(user_id="huqiang", team="test-team")
    r = c2.get("/api/v1/datagrid/datasources")
    assert all(i["dsName"] != "other-ds" for i in r.json()["data"])
    # 授权后可见
    db.add(TeamDsMap(id="m1", ds_name="other-ds", team_name="test-team", is_prod="0"))
    db.commit()
    r = c2.get("/api/v1/datagrid/datasources")
    assert any(i["dsName"] == "other-ds" for i in r.json()["data"])


def test_datagrid_execute_sql_sqlite(dg):
    """sqlite 数据源可执行查询（复用连接引擎）。"""
    make, db = dg
    client = make()
    _seed_ds(db, url="sqlite:///:memory:", ds_type="postgresql")  # sqlite 走通用 engine
    r = client.post("/api/v1/datagrid/execute", json={"dsName": "demo-mysql", "sql": "SELECT 1 AS one, 2 AS two"})
    assert r.status_code == 200
    data = r.json()["data"][0]
    assert data["success"] is True
    assert data["columns"] == ["one", "two"]
    assert data["rows"][0] == {"one": 1, "two": 2}


def test_datagrid_execute_sql_error(dg):
    make, db = dg
    client = make()
    _seed_ds(db, url="sqlite:///:memory:")
    r = client.post("/api/v1/datagrid/execute", json={"dsName": "demo-mysql", "sql": "SELECT * FROM no_such_table"})
    assert r.status_code == 200
    assert r.json()["data"][0]["success"] is False


def test_datagrid_execute_unknown_ds(dg):
    make, _db = dg
    client = make()
    r = client.post("/api/v1/datagrid/execute", json={"dsName": "nope", "sql": "SELECT 1"})
    assert r.status_code == 200
    assert r.json()["data"][0]["success"] is False
    assert "不存在" in r.json()["data"][0]["msg"]


def test_datagrid_tables_meta(dg):
    """表列表端点连通性。

    测试用 sqlite 模拟，无法执行 information_schema 查询（真实 mysql/pg 支持），
    因此只断言端点返回结构化结果（success 或带 msg），不要求拿到表。
    """
    make, db = dg
    client = make()
    _seed_ds(db, url="sqlite:///:memory:")
    r = client.post("/api/v1/datagrid/tables", json={"dsName": "demo-mysql"})
    assert r.status_code == 200
    body = r.json()
    assert "success" in body
    if body["success"] is False:
        assert "msg" in body


def test_datagrid_columns_requires_table(dg):
    make, _db = dg
    client = make()
    r = client.post("/api/v1/datagrid/columns", json={"dsName": "x"})
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "tableName" in r.json()["msg"]


def test_datagrid_all_meta_endpoints_register(dg):
    make, db = dg
    client = make()
    _seed_ds(db, url="sqlite:///:memory:")
    for ep in ("tables", "views", "functions", "procedures", "sequences"):
        r = client.post(f"/api/v1/datagrid/{ep}", json={"dsName": "demo-mysql"})
        assert r.status_code == 200, ep
