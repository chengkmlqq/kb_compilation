"""loop 内步骤失败 → run 必须收敛终态 failed(2026-10-28 bug 回归测试)。

现象:摘要-Map-Reduce 编排「LLM子摘要」在 round=6 因 429 限流失败后,
kb_tape_run 状态永远停在 running —— 任务监控显示"运行中",实际已失败。

根因:`_advance_impl` 的终态判定依赖 `all_done`(expected_rounds 全部轮次
完成)。loop 展开(`dispatch_next_round`)要求「本轮 body 全部完成」才开下一轮;
某轮 body 有步骤 failed 时本轮永远凑不齐 → `expected_rounds` 永远无法满足
→ `all_done` 恒 False → run 卡 running,失败不冒泡到 run 级。

修复:`_advance_impl` 在无就绪节点且无 loop 可展开时,若已存在 failed 步骤
且存在「残留未完成轮次」(被失败阻断、永远不会再来),直接按 failed 收敛
run 终态,而不是无限等待。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base
from api.models.orchestration import Tape, TapeRun
from worker.tasks import orch_loop
from worker.tasks import orch_runner


@pytest.fixture()
def run_db(monkeypatch):
    """in-memory 库 + 指向它的 get_sessionmaker。"""
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


def _add_loop_tape(db) -> None:
    """loop 编排:loop → 循环体 [LLM子摘要, 写文档摘要]。"""
    db.add(Tape(
        id="tape-sum", tape_name="摘要-Map-Reduce", tape_label="摘要",
        tape_type="general", status="draft", nodes=[], edges=[], exec_params={},
    ))
    db.add_all([
        # 用 api.models.orchestration.TapeStep 建步骤定义
    ])
    db.commit()


def _add_run(db, run_id: str, steps: list[dict]) -> TapeRun:
    run = TapeRun(
        id=run_id, tape_id="tape-sum", tape_name="摘要-Map-Reduce",
        status="running", inputs={"purpose": "summary", "document_id": "doc-1"},
        step_results=steps,
        bindings={},
    )
    db.add(run)
    db.commit()
    return run


def _step(sid: str, status: str, round_no: int, **kw) -> dict:
    base = {
        "step_id": sid, "step_inst": "script", "step_label": sid,
        "queue": "orch", "status": status, "error": None,
        "duration_ms": 100, "round": round_no, "body": None,
    }
    base.update(kw)
    return base


def test_loop_step_failed_stuck_running_is_converged_to_failed(run_db, monkeypatch) -> None:
    """round=6 的 LLM子摘要 失败 + 本轮后续步骤未执行 → run 收敛 failed,而非卡 running。"""
    db = run_db
    db.add(Tape(
        id="tape-sum", tape_name="摘要-Map-Reduce", tape_label="摘要",
        tape_type="general", status="draft", nodes=[], edges=[], exec_params={},
    ))
    db.commit()

    # 步骤:loop + 循环体 2 步(LLM子摘要 → 写文档摘要)
    steps_def = {
        "s_loop": {"id": "s_loop", "inst": "loop", "label": "逐批循环",
                   "config": {"item_var": "batch"}, "queue": "orch", "pre": ["s_read"], "next": ["s_llm"]},
        "s_llm": {"id": "s_llm", "inst": "llm", "label": "LLM子摘要",
                  "config": {}, "queue": "orch", "pre": ["s_loop"], "next": ["s_write"]},
        "s_write": {"id": "s_write", "inst": "script", "label": "写文档摘要",
                    "config": {}, "queue": "orch", "pre": ["s_llm"], "next": []},
        "s_read": {"id": "s_read", "inst": "script", "label": "读文档切片",
                   "config": {}, "queue": "orch", "pre": [], "next": ["s_loop"]},
    }
    monkeypatch.setattr(orch_runner, "_load_steps", lambda tape_id: dict(steps_def))
    monkeypatch.setattr(orch_runner, "_get_tape", lambda tape_id: db.get(Tape, tape_id))
    monkeypatch.setattr(orch_runner, "_send_node", lambda *a, **k: None)

    # loop 共 7 轮。round 0-5 全部 success;round 6:LLM子摘要 failed,
    # 写文档摘要 未执行(被失败阻断)。loop 自身 success。
    results = [
        _step("s_read", "success", 0),
        _step("s_loop", "success", 0, body=["b0", "b1", "b2", "b3", "b4", "b5", "b6"]),
    ]
    for rnd in range(7):
        results.append(_step("s_llm", "success", rnd))
        results.append(_step("s_write", "success", rnd))
    # round 6:LLM子摘要 failed(找到 round=6 的 s_llm 记录替换)
    for i, r in enumerate(results):
        if r["step_id"] == "s_llm" and r["round"] == 6:
            results[i] = _step(
                "s_llm", "failed", 6,
                error="LLM 调用失败: HTTP 429 限流,已自动重试 5 次仍未成功",
            )
            # 关键:失败轮的后继「写文档摘要 round=6」从未执行(被失败阻断)
            # → expected_rounds 轮次缺口,all_done 永远 False,run 卡 running
            break
    results = [r for r in results if not (r["step_id"] == "s_write" and r["round"] == 6)]
    run = _add_run(db, "run-stuck", results)

    # 注入 loop 展开状态:7 轮已全部展开,idx=7(最后一轮已投)
    run.bindings = {
        orch_loop.LOOP_STATE_KEY: {
            "loops": {
                "s_loop": {
                    "items": ["b0", "b1", "b2", "b3", "b4", "b5", "b6"],
                    "idx": 7,
                    "body_ids": ["s_llm", "s_write"],
                    "current_round": 6,
                    "bindings": {},
                },
            }
        }
    }
    db.commit()

    # 模拟 state 已展开后再推进(round 6 失败后回调 advance)
    orch_runner._advance_impl("run-stuck")

    db.expire_all()
    run = db.get(TapeRun, "run-stuck")
    assert run.status == "failed", f"loop 内步骤失败必须收敛 failed,实际 {run.status}"
    assert "429" in (run.error or "")

    # 被失败阻断的「写文档摘要 round=6」应补成 skipped,保证轮次可判定
    pairs = orch_loop.done_pairs(run.step_results)
    assert ("s_write", 6) in pairs, "被阻断的循环体步骤应标记 skipped"
    assert ("s_llm", 6) in pairs


def test_loop_all_success_stays_success(run_db, monkeypatch) -> None:
    """对照:全轮 success 的 run 正常收敛 success,不误伤。"""
    db = run_db
    db.add(Tape(
        id="tape-sum", tape_name="摘要-Map-Reduce", tape_label="摘要",
        tape_type="general", status="draft", nodes=[], edges=[], exec_params={},
    ))
    db.commit()
    steps_def = {
        "s_loop": {"id": "s_loop", "inst": "loop", "label": "逐批循环",
                   "config": {"item_var": "batch"}, "pre": ["s_read"], "next": ["s_llm"]},
        "s_llm": {"id": "s_llm", "inst": "llm", "label": "LLM子摘要",
                  "config": {}, "pre": ["s_loop"], "next": ["s_write"]},
        "s_write": {"id": "s_write", "inst": "script", "label": "写文档摘要",
                    "config": {}, "pre": ["s_llm"], "next": []},
        "s_read": {"id": "s_read", "inst": "script", "label": "读文档切片",
                   "config": {}, "pre": [], "next": ["s_loop"]},
    }
    monkeypatch.setattr(orch_runner, "_load_steps", lambda tape_id: dict(steps_def))
    monkeypatch.setattr(orch_runner, "_get_tape", lambda tape_id: db.get(Tape, tape_id))
    monkeypatch.setattr(orch_runner, "_send_node", lambda *a, **k: None)

    results = [
        _step("s_read", "success", 0),
        _step("s_loop", "success", 0, body=["b0", "b1"]),
    ]
    for rnd in range(2):
        results.append(_step("s_llm", "success", rnd))
        results.append(_step("s_write", "success", rnd))
    run = _add_run(db, "run-ok", results)
    run.bindings = {
        orch_loop.LOOP_STATE_KEY: {
            "loops": {
                "s_loop": {
                    "items": ["b0", "b1"],
                    "idx": 2,
                    "body_ids": ["s_llm", "s_write"],
                    "current_round": 1,
                    "bindings": {},
                },
            }
        }
    }
    db.commit()

    orch_runner._advance_impl("run-ok")

    db.expire_all()
    run = db.get(TapeRun, "run-ok")
    assert run.status == "success", f"全轮成功应收敛 success,实际 {run.status}"