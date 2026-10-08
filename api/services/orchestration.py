"""编排（Tape）管理服务——CRUD + 设计器保存同步 + 发布/下线 + 执行。"""

from __future__ import annotations

import json
import uuid

from api.models.orchestration import Tape, TapeStep
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
    """同步执行编排，返回逐步日志与结果。"""
    t = get_tape(db, tape_id)
    if t.status != "effective" and t.status != "draft":
        raise ValueError(f"编排状态 {t.status} 不可执行")
    engine = OrchestrationEngine(db)
    engine.load_tape(t)
    if not engine.step_index:
        raise ValueError("编排没有可执行的步骤（请先在设计器保存）")
    return engine.execute(t, inputs=inputs or {})
