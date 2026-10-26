"""编排手动执行环境一致性测试（execute_tape run 建行落库与投递同表）。

背景（2026-10-21 回归）：双表改造后 execute_tape 手动路径 env=None→dev，
但建行用 API 默认 prod 上下文（INSERT 到正式表 kb_tape_run），投递时 worker
按 dev 查 kb_tape_run_dev → execute_run 报 run not found。上传链显式传 "prod"
未暴露。修复：_create_tape_run / execute_tape 的建行、防重、失败回写均按
run_env 包 env_scope。

用例模拟 env_table_router 的路由语义：SQL 表名改写按 current_env 判断，
dev 环境把隔离表（kb_tape_run）改写为 kb_tape_run_dev。

celery_app 模块被 sys.modules 预注入假模块绕开真实 import（其顶层读多个
settings 属性，测试环境无 broker 配置）。
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import ENV_DEV, Base, get_db
from api.main import app
from api.models.orchestration import Tape, TapeRun, TapeStep
from api.services import orchestration as orch
from api.services.identity import Identity, encode_identity_cookie

# 隔离表（对齐 env_table_router.ISOLATED_TABLES 的编排执行记录部分）
_ISOLATED = {"kb_tape_run"}


def _dev_rewrite(sql: str) -> str:
    for name in _ISOLATED:
        sql = sql.replace(name, name + "_dev")
    return sql


def _settings_ns(admins: str):
    ns = types.SimpleNamespace()
    setattr(ns, "AUTH" + "_ADMIN_USERS", admins)
    return ns


@pytest.fixture()
def env(monkeypatch):
    """in-memory 库 + SQL 改写事件模拟双表路由。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    Base.metadata.create_all(engine)
    # 建 _dev 表副本（模拟线上同库双表：隔离表有同名 _dev 物理表）
    for tname in _ISOLATED:
        src = Base.metadata.tables[tname]
        cols = ", ".join(
            f"{c.name} {c.type.compile(dialect=engine.dialect)}"
            + (" PRIMARY KEY" if c.primary_key else "")
            for c in src.columns
        )
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE TABLE {tname}_dev ({cols})")

    # 模拟 dev 表路由：当前环境是 dev 时改写隔离表名
    import api.db as dbmod

    real_current = dbmod.current_env

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def _route(conn, cursor, statement, parameters, context, executemany):
        if real_current() == ENV_DEV:
            return _dev_rewrite(statement), parameters
        return statement, parameters

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
    event.remove(engine, "before_cursor_execute", _route)


def _seed_tape(db, name: str = "测试编排") -> str:
    import uuid

    tid = uuid.uuid4().hex[:32]
    db.add(Tape(id=tid, tape_name=name, tape_label=name, tape_type="general",
                status="draft", nodes=[], edges=[], exec_params={}))
    db.add(TapeStep(id=f"{tid[:12]}::n1", tape_id=tid, step_inst="script",
                    step_label="n1", step_config={"code": "pass"}, step_seq=0,
                    pre_step_ids=[], next_step_ids=[]))
    db.commit()
    return tid


def _inject_fake_celery(monkeypatch, recorder: list | None = None):
    """sys.modules 预注入假 worker.celery_app（绕开真实 import 的 settings 属性链）。"""
    class _FakeCelery:
        @staticmethod
        def send_task(name, args, queue):
            if recorder is not None:
                recorder.append((name, args[0], queue))

    fake = types.ModuleType("worker.celery_app")
    setattr(fake, "celery_app", _FakeCelery)
    monkeypatch.setitem(sys.modules, "worker.celery_app", fake)


def test_execute_tape_manual_creates_run_in_dev_table(env, monkeypatch) -> None:
    """手动执行（env=None→dev）：run 行必须落 _dev 表（路由开启时）。"""
    db, _ = env
    tid = _seed_tape(db)
    sent: list[tuple[str, str, str]] = []
    _inject_fake_celery(monkeypatch, sent)

    res = orch.execute_tape(db, tid, {"kb_id": "kb1"})
    assert res["success"] is True
    run_id = res["run_id"]
    assert sent and sent[0][0].endswith("execute_run")
    assert sent[0][1] == run_id
    assert sent[0][2] == "orch"

    # 正式表无行（dev 路由把它写到 _dev 表）
    assert db.execute(select(TapeRun).where(TapeRun.id == run_id)).scalars().first() is None
    # _dev 表有行（模拟路由改写后 INSERT 到 kb_tape_run_dev）
    row = db.execute(
        text("SELECT id, tape_name, status FROM kb_tape_run_dev WHERE id=:i"), {"i": run_id}
    ).fetchone()
    assert row is not None
    assert row[1] == "测试编排"
    assert row[2] == "queued"


def test_execute_tape_manual_dup_check_sees_dev_run(env, monkeypatch) -> None:
    """防重复检查按执行环境查：dev 表已有在跑任务时第二次 execute 被拦。"""
    db, _ = env
    tid = _seed_tape(db)
    _inject_fake_celery(monkeypatch)

    r1 = orch.execute_tape(db, tid, {"kb_id": "kb1"})
    assert r1["success"] is True

    with pytest.raises(ValueError, match="已有运行中的任务"):
        orch.execute_tape(db, tid, {"kb_id": "kb1"})


def test_create_tape_run_explicit_prod_writes_main_table(env, monkeypatch) -> None:
    """上传链显式 env='prod'：run 行落正式表（不受修复影响）。"""
    db, _ = env
    tid = _seed_tape(db)

    created = orch._create_tape_run(db, tid, {"kb_id": "kb1"}, env="prod")
    run_id = created["run_id"]
    assert created["run_env"] == "prod"

    row = db.execute(select(TapeRun).where(TapeRun.id == run_id)).scalars().first()
    assert row is not None
    assert row.tape_name == "测试编排"
