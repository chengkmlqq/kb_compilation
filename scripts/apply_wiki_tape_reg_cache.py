#!/usr/bin/env python3
"""把线上 wiki构建-* 编排重建为 reg_cache（MinIO 中间产物）版（2026-10-27）。

背景：wiki构建-* 编排节点此前用容器本地 /tmp/reg_<kid8>/ 传中间产物，
容器重建即丢失 → 在途 run 批量 FileNotFoundError。种子已由
migrate_wiki_tape_reg_cache.py 改写为 reg_cache（MinIO）调用，本脚本把
线上 tape 定义（nodes/edges/steps/exec_params）与迁移后种子对齐。

数据真源：scripts/seed_tape_data/wiki构建-*.json（nodes + steps 双份）。
本脚本保持 tape id / name / category / status 不变，覆盖 nodes / edges /
exec_params / label / descr，并按导出 steps 重建 kb_tape_step（执行层）。
幂等可重放。已存在在途 run 不受影响（step id 前缀=tape_id[:12] 不变，
拓扑 pre/next 不变，仅 step_config.code 换新）。

用法（容器内，api-server 或 worker 容器均可）：
  docker cp scripts/apply_wiki_tape_reg_cache.py kb-api-server:/tmp/
  docker cp -r scripts/seed_tape_data kb-api-server:/tmp/seed_tape_data
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_wiki_tape_reg_cache.py
环境：DATABASE_URL（compose 已注入）。默认 prod 正式表。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/srv/kb/scripts")

from sqlalchemy import select  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.orchestration import Tape  # noqa: E402
from seed_framework_db import _restore_tape_steps  # noqa: E402

# 本地直跑兜底（未 docker cp 到 /srv/kb 时）：
_LOCAL_SEED_DIR = Path(__file__).resolve().parent / "seed_tape_data"


def _seed_dir() -> Path:
    p = Path("/srv/kb/scripts/seed_tape_data")
    if p.is_dir():
        return p
    return _LOCAL_SEED_DIR


def main() -> None:
    seed_dir = _seed_dir()
    seeds = sorted(seed_dir.glob("wiki构建-*.json"))
    if not seeds:
        print(f"[apply-wiki-rc] 未找到任何 wiki构建 种子（{seed_dir}）")
        return
    db = get_sessionmaker()()
    try:
        total = 0
        for sf in seeds:
            doc = json.loads(sf.read_text(encoding="utf-8"))
            d = doc.get("draft") or doc
            tname = str(d.get("tapeName") or "").strip()
            if not tname:
                print(f"[apply-wiki-rc] 跳过（无 tapeName）: {sf.name}")
                continue
            tape = db.execute(select(Tape).where(Tape.tape_name == tname)).scalars().first()
            if tape is None:
                print(f"[apply-wiki-rc] 未找到线上编排「{tname}」，跳过（先跑 seed 播种）")
                continue
            old_n = len(tape.nodes or [])
            tape.tape_label = str(d.get("tapeLabel") or tname)
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
            has_rc = any(
                "reg-cache" in json.dumps((s.get("step_config") or {}), ensure_ascii=False)
                for s in steps
            )
            print(
                f"[apply-wiki-rc] 「{tname}」重建完成: 节点 {old_n} -> {len(tape.nodes)} "
                f"边 {len(tape.edges)} 步骤 {len(steps)} reg_cache={has_rc} id={tape.id[:12]}…"
            )
            total += 1
        print(f"[apply-wiki-rc] 完成：共重建 {total} 条 wiki构建 编排")
    finally:
        db.close()


if __name__ == "__main__":
    main()