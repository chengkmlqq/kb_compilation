#!/usr/bin/env python3
"""把两个知识聚合编排（制度版本演进 / 聚类知识-主题聚合）从 seed_tape_data 重新同步到线上库。

背景：commit 801cadf 新增「制度版本演进」编排种子后未入库（库中 kb_tape 无此条）；
「聚类知识-主题聚合」虽已在库，但 67f138a 通用化改造（语料收集通用化 + 综述 4 要素
模板 + 双链校验）后的新版 nodes/steps 未同步——库中 seq0/seq2 step_config 仍为旧版。

同步策略（幂等，可重复执行）：
  - 制度版本演进   不存在 → 新建 Tape + kb_tape_step（按导出 steps 原样重建）
  - 聚类知识-主题聚合 已存在 → 保留 tape id，覆盖更新 label/descr/type/status/category/
    nodes/edges/exec_params + 重建 kb_tape_step（同 _seed_tapes_from_exports 的
    _restore_tape_steps 路径，执行层与画布都以文件为准）

用法（容器内）：
  docker cp scripts/apply_sync_two_tapes.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_sync_two_tapes.py
环境：DATABASE_URL（compose 已注入）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/srv/kb/scripts")

from sqlalchemy import select  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.orchestration import Tape, TapeStep  # noqa: E402
from api.services.orchestration import ensure_tape_category  # noqa: E402

DATA_DIR = Path("/srv/kb/scripts/seed_tape_data")
TAPES = ["制度版本演进", "聚类知识-主题聚合"]


def _load(doc: dict) -> dict:
    return doc.get("draft") or doc


def _restore_tape_steps(db, tape_id: str, steps: list) -> None:
    """按导出执行层 steps 原样重建 kb_tape_step（与 seed_framework_db._restore_tape_steps 一致）。"""
    prefix = tape_id[:12]

    def _sid(nid) -> str:
        bare = str(nid or "").rsplit("::", 1)[-1]
        return f"{prefix}::{bare}" if bare else ""

    db.query(TapeStep).filter(TapeStep.tape_id == tape_id).delete()
    db.commit()
    for st in sorted(steps, key=lambda s: int(s.get("step_seq") or 0)):
        sid = _sid(st.get("step_id") or "")
        db.add(
            TapeStep(
                id=sid or uuid.uuid4().hex[:64],
                tape_id=tape_id,
                step_inst=str(st.get("step_inst") or "def"),
                step_label=str(st.get("step_label") or "")[:128],
                step_config=st.get("step_config") or {},
                step_seq=int(st.get("step_seq") or 0),
                pre_step_ids=[x for x in (_sid(i) for i in (st.get("pre_step_ids") or [])) if x],
                next_step_ids=[x for x in (_sid(i) for i in (st.get("next_step_ids") or [])) if x],
                queue_name=str(st.get("queue_name") or ""),
            )
        )
    db.commit()


def _sync_one(db, name: str) -> None:
    fp = DATA_DIR / f"{name}.json"
    if not fp.is_file():
        print(f"[sync] 缺种子文件 {fp.name}，跳过")
        return
    d = _load(json.loads(fp.read_text(encoding="utf-8")))
    tape_name = str(d.get("tapeName") or name).strip()
    nodes = list(d.get("nodes") or [])
    edges = list(d.get("edges") or [])
    exec_params = d.get("execParams")
    steps = list(d.get("steps") or [])
    category = str(d.get("category") or "").strip() or "通用"
    ensure_tape_category(db, category, cat_type="tape")

    tape = db.execute(select(Tape).where(Tape.tape_name == tape_name)).scalars().first()
    if tape is None:
        tape = Tape(
            id=uuid.uuid4().hex[:32],
            tape_name=tape_name,
            tape_label=str(d.get("tapeLabel") or tape_name),
            tape_descr=str(d.get("tapeDescr") or ""),
            tape_type=str(d.get("tapeType") or "general") or "general",
            category=category,
            status=str(d.get("status") or "draft") or "draft",
            nodes=nodes,
            edges=edges,
            exec_params=exec_params,
            create_user=None,
        )
        db.add(tape)
        db.commit()
        action = "新建"
    else:
        old_n = len(tape.nodes or [])
        tape.tape_label = str(d.get("tapeLabel") or tape_name)
        tape.tape_descr = str(d.get("tapeDescr") or "")
        tape.tape_type = str(d.get("tapeType") or "general") or "general"
        tape.category = category
        tape.status = str(d.get("status") or "draft") or "draft"
        tape.nodes = nodes
        tape.edges = edges
        tape.exec_params = exec_params
        db.commit()
        action = f"覆盖(节点 {old_n} -> {len(nodes)})"

    if steps:
        _restore_tape_steps(db, tape.id, steps)
        step_n = len(steps)
    else:
        from api.services.orchestration import save_tape_design  # noqa: E402

        save_tape_design(db, tape.id, nodes, edges, exec_params)
        step_n = len(nodes)
    db.refresh(tape)
    node_n = len(tape.nodes or [])
    edge_n = len(tape.edges or [])
    print(
        f"[sync] 「{tape.tape_name}」{action}: id={tape.id[:12]}… "
        f"节点 {node_n} 边 {edge_n} 步骤 {step_n} "
        f"status={tape.status} category={tape.category}"
    )


def main() -> None:
    db = get_sessionmaker()()
    try:
        for name in TAPES:
            _sync_one(db, name)
    finally:
        db.close()


if __name__ == "__main__":
    main()
