"""编排（Tape）管理服务——CRUD + 设计器保存同步 + 发布/下线 + 执行。"""

from __future__ import annotations

import json
import uuid

from api.models.orchestration import Tape, TapeRun, TapeStep
from api.services.orchestration_engine import (
    OrchestrationEngine,
    list_step_defines,
    create_step_define,
    update_step_define,
    delete_step_define,
    serialize_step_define,
)


def _tape_payload_to_model(obj: Tape, payload: dict) -> None:
    for field in ("tape_name", "tape_label", "tape_descr", "tape_type"):
        if field in payload:
            setattr(obj, field, payload[field] or ("" if field != "tape_name" else obj.tape_name))


def list_tapes(db, page=1, page_size=20, keyword="", status=""):
    q = db.query(Tape)
    if keyword:
        q = q.filter(Tape.tape_name.like(f"%{keyword}%") | Tape.tape_label.like(f"%{keyword}%"))
    if status:
        q = q.filter(Tape.status == status)
    total = q.count()
    items = q.order_by(Tape.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [serialize_tape(t) for t in items]}


def serialize_tape(t: Tape, with_design: bool = False) -> dict:
    out = {
        "id": t.id,
        "tape_name": t.tape_name,
        "tape_label": t.tape_label or "",
        "tape_descr": t.tape_descr or "",
        "tape_type": t.tape_type or "general",
        "status": t.status or "draft",
        "create_user": t.create_user or "",
        "created_at": t.created_at.isoformat() if t.created_at else "",
        "updated_at": t.updated_at.isoformat() if t.updated_at else "",
    }
    if with_design:
        out["nodes"] = t.nodes or []
        out["edges"] = t.edges or []
        out["exec_params"] = t.exec_params or {}
    return out


def get_tape(db, tape_id: str) -> Tape:
    t = db.get(Tape, tape_id)
    if not t:
        raise KeyError("编排不存在")
    return t


def create_tape(db, payload: dict, user_id: str = "") -> Tape:
    name = str(payload.get("tape_name") or "").strip()
    if not name:
        raise ValueError("编排名称不能为空")
    t = Tape(
        id=uuid.uuid4().hex[:64],
        tape_name=name,
        tape_label=str(payload.get("tape_label") or name)[:128],
        tape_descr=payload.get("tape_descr") or None,
        tape_type=str(payload.get("tape_type") or "general") or "general",
        status="draft",
        nodes=payload.get("nodes") or [],
        edges=payload.get("edges") or [],
        exec_params=payload.get("exec_params") or {},
        create_user=user_id or None,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def update_tape(db, tape_id: str, payload: dict) -> Tape:
    t = get_tape(db, tape_id)
    if "tape_name" in payload:
        name = str(payload["tape_name"] or "").strip()
        if not name:
            raise ValueError("编排名称不能为空")
        t.tape_name = name
    _tape_payload_to_model(t, payload)
    if "nodes" in payload:
        t.nodes = payload["nodes"]
    if "edges" in payload:
        t.edges = payload["edges"]
    if "exec_params" in payload:
        t.exec_params = payload["exec_params"]
    db.commit()
    db.refresh(t)
    return t


def save_tape_design(db, tape_id: str, nodes: list, edges: list, exec_params: dict | None = None) -> Tape:
    """设计器保存：写主表 nodes/edges，并覆盖式同步 kb_tape_step 子表。"""
    t = get_tape(db, tape_id)
    t.nodes = nodes or []
    t.edges = edges or []
    if exec_params is not None:
        t.exec_params = exec_params or {}
    db.commit()

    # 覆盖式同步步骤子表
    db.query(TapeStep).filter(TapeStep.tape_id == tape_id).delete()
    node_by_id = {n.get("id", ""): n for n in (nodes or [])}
    # edges: source->target 与 target->source 两个方向都建（pre/next 依赖）
    next_map: dict[str, list[str]] = {}
    pre_map: dict[str, list[str]] = {}
    for e in edges or []:
        src, tgt = e.get("source"), e.get("target")
        if not src or not tgt:
            continue
        next_map.setdefault(src, []).append(tgt)
        pre_map.setdefault(tgt, []).append(src)
    seq = 0
    for n in nodes or []:
        nid = n.get("id", "")
        data = n.get("data") or {}
        step_inst = str(data.get("stepInst") or data.get("step_inst") or "def")
        # 节点队列（2026-10-09）：data.queue 或 data.config.queue 指定该节点
        # 投递到哪个 worker 队列（default/agent/build/orch），空 = 编排默认
        queue_name = str(
            data.get("queue") or (data.get("config") or {}).get("queue") or ""
        ).strip()
        db.add(
            TapeStep(
                id=nid or uuid.uuid4().hex[:64],
                tape_id=tape_id,
                step_inst=step_inst,
                step_label=str(data.get("label") or data.get("stepLabel") or step_inst)[:128],
                step_config=data.get("config") or {},
                step_seq=seq,
                pre_step_ids=pre_map.get(nid, []),
                next_step_ids=next_map.get(nid, []),
                queue_name=queue_name,
            )
        )
        seq += 1
    db.commit()
    return t


def publish_tape(db, tape_id: str) -> Tape:
    t = get_tape(db, tape_id)
    if not t.nodes:
        raise ValueError("编排尚未设计，无法发布")
    t.status = "effective"
    db.commit()
    db.refresh(t)
    return t


def offline_tape(db, tape_id: str) -> Tape:
    t = get_tape(db, tape_id)
    t.status = "offline"
    db.commit()
    db.refresh(t)
    return t


def delete_tape(db, tape_id: str) -> None:
    t = get_tape(db, tape_id)
    db.query(TapeStep).filter(TapeStep.tape_id == tape_id).delete()
    db.delete(t)
    db.commit()


def execute_tape(db, tape_id: str, inputs: dict | None = None) -> dict:
    """异步执行编排：建 kb_tape_run + 投递 orch 调度中枢，立即返回 run_id。

    实际执行在 celery worker（orch 队列调度中枢 + 各节点队列执行）。
    前端通过 GET runs/{run_id} 轮询逐步日志。
    """
    t = get_tape(db, tape_id)
    if t.status != "effective" and t.status != "draft":
        raise ValueError(f"编排状态 {t.status} 不可执行")
    # 检查是否有已保存步骤
    step_count = db.query(TapeStep).filter(TapeStep.tape_id == tape_id).count()
    if not step_count:
        raise ValueError("编排没有可执行的步骤（请先在设计器保存）")

    run_id = uuid.uuid4().hex[:32]
    run = TapeRun(
        id=run_id,
        tape_id=tape_id,
        tape_name=t.tape_name,
        status="queued",
        inputs=inputs or {},
        step_results=[],
        bindings=inputs or {},
    )
    db.add(run)
    db.commit()

    # 投递调度中枢（orch 队列）
    try:
        from worker.celery_app import celery_app

        celery_app.send_task(
            "worker.tasks.orch_runner.execute_run",
            args=[run_id],
            queue="orch",
        )
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = f"编排任务投递失败: {exc}"
        db.commit()
        raise ValueError(f"编排任务投递失败: {exc}")

    return {"success": True, "run_id": run_id, "tape_id": tape_id, "status": "queued"}


def get_tape_run(db, run_id: str) -> TapeRun:
    run = db.get(TapeRun, run_id)
    if not run:
        raise KeyError(f"执行记录不存在: {run_id}")
    return run


def list_tape_runs(db, tape_id: str, page=1, page_size=20) -> dict:
    q = db.query(TapeRun).filter(TapeRun.tape_id == tape_id)
    total = q.count()
    items = q.order_by(TapeRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "tape_id": r.tape_id,
                "tape_name": r.tape_name,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            }
            for r in items
        ],
    }


def serialize_tape_run(run: TapeRun) -> dict:
    return {
        "id": run.id,
        "tape_id": run.tape_id,
        "tape_name": run.tape_name,
        "status": run.status,
        "inputs": run.inputs or {},
        "step_results": run.step_results or [],
        "bindings": run.bindings or {},
        "error": run.error or "",
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "updated_at": run.updated_at.isoformat() if run.updated_at else "",
    }
