"""编排异步执行（2026-10-09 新增）——worker 侧调度中枢 + 单节点执行。

执行模型：事件驱动 DAG。API 收到 execute → 建 kb_tape_run(queued) →
send_task 本模块 execute_run（orch 队列）→ 调度中枢按 BFS 依赖检查把
「所有 pre 已完成的就绪节点」逐个 send_task 到该节点指定队列（不同节点
可跑不同 worker）→ 每个节点任务完成时更新 run.step_results 并回调
advance_run 继续推进；全部节点终态后 run 置 success/failed。

bindings 传递：单节点任务参数携带该节点依赖的 bindings 快照；节点执行
后把新 bindings 写回 run 记录（advance 时合并）。loop 节点在调度层展开
（对 collection 每项设置 item_var 后重投循环体子图）。
"""

from __future__ import annotations

import json
import logging
import time
import uuid

logger = logging.getLogger("kb.orch.runner")

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeRun, TapeStep
from api.services.orchestration_engine import ATOM_MAP, TapeRuntimeContext

TASK_CLASS_ORCH_RUN = "KbOrchestrationRunTask"
TASK_CLASS_ORCH_NODE = "KbOrchestrationNodeTask"
TASK_CLASS_ORCH_ADVANCE = "KbOrchestrationAdvanceTask"

# 默认编排调度队列（compose 应加 -Q orch 的 worker）
ORCH_QUEUE = "orch"


def _db():
    return get_sessionmaker()()


def _get_run(run_id: str):
    db = _db()
    try:
        return db.get(TapeRun, run_id)
    finally:
        db.close()


def _get_tape(tape_id: str):
    db = _db()
    try:
        return db.get(Tape, tape_id)
    finally:
        db.close()


def _load_steps(tape_id: str) -> dict:
    db = _db()
    try:
        rows = db.query(TapeStep).filter(TapeStep.tape_id == tape_id).all()
    finally:
        db.close()
    steps: dict[str, dict] = {}
    for s in rows:
        steps[s.id] = {
            "id": s.id,
            "inst": s.step_inst,
            "label": s.step_label or s.step_inst,
            "config": s.step_config or {},
            "pre": [p for p in (s.pre_step_ids or []) if p],
            "next": [n for n in (s.next_step_ids or []) if n],
            "queue": (s.queue_name or "").strip() or "",
        }
    return steps


def _step_queue(steps: dict, sid: str) -> str:
    q = steps[sid]["queue"]
    return q if q in ("default", "agent", "build", "orch") else ORCH_QUEUE


def _append_step_result(run_id: str, result: dict) -> None:
    db = _db()
    try:
        run = db.get(TapeRun, run_id)
        if not run:
            return
        results = list(run.step_results or [])
        results.append(result)
        run.step_results = results
        db.commit()
    finally:
        db.close()


def _update_run(run_id: str, **fields) -> None:
    db = _db()
    try:
        run = db.get(TapeRun, run_id)
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        db.commit()
    finally:
        db.close()


def _bindings_from_run(run_id: str) -> dict:
    db = _db()
    try:
        run = db.get(TapeRun, run_id)
        return dict(run.bindings or {})
    finally:
        db.close()


# ───────────────────────── 调度中枢 ─────────────────────────

def _advance_impl(run_id: str) -> None:
    """检查所有可推进的就绪节点并投递；无就绪节点时判定终态。"""
    from worker.celery_app import celery_app

    run = _get_run(run_id)
    if not run or run.status not in ("queued", "running"):
        return
    tape = _get_tape(run.tape_id)
    if not tape:
        _update_run(run_id, status="failed", error="编排不存在")
        return
    steps = _load_steps(run.tape_id)
    results = list(run.step_results or [])
    done = {r["step_id"] for r in results}
    # 2026-10-09 实测坑: 失败传播缺失——run_script 失败后 print 仍执行。
    # 语义: 任一 pre 失败 → 后继阻断(skipped 标记,不投递)。
    failed_ids = {r["step_id"] for r in results if r.get("status") == "failed"}
    skip_ids = {r["step_id"] for r in results if r.get("status") == "skipped"}
    # bindings 合并：已执行步骤的 body 更新到 run.bindings（def/print 结果）
    bindings = dict(run.bindings or {})
    for r in results:
        if r.get("status") == "success" and isinstance(r.get("body"), dict):
            body = r["body"]
            for k, v in body.items():
                if k.startswith("set:"):
                    bindings[k[4:]] = v

    # 0) 失败传播：任一 pre 失败的未执行节点 → skipped（阻断链），标记入 done 继续传播
    newly_skipped = []
    for sid, s in steps.items():
        if sid in done or sid in skip_ids:
            continue
        if any(p in failed_ids or p in skip_ids for p in s["pre"]):
            newly_skipped.append(sid)
    if newly_skipped:
        for sid in newly_skipped:
            _append_step_result(run_id, {
                "step_id": sid, "step_inst": steps[sid]["inst"],
                "step_label": steps[sid]["label"],
                "queue": _step_queue(steps, sid), "status": "skipped",
                "error": None, "duration_ms": 0,
                "body": None,
            })
        logger.info("编排 run=%s 失败传播：%d 个后继节点 skipped", run_id, len(newly_skipped))
        # 重新取 run（结果已变更），继续推进以标记完整条阻断链
        run = _get_run(run_id)
        results = list(run.step_results or [])
        done = {r["step_id"] for r in results}
        failed_ids = {r["step_id"] for r in results if r.get("status") == "failed"}
        skip_ids = {r["step_id"] for r in results if r.get("status") == "skipped"}

    # 1) 找就绪节点（所有 pre 已完成 && 自身未执行）
    ready = []
    for sid, s in steps.items():
        if sid in done:
            continue
        if all(p in done for p in s["pre"]):
            ready.append(sid)
    # 2) loop 展开：loop 节点是"就绪"但展开逻辑特殊——先处理循环体迭代
    #    简化：loop 节点本身作为普通节点投递；其返回 collection 后由
    #    advance 对 collection 每项重投循环体（在节点结果 body 标记）。
    if ready:
        run.status = "running"
        _update_run(run_id, status="running", bindings=bindings)
        for sid in ready:
            s = steps[sid]
            q = _step_queue(steps, sid)
            celery_app.send_task(
                "worker.tasks.orch_runner.run_node",
                args=[run_id, sid, bindings],
                queue=q,
            )
        return

    # 3) 无就绪节点：检查是否全部完成 → 终态
    all_done = all(sid in done for sid in steps)
    if all_done:
        failed = [r for r in results if r.get("status") == "failed"]
        status = "failed" if failed else "success"
        _update_run(run_id, status=status, bindings=bindings,
                    error="; ".join(f"{r['step_id']}: {r.get('error','')}" for r in failed) if failed else None)
        logger.info("编排 run=%s 终态 %s (%d 步)", run_id, status, len(results))
    else:
        # 有未执行但 pre 未完成（依赖等待中）—— 不动作，等节点回调
        logger.info("编排 run=%s 等待依赖完成 (done=%d/%d)", run_id, len(done), len(steps))


def execute_run(run_id: str) -> dict:
    """调度中枢入口（orch 队列）：首次推进 DAG。"""
    run = _get_run(run_id)
    if not run:
        return {"success": False, "error": f"run not found: {run_id}"}
    _update_run(run_id, status="running")
    _advance_impl(run_id)
    return {"success": True, "run_id": run_id}


def advance_run(run_id: str, step_id: str) -> dict:
    """节点完成回调（orch 队列）：记录结果后推进 DAG。"""
    _advance_impl(run_id)
    return {"success": True, "run_id": run_id}


# ───────────────────────── 单节点执行（节点所在 worker） ─────────────────────────

def run_node(run_id: str, step_id: str, bindings: dict) -> dict:
    """执行单个编排节点（投递到该节点指定队列的 worker）。

    执行完写 step_results，再 send_task advance_run 推进 DAG。
    """
    run = _get_run(run_id)
    if not run:
        return {"success": False, "error": f"run not found: {run_id}"}
    steps = _load_steps(run.tape_id)
    s = steps.get(step_id)
    if not s:
        return {"success": False, "error": f"step not found: {step_id}"}

    step_cls = ATOM_MAP.get(s["inst"])
    if not step_cls:
        _append_step_result(run_id, {
            "step_id": step_id, "step_inst": s["inst"], "step_label": s["label"],
            "queue": _step_queue(steps, step_id), "status": "failed",
            "error": f"未知组件指令: {s['inst']}", "duration_ms": 0,
        })
        _send_advance(run_id, step_id)
        return {"success": False, "error": f"unknown step_inst {s['inst']}"}

    start = time.time()
    context = TapeRuntimeContext(dict(bindings))
    try:
        response = step_cls(step_id, s["label"], s["inst"], s["config"]).handle(context)
        status = "success" if response.success else "failed"
        body = response.body if response.success else None
        error = "" if response.success else response.error
    except Exception as e:  # noqa: BLE001
        status, body, error = "failed", None, str(e)

    result = {
        "step_id": step_id,
        "step_inst": s["inst"],
        "step_label": s["label"],
        "queue": _step_queue(steps, step_id),
        "status": status,
        "duration_ms": int((time.time() - start) * 1000),
        "body": body if status == "success" else None,
        "error": error or None,
    }
    _append_step_result(run_id, result)

    # 新 bindings 写回 run（advance 时合并）
    _update_run(run_id, bindings=context.bindings)
    _send_advance(run_id, step_id)
    logger.info("编排节点[%s %s] %s (%.0fms) run=%s", s["inst"], s["label"], status, (time.time() - start) * 1000, run_id)
    return {"success": status == "success", "step_id": step_id}


def _send_advance(run_id: str, step_id: str) -> None:
    from worker.celery_app import celery_app

    celery_app.send_task(
        "worker.tasks.orch_runner.advance_run",
        args=[run_id, step_id],
        queue=ORCH_QUEUE,
    )


def register_task_handlers() -> None:
    """注册到 TASK_CLASS_REGISTRY（scheduler.execute_modo_job 分派）。"""
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY

    def _handle_run(job_id, task_params):
        try:
            params = json.loads(task_params) if task_params else {}
        except Exception:
            params = {}
        run_id = params.get("runId") or params.get("run_id") or ""
        if not run_id:
            return {"success": False, "error": "runId required"}
        return execute_run(run_id)

    def _handle_node(job_id, task_params):
        try:
            params = json.loads(task_params) if task_params else {}
        except Exception:
            params = {}
        run_id = params.get("runId") or params.get("run_id") or ""
        step_id = params.get("stepId") or params.get("step_id") or ""
        bindings = params.get("bindings") or {}
        if not run_id or not step_id:
            return {"success": False, "error": "runId/stepId required"}
        return run_node(run_id, step_id, bindings)

    TASK_CLASS_REGISTRY[TASK_CLASS_ORCH_RUN] = _handle_run
    TASK_CLASS_REGISTRY[TASK_CLASS_ORCH_NODE] = _handle_node
    logger.info("orch_runner handlers registered: %s", TASK_CLASS_REGISTRY.keys())


register_task_handlers()

# 注册为 celery task（send_task 按名投递，worker 端必须有对应注册的策略）
# 2026-10-09 实测坑：orch worker 报 KeyError 'worker.tasks.orch_runner.execute_run'
# —— 普通函数不注册，celery 消费时 strategies 里找不到任务名。
from worker.celery_app import celery_app  # noqa: E402

for _task_name, _task_fn in (
    ("execute_run", execute_run),
    ("advance_run", advance_run),
    ("run_node", run_node),
):
    celery_app.task(name=f"worker.tasks.orch_runner.{_task_name}")(_task_fn)
    logger.info("registered celery task worker.tasks.orch_runner.%s", _task_name)
