#!/usr/bin/env python3
"""通用编排 seed 应用器（2026-10-23）：把 seed_tape_data/<name>.json 重建到线上 kb_tape。

与 apply_doc_eval_tape.py / apply_chunk_eval_tape_v3.py 同语义：
保持 tape id/name/category/status 不变，覆盖 nodes/edges/exec_params/label/descr，
并按导出 steps 重建 kb_tape_step（执行层）。幂等可重放。

用法（容器内 api-server 或 worker）：
  docker cp scripts/apply_quality_tape.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_quality_tape.py <tape_name> [<tape_name> ...]
  例：docker exec ... /tmp/apply_quality_tape.py 文档转换评估 切片评估 图数据评估 质量评估-QA
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/srv/kb/scripts")

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape
from seed_framework_db import _restore_tape_steps  # noqa: E402

_LOCAL_SEED_DIR = Path("/srv/kb/scripts/seed_tape_data")
_FALLBACK_DIR = Path(__file__).resolve().parent / "seed_tape_data"


def apply_one(db, tape_name: str) -> None:
    seed = _LOCAL_SEED_DIR / f"{tape_name}.json"
    if not seed.exists():
        seed = _FALLBACK_DIR / f"{tape_name}.json"
    doc = json.loads(seed.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    assert str(d.get("tapeName") or "").strip() == tape_name, d.get("tapeName")

    tape = db.execute(select(Tape).where(Tape.tape_name == tape_name)).scalars().first()
    if tape is None:
        print(f"[apply-tape] 未找到编排「{tape_name}」，跳过（先跑 seed_framework_db.py 播种）")
        return
    old_n = len(tape.nodes or [])
    tape.tape_label = str(d.get("tapeLabel") or tape_name)
    tape.tape_descr = str(d.get("tapeDescr") or "")
    tape.tape_type = str(d.get("tapeType") or "general") or "general"
    tape.nodes = list(d.get("nodes") or [])
    tape.edges = list(d.get("edges") or [])
    tape.exec_params = d.get("execParams")
    steps = d.get("steps") or []
    if steps:
        _restore_tape_steps(db, tape.id, steps)
    else:
        from api.services.orchestration import save_tape_design

        save_tape_design(db, tape.id, tape.nodes, tape.edges, tape.exec_params)
    db.commit()
    db.refresh(tape)
    print(
        f"[apply-tape] 「{tape.tape_name}」重建完成: "
        f"节点 {old_n} -> {len(tape.nodes)} 边 {len(tape.edges)} 步骤 {len(steps)} "
        f"status={tape.status} id={tape.id}"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    db = get_sessionmaker()()
    try:
        for name in sys.argv[1:]:
            apply_one(db, name)
    finally:
        db.close()


if __name__ == "__main__":
    main()