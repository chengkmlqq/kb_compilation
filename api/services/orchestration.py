"""编排（Tape）管理服务——CRUD + 设计器保存同步 + 发布/下线 + 执行。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from api.models.orchestration import Tape, TapeRun, TapeStep, StepDefine
from api.services.orchestration_engine import (
    ATOM_MAP,
    OrchestrationEngine,
    list_step_defines,
    create_step_define,
    update_step_define,
    delete_step_define,
    serialize_step_define,
)

EXPORT_TYPE = "kb.orchestration.draft"
EXPORT_SCHEMA_VERSION = 1

# kb_tape.tape_name 是 String(64)：超长直接落库会抛 MySQL Data too long → 500。
# 在入口截断（与 tape_label 的 [:128] 同一策略），既不 500 也不丢数据。
TAPE_NAME_MAX = 64


def _clean_name(raw, required: bool = True) -> str:
    """编排名清洗：去空白、非空校验、超长截断（列长 64，避免 MySQL 500）。"""
    name = str(raw or "").strip()
    if not name:
        if required:
            raise ValueError("编排名称不能为空")
        return ""
    if len(name) > TAPE_NAME_MAX:
        name = name[:TAPE_NAME_MAX]
    return name


def export_tape_draft(db, tape_id: str) -> dict:
    """导出编排草稿为 JSON：节点/连线/执行参数 + 基础信息（对齐 data-synth 导出语义）。"""
    t = get_tape(db, tape_id)
    payload = {
        "type": EXPORT_TYPE,
        "schemaVersion": EXPORT_SCHEMA_VERSION,
        "exportedAt": datetime.now().isoformat(),
        "draft": {
            "tapeName": t.tape_name,
            "tapeLabel": t.tape_label or "",
            "tapeDescr": t.tape_descr or "",
            "tapeType": t.tape_type or "general",
            "nodes": t.nodes or [],
            "edges": t.edges or [],
            "execParams": t.exec_params or {},
        },
    }
    return {
        "file_name": f"{t.tape_name or tape_id}_draft_export.json",
        "content": json.dumps(payload, ensure_ascii=False, indent=2),
    }


def import_tape_draft(db, tape_id: str, content: str) -> dict:
    """导入编排草稿：解析 JSON -> 校验组件存在 -> 覆盖当前草稿（不自动发布）。

    兼容导出文件（外层 draft）与裸节点结构（直接 nodes/edges/execParams）。
    """
    try:
        parsed = json.loads(content)
    except Exception:
        raise ValueError("导入文件不是合法的 JSON")
    if not isinstance(parsed, dict):
        raise ValueError("导入文件格式不正确")

    if parsed.get("type") and parsed.get("type") != EXPORT_TYPE:
        raise ValueError(f"导入文件类型不支持: {parsed.get('type')}")

    draft = parsed.get("draft") if isinstance(parsed.get("draft"), dict) else parsed
    nodes = draft.get("nodes") if isinstance(draft.get("nodes"), list) else []
    edges = draft.get("edges") if isinstance(draft.get("edges"), list) else []
    exec_params = draft.get("execParams")
    if not isinstance(exec_params, dict):
        exec_params = {}

    # 组件存在性校验：有效 step-define（effective）或引擎内置原子
    enabled = {s.step_inst for s in db.query(StepDefine).filter(StepDefine.status == "effective").all()}
    enabled |= set(ATOM_MAP.keys())
    unknown: list[str] = []
    for n in nodes:
        data = n.get("data") if isinstance(n, dict) else None
        inst = (data or {}).get("stepInst") if isinstance(data, dict) else None
        if inst and inst not in enabled:
            unknown.append(str(inst))
    if unknown:
        raise ValueError(f"导入文件包含当前环境不存在的组件: {', '.join(sorted(set(unknown)))}")

    save_tape_design(db, tape_id, nodes, edges, exec_params)
    return {"imported_step_count": len(nodes), "overwrite": True}


def _tape_payload_to_model(obj: Tape, payload: dict) -> None:
    """把可更新字段写入模型。tape_name 走 _clean_name（截断防超列长）。

    2026-10-09 修复：原先直接 setattr(payload[field])，超长 tape_name/
    tape_label 会原样落库 → MySQL Data too long → 500。
    """
    if "tape_name" in payload:
        obj.tape_name = _clean_name(payload["tape_name"], required=True)
    if "tape_label" in payload:
        label = str(payload["tape_label"] or "").strip()
        obj.tape_label = label[:128]
    if "tape_descr" in payload:
        descr = str(payload["tape_descr"] or "").strip()
        obj.tape_descr = descr[:2000] or None
    if "tape_type" in payload:
        obj.tape_type = str(payload["tape_type"] or "general") or "general"


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
    name = _clean_name(payload.get("tape_name"), required=True)
    t = Tape(
        id=uuid.uuid4().hex[:64],
        tape_name=name,
        tape_label=str(payload.get("tape_label") or name)[:128] or None,
        tape_descr=str(payload.get("tape_descr") or "").strip()[:2000] or None,
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
    _tape_payload_to_model(t, payload)  # 内含 tape_name 清洗/截断
    if "nodes" in payload:
        if payload["nodes"] is not None and not isinstance(payload["nodes"], list):
            raise ValueError("nodes 必须是数组")
        t.nodes = payload["nodes"] or []
    if "edges" in payload:
        if payload["edges"] is not None and not isinstance(payload["edges"], list):
            raise ValueError("edges 必须是数组")
        t.edges = payload["edges"] or []
    if "exec_params" in payload:
        if payload["exec_params"] is not None and not isinstance(payload["exec_params"], dict):
            raise ValueError("exec_params 必须是对象")
        t.exec_params = payload["exec_params"] or {}
    db.commit()
    db.refresh(t)
    return t


def save_tape_design(db, tape_id: str, nodes: list, edges: list, exec_params: dict | None = None) -> Tape:
    """设计器保存：写主表 nodes/edges，并覆盖式同步 kb_tape_step 子表。"""
    t = get_tape(db, tape_id)
    # 类型守卫：非 list 的 nodes/edges 直接落库会在后续迭代时抛 500。
    if nodes is None:
        nodes = []
    if edges is None:
        edges = []
    if not isinstance(nodes, list):
        raise ValueError("nodes 必须是数组")
    if not isinstance(edges, list):
        raise ValueError("edges 必须是数组")
    t.nodes = nodes or []
    t.edges = edges or []
    if exec_params is not None:
        t.exec_params = exec_params or {}
    db.commit()

    # 覆盖式同步步骤子表
    db.query(TapeStep).filter(TapeStep.tape_id == tape_id).delete()
    db.commit()
    node_by_id = {n.get("id", ""): n for n in (nodes or [])}
    # 2026-10-09 实测坑: kb_tape_step.id 是全局主键,node id(如 n1/n2/n3)只
    # 在编排内唯一——两个编排用相同 node id 保存 → Duplicate entry 500。
    # 步骤行 id 加 tape 前缀保证全表唯一,pre/next 引用同步重映射。
    def _step_id(nid: str) -> str:
        return f"{tape_id[:12]}::{nid}" if nid else ""

    # edges: source->target 与 target->source 两个方向都建（pre/next 依赖）
    next_map: dict[str, list[str]] = {}
    pre_map: dict[str, list[str]] = {}
    for e in edges or []:
        src, tgt = e.get("source"), e.get("target")
        if not src or not tgt:
            continue
        _src, _tgt = _step_id(src), _step_id(tgt)
        next_map.setdefault(_src, []).append(_tgt)
        pre_map.setdefault(_tgt, []).append(_src)
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
                id=_step_id(nid) or uuid.uuid4().hex[:64],
                tape_id=tape_id,
                step_inst=step_inst,
                step_label=str(data.get("label") or data.get("stepLabel") or step_inst)[:128],
                step_config=data.get("config") or {},
                step_seq=seq,
                pre_step_ids=pre_map.get(_step_id(nid), []),
                next_step_ids=next_map.get(_step_id(nid), []),
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
    # 编排不存在 → KeyError → router _guarded 转 404（与其它编排端点一致）。
    get_tape(db, tape_id)
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
    # 过滤内部迭代状态（loop 调度状态借 bindings 内层承载，不属业务变量）。
    bindings = {k: v for k, v in (run.bindings or {}).items()
                if not str(k).startswith("__")}
    return {
        "id": run.id,
        "tape_id": run.tape_id,
        "tape_name": run.tape_name,
        "status": run.status,
        "inputs": run.inputs or {},
        "step_results": run.step_results or [],
        "bindings": bindings,
        "error": run.error or "",
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "updated_at": run.updated_at.isoformat() if run.updated_at else "",
    }
