"""知识聚合配置测试（2026-10-25）：create/update/delete KB 的聚合开关、
定时任务（modo_cron_task）同步与编排立即触发。

覆盖：
  1. create_kb 开启主题聚合 → 建 agg_topic_{kb_id} 定时任务（fire_params
     含 tape_name/topics）+ 立即触发编排（假 celery 收到 execute_modo_job）；
  2. 开启主题聚合但主题为空 → ValueError（主题必填）；
  3. update_kb 关闭开关 → 对应定时任务删除；开启版本聚类 → 新增；
  4. update_kb 修改主题列表 → fire_params 同步更新；
  5. delete_kb → 级联删除聚合定时任务；
  6. enqueue_aggregation_run 幂等：同编排已有 queued/running run 时跳过。

测试环境：sqlite in-memory + get_db 覆盖（对齐 test_orchestration_manual_env）；
celery_app 用 sys.modules 假模块绕开真实 import（无 broker）。
"""
from __future__ import annotations

import json
import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.framework import CronTask
from api.models.orchestration import Tape, TapeStep
from api.services import aggregation as agg
from api.services.identity import Identity, encode_identity_cookie
from api.services.kb_admin import delete_kb, update_kb
from conftest import create_test_kb as create_kb


@pytest.fixture()
def env(monkeypatch):
    """in-memory 库 + get_db 覆盖 + 假 celery。"""
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
    ns = types.SimpleNamespace()
    setattr(ns, "AUTH" + "_ADMIN_USERS", "")
    monkeypatch.setattr("api.middleware.get_settings", lambda: ns)
    monkeypatch.setattr("api.config.get_settings", lambda: ns)
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )

    class _FakeCelery:
        @staticmethod
        def send_task(name, args, queue, task_id=None):
            sent.append((name, args, queue, task_id))

    sent: list = []
    fake = types.ModuleType("worker.celery_app")
    setattr(fake, "celery_app", _FakeCelery)
    monkeypatch.setitem(sys.modules, "worker.celery_app", fake)

    yield db, client, sent
    app.dependency_overrides.pop(get_db, None)


def _seed_aggregation_tapes(db) -> None:
    """建两个聚合编排（effective 状态，供立即触发查 Tape）。"""
    for name, step_count in (
        (agg.TAPE_NAME_TOPIC_CLUSTER, 1),
        (agg.TAPE_NAME_VERSION_EVOLUTION, 1),
    ):
        tid = uuid.uuid4().hex[:32]
        db.add(Tape(id=tid, tape_name=name, tape_label=name, tape_type="general",
                    status="effective", nodes=[], edges=[], exec_params={}))
        db.add(TapeStep(id=f"{tid[:12]}::n1", tape_id=tid, step_inst="script",
                        step_label="n1", step_config={"code": "pass"}, step_seq=0,
                        pre_step_ids=[], next_step_ids=[]))
    db.commit()


def _cron_row(db, cron_id: str) -> CronTask | None:
    return db.execute(select(CronTask).where(CronTask.id == cron_id)).scalars().first()


def test_create_kb_topic_cluster_creates_cron_and_triggers(env) -> None:
    """开启主题聚合：建 agg_topic_{kb_id} 定时任务 + 立即触发主题聚类编排。"""
    db, _, sent = env
    _seed_aggregation_tapes(db)

    kb = create_kb(
        db,
        name="聚合测试库",
        aggregation_config={
            "topic_cluster_enabled": True,
            "topic_cluster_topics": ["采购管理", "合同管理"],
        },
    )
    assert kb.aggregation_config["topic_cluster_enabled"] is True
    assert kb.aggregation_config["topic_cluster_topics"] == ["采购管理", "合同管理"]

    cron_id = f"agg_topic_{kb.id}"
    row = _cron_row(db, cron_id)
    assert row is not None
    assert row.task_class == "KbAggregationRunTask"
    assert row.state == "1"
    assert row.cron_expression == agg.DEFAULT_CRON_EXPRESSION
    params = json.loads(row.fire_params)
    assert params["kb_id"] == kb.id
    assert params["tape_name"] == agg.TAPE_NAME_TOPIC_CLUSTER
    assert params["topics"] == ["采购管理", "合同管理"]
    assert params["env"] == "prod"

    # 未开版本聚类：无 agg_version 任务
    assert _cron_row(db, f"agg_version_{kb.id}") is None

    # 立即触发：假 celery 收到 execute_modo_job（ORCH_RUN_* job）
    orch_sends = [s for s in sent if s[0].endswith("execute_modo_job")]
    assert len(orch_sends) == 1
    job_id = orch_sends[0][1][0]
    assert job_id.startswith("ORCH_RUN_")


def test_create_kb_topic_cluster_requires_topics(env) -> None:
    """开启主题聚合但主题为空 → ValueError（主题必填）。"""
    db, _, _ = env
    with pytest.raises(ValueError, match="至少填写一个主题"):
        create_kb(
            db,
            name="空主题库",
            aggregation_config={
                "topic_cluster_enabled": True,
                "topic_cluster_topics": [],
            },
        )


def test_create_kb_version_cluster_only(env) -> None:
    """仅开版本聚类：建 agg_version 任务，无 agg_topic 任务。"""
    db, _, sent = env
    _seed_aggregation_tapes(db)

    kb = create_kb(
        db,
        name="版本聚类库",
        aggregation_config={"version_cluster_enabled": True},
    )
    assert _cron_row(db, f"agg_version_{kb.id}") is not None
    assert _cron_row(db, f"agg_topic_{kb.id}") is None
    version_sends = [
        s for s in sent
        if s[0].endswith("execute_modo_job") and "版本" in json.dumps(
            _cron_row(db, f"agg_version_{kb.id}").fire_params, ensure_ascii=False
        )
    ]
    # 立即触发只有版本聚类一次（无主题聚合）
    assert len([s for s in sent if s[0].endswith("execute_modo_job")]) == 1


def test_update_kb_toggles_and_updates_crons(env) -> None:
    """编辑：开启主题聚合 → 建任务；关闭 → 删任务；改主题 → fire_params 更新。"""
    db, _, sent = env
    _seed_aggregation_tapes(db)

    kb = create_kb(db, name="开关测试库")
    assert _cron_row(db, f"agg_topic_{kb.id}") is None

    # 开启主题聚合
    update_kb(
        db, kb.id,
        {"aggregation_config": {
            "topic_cluster_enabled": True,
            "topic_cluster_topics": ["质量管理"],
        }},
    )
    row = _cron_row(db, f"agg_topic_{kb.id}")
    assert row is not None
    assert json.loads(row.fire_params)["topics"] == ["质量管理"]

    # 修改主题列表 → fire_params 更新
    update_kb(
        db, kb.id,
        {"aggregation_config": {
            "topic_cluster_enabled": True,
            "topic_cluster_topics": ["质量管理", "安全生产"],
        }},
    )
    row = _cron_row(db, f"agg_topic_{kb.id}")
    assert json.loads(row.fire_params)["topics"] == ["质量管理", "安全生产"]

    # 关闭 → 删除任务
    update_kb(
        db, kb.id,
        {"aggregation_config": {
            "topic_cluster_enabled": False,
            "topic_cluster_topics": [],
        }},
    )
    assert _cron_row(db, f"agg_topic_{kb.id}") is None


def test_update_kb_adds_version_cluster(env) -> None:
    """编辑时追加开启版本聚类 → 新增 agg_version 任务。"""
    db, _, _ = env
    _seed_aggregation_tapes(db)

    kb = create_kb(db, name="追加版本库")
    update_kb(
        db, kb.id,
        {"aggregation_config": {"version_cluster_enabled": True}},
    )
    row = _cron_row(db, f"agg_version_{kb.id}")
    assert row is not None
    assert json.loads(row.fire_params)["tape_name"] == agg.TAPE_NAME_VERSION_EVOLUTION


def test_delete_kb_cascades_aggregation_crons(env) -> None:
    """删除知识库：级联删除聚合定时任务。"""
    db, _, sent = env
    _seed_aggregation_tapes(db)

    kb = create_kb(
        db,
        name="待删库",
        aggregation_config={
            "topic_cluster_enabled": True,
            "topic_cluster_topics": ["采购"],
            "version_cluster_enabled": True,
        },
    )
    assert _cron_row(db, f"agg_topic_{kb.id}") is not None
    assert _cron_row(db, f"agg_version_{kb.id}") is not None

    result = delete_kb(db, kb.id)
    assert result["success"] is True
    assert _cron_row(db, f"agg_topic_{kb.id}") is None
    assert _cron_row(db, f"agg_version_{kb.id}") is None


def test_enqueue_aggregation_run_skips_when_active(env) -> None:
    """同编排已有 queued/running run 时立即触发跳过（幂等防重）。"""
    db, _, sent = env
    _seed_aggregation_tapes(db)
    kb = create_kb(db, name="防重库")

    # 首次触发成功
    r1 = agg.enqueue_aggregation_run(db, agg.TAPE_NAME_TOPIC_CLUSTER, kb.id, ["主题A"])
    assert r1["success"] is True and r1["skipped"] is False
    assert len([s for s in sent if s[0].endswith("execute_modo_job")]) == 1

    # 同编排已有 queued run → 跳过，不重复投递
    r2 = agg.enqueue_aggregation_run(db, agg.TAPE_NAME_TOPIC_CLUSTER, kb.id, ["主题A"])
    assert r2["success"] is True and r2["skipped"] is True
    assert r2["run_id"] == r1["run_id"]
    assert len([s for s in sent if s[0].endswith("execute_modo_job")]) == 1


def test_normalize_aggregation_config_rules(env) -> None:
    """归一规则：去空白/去重、默认 cron、非法 cron 拒绝。"""
    cfg = agg.normalize_aggregation_config({
        "topic_cluster_enabled": True,
        "topic_cluster_topics": [" 采购管理 ", "采购管理", "合同管理"],
        "version_cluster_enabled": False,
    })
    assert cfg["topic_cluster_topics"] == ["采购管理", "合同管理"]
    assert cfg["cron_expression"] == agg.DEFAULT_CRON_EXPRESSION

    with pytest.raises(ValueError, match="定时表达式不合法"):
        agg.normalize_aggregation_config({"cron_expression": "not a cron"})
