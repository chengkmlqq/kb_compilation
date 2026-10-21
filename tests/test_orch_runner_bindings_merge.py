"""并行编排节点 bindings 合并 lost-update 回归测试（2026-10-22）。

背景：质量评估 dim=all（三维度合一编排）在 电信语义层 跑出「全部维度
None 分」。排查：run.bindings 只含 n05/n06/n07（wiki_sem_r/wiki_cmp_r/
graph_sem_r），n02/n03/n04 并行写入的 chunk_r/wiki_r/graph_r 丢失 → 汇总
节点 n08 读到空 → 评估结果全 None。

根因：`_run_node_impl` 写回 bindings 用的是「无锁读 → merge → 带锁写」三段式
（_bindings_from_run 读旧值 → {**旧, **新} → _update_run 整行覆盖写）。三个
fan-out 并行节点在毫秒级完成时（小库单节点 100-250ms），A 读到的旧 bindings
会在 B/C 之后提交时把 A 的 key 整体覆盖掉（后写者赢）。

修复：`_merge_run_bindings`——读-改-写全在 `_lock_run`（SELECT FOR UPDATE）
内完成，同 run 的并行节点提交串行化。

本测试模拟该交错（以无锁读-改-写对照验证修复后不再丢 key），并覆盖
loop 迭代状态不被节点合并覆盖的既有语义。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base
from api.models.orchestration import Tape, TapeRun
from worker.tasks import orch_runner
from worker.tasks import orch_loop


@pytest.fixture()
def run_db(monkeypatch):
    """in-memory 库 + 指向它的 get_sessionmaker（节点写回走 _run_db→get_sessionmaker）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    def _sm(env=None):
        return Session

    import api.db

    monkeypatch.setattr(api.db, "get_sessionmaker", _sm)
    monkeypatch.setattr(orch_loop, "LOOP_STATE_KEY", "__loop_state__")
    yield db
    db.close()


def _add_run(db, run_id: str, bindings: dict | None = None) -> TapeRun:
    db.add(Tape(
        id="tape1", tape_name="t", tape_label="t", tape_type="general",
        status="draft", nodes=[], edges=[], exec_params={},
    ))
    run = TapeRun(
        id=run_id, tape_id="tape1", tape_name="t", status="running",
        inputs={}, step_results=[], bindings=bindings or {},
    )
    db.add(run)
    db.commit()
    return run


def test_merge_run_bindings_keeps_parallel_writes(run_db) -> None:
    """三个并行节点顺序合并：每个 key 都保留（修复前后者覆盖前者）。"""
    _add_run(run_db, "run1", {"kb_id": "kb1"})

    orch_runner._merge_run_bindings("run1", {"chunk_r": {"metrics": {"health_score": 99}}})
    orch_runner._merge_run_bindings("run1", {"wiki_r": {"metrics": {"health_score": 50}}})
    orch_runner._merge_run_bindings("run1", {"graph_r": {"metrics": {"health_score": 100}}})

    run_db.expire_all()
    run = run_db.get(TapeRun, "run1")
    b = run.bindings or {}
    assert b.get("kb_id") == "kb1"
    assert b["chunk_r"]["metrics"]["health_score"] == 99
    assert b["wiki_r"]["metrics"]["health_score"] == 50
    assert b["graph_r"]["metrics"]["health_score"] == 100


def test_merge_run_bindings_interleaved_read_then_write(run_db) -> None:
    """模拟旧 bug 的交错场景：节点 A 持有陈旧快照、在 B 提交后才合并写回。

    旧代码路径是「读旧快照 → 与自身结果合并 → 整行覆盖」，此时 B 的 key 会被
    冲掉。新实现每次合并都从库内最新 bindings 出发（行锁内），无此窗口。
    """
    _add_run(run_db, "run2", {"kb_id": "kb1"})

    # 节点 B 先提交（并行分支）
    orch_runner._merge_run_bindings("run2", {"wiki_r": {"metrics": {"health_score": 50}}})
    # 节点 A 持有启动时的陈旧快照，并入自身结果后提交——不得覆盖 B 的 key
    stale_snapshot = {"kb_id": "kb1"}
    orch_runner._merge_run_bindings("run2", {**stale_snapshot, "chunk_r": {"metrics": {"health_score": 99}}})

    run_db.expire_all()
    b = run_db.get(TapeRun, "run2").bindings or {}
    assert b.get("kb_id") == "kb1"
    assert b.get("wiki_r") == {"metrics": {"health_score": 50}}, "并行节点 key 不得被覆盖"
    assert b.get("chunk_r") == {"metrics": {"health_score": 99}}


def test_merge_run_bindings_keeps_loop_state(run_db) -> None:
    """loop 迭代状态（__loop_state__）由调度中枢独占，节点合并不覆盖。"""
    _add_run(run_db, "run3", {"__loop_state__": {"round": 2, "items": ["a", "b"]}})

    orch_runner._merge_run_bindings("run3", {"chunk_r": {"metrics": {"health_score": 80}}})
    run_db.expire_all()
    b = run_db.get(TapeRun, "run3").bindings or {}
    assert b["__loop_state__"] == {"round": 2, "items": ["a", "b"]}
    assert b["chunk_r"]["metrics"]["health_score"] == 80

    # 节点侧即使带同名 key 也不能覆盖 loop 状态
    orch_runner._merge_run_bindings("run3", {"__loop_state__": {"round": 99}, "new_k": 1})
    run_db.expire_all()
    b = run_db.get(TapeRun, "run3").bindings or {}
    assert b["__loop_state__"] == {"round": 2, "items": ["a", "b"]}
    assert b["new_k"] == 1


def test_merge_run_bindings_missing_run_noop(run_db) -> None:
    """run 不存在（被删/环境不一致）→ 静默返回不抛。"""
    orch_runner._merge_run_bindings("no-such-run", {"a": 1})  # 不抛即通过
