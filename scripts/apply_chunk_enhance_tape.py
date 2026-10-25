#!/usr/bin/env python3
"""把「切片智能增强」编排灌入线上库（幂等可重放）。

编排种子真源 = scripts/seed_tape_data/切片智能增强.json（tape_name=切片智能增强，
tape_type=chunk_enhance，category=知识聚合）。本脚本按 tape_name 幂等建编排；
编排已存在则跳过（不覆盖线上设计）。

该编排不建定时任务——触发由文档解析链头（worker/tasks/doc_process.py 的
_enqueue_chunk_enhance_run）在文档解析 READY 后按 KB/文件增强配置触发。

用法（容器内，需 scripts/ 在 PYTHONPATH）：
  docker cp scripts/apply_chunk_enhance_tape.py kb-api-server:/tmp/
  docker cp scripts/seed_tape_data/切片智能增强.json kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_chunk_enhance_tape.py
环境：DATABASE_URL（compose 已注入）。幂等：重复执行不产生重复编排。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/srv/kb/scripts")

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape
from api.services.orchestration import ensure_tape_category, save_tape_design

TAPE_NAME = "切片智能增强"
SEED_FILE = "/tmp/切片智能增强.json"  # docker cp 的目标路径


def _restore_tape_steps(db, tape_id: str, steps: list) -> None:
    """按种子执行层 steps 原样重建 kb_tape_step（id 前缀重映射为 tape_id[:12]）。"""
    from api.models.orchestration import TapeStep

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


def main() -> None:
    src = Path(SEED_FILE)
    if not src.is_file():
        print(f"[apply-chunk-enhance] 种子文件不存在: {SEED_FILE}（先 docker cp seed_tape_data/切片智能增强.json）")
        return
    doc = json.loads(src.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    db = get_sessionmaker()()
    try:
        existing = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if existing:
            # 2026-10-26 幂等升级：已存在时按种子对齐执行层（queue_name + step_config），
            # nodes/edges 设计内容不动（防覆盖线上画布调整）。step_config 以种子为真源
            # ——种子修复（如 n1 补 doc_extract_prompt）须能经 apply 推送到线上生效。
            steps = d.get("steps") or []
            if steps:
                from api.models.orchestration import TapeStep

                prefix = existing.id[:12]
                changed = 0
                for st in steps:
                    bare = str(st.get("step_id") or "").rsplit("::", 1)[-1]
                    sid = f"{prefix}::{bare}"
                    row = db.execute(
                        select(TapeStep).where(TapeStep.tape_id == existing.id, TapeStep.id == sid)
                    ).scalars().first()
                    if row is not None:
                        new_q = str(st.get("queue_name") or "")
                        new_cfg = st.get("step_config") or {}
                        if (row.queue_name or "") != new_q or (row.step_config or {}) != new_cfg:
                            row.queue_name = new_q
                            row.step_config = new_cfg
                            changed += 1
                if changed:
                    db.commit()
                    print(f"[apply-chunk-enhance] 编排「{TAPE_NAME}」已存在，对齐 {changed} 个步骤 (queue_name+step_config)")
                else:
                    print(f"[apply-chunk-enhance] 编排「{TAPE_NAME}」已存在且执行层一致 (skip) id={existing.id[:12]}…")
            else:
                print(f"[apply-chunk-enhance] 编排「{TAPE_NAME}」已存在 (skip) id={existing.id[:12]}…")
            return
        nodes = list(d.get("nodes") or [])
        edges = list(d.get("edges") or [])
        exec_params = d.get("execParams")
        category = str(d.get("category") or "通用").strip() or "通用"
        ensure_tape_category(db, category, cat_type="tape")
        tape = Tape(
            id=uuid.uuid4().hex[:32],
            tape_name=TAPE_NAME,
            tape_label=str(d.get("tapeLabel") or TAPE_NAME),
            tape_descr=str(d.get("tapeDescr") or ""),
            tape_type=str(d.get("tapeType") or "general") or "general",
            category=category,
            status=str(d.get("status") or "effective") or "effective",
            nodes=nodes,
            edges=edges,
            exec_params=exec_params,
            create_user=None,
        )
        db.add(tape)
        db.commit()
        steps = d.get("steps") or []
        if steps:
            _restore_tape_steps(db, tape.id, steps)
        else:
            save_tape_design(db, tape.id, nodes, edges, exec_params)
        print(
            f"[apply-chunk-enhance] 编排「{TAPE_NAME}」新建 OK id={tape.id[:12]}… "
            f"节点 {len(nodes)} 边 {len(edges)} 步骤 {len(steps)}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()