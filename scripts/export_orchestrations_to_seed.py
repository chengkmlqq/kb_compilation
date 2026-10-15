#!/usr/bin/env python3
"""把线上编排管理的全部编排数据导出为种子 JSON（幂等可重放，2026-10-17）。

用途：仓库 scripts/seed_tape_data/*.json 是系统编排种子数据的真源（部署后由
seed_framework_db._seed_tapes_from_exports 播种）。本脚本把线上库（编排管理页
维护的 kb_tape + kb_tape_step）导出为该目录的 JSON，使种子与线上编排数据一致。

导出内容（比 API export 端点多执行层）：
  - 元信息：tape_name / tape_label / tape_descr / tape_type / category / status
  - 画布：nodes / edges（Tape.nodes/edges JSON）
  - 执行参数：execParams（Tape.exec_params）
  - 执行层步骤：steps[]（kb_tape_step 逐行：step_inst/step_label/step_config/
    step_seq/pre_step_ids/next_step_ids/queue_name）——执行引擎读取的是这层，
    API 的 export 端点不暴露；画布 nodes 与 steps 历史上可能脱节（2026-10-15
    实测三个摘要编排画布还是远古版），因此必须把 steps 一并固化，播种时
    _seed_tapes_from_exports 直接按 steps 重建执行层，不依赖画布推导。

⚠️ 字段保真策略：
  - category / status 直接取线上库真值。
  - tape_type 线上库曾是全 general（3de3c25 功能标签打标晚于存量编排导入，
    未落实到线上库）；导出时按命名规则补功能标签（wiki构建-*/表构建Wiki-*
    → kb_build，摘要-* → summary，其余 general），与 seed 既有语义一致。

用法（容器内，遵循仓库 apply 脚本惯例）：
  docker cp scripts/export_orchestrations_to_seed.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/export_orchestrations_to_seed.py
环境：DATABASE_URL（compose 已注入）。--dir 指定输出目录（默认仓库 scripts/seed_tape_data）。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

EXPORT_TYPE = "kb.orchestration.draft"
SCHEMA_VERSION = 1


def func_tape_type(name: str, live_type: str) -> str:
    """功能标签：线上库 tape_type 曾全 general（打标未落实），按命名规则补回。"""
    if name.startswith("wiki构建-") or name.startswith("表构建Wiki"):
        return "kb_build"
    if name.startswith("摘要-"):
        return "summary"
    return live_type or "general"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="", help="输出目录（默认 scripts/seed_tape_data，相对仓库根或绝对路径）")
    args = ap.parse_args()

    out_dir = args.dir or str(Path(__file__).resolve().parent / "seed_tape_data")
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    db = get_sessionmaker()()
    try:
        tapes = db.execute(select(Tape).order_by(Tape.tape_name)).scalars().all()
        print(f"线上编排总数: {len(tapes)}")
        for t in tapes:
            steps = db.execute(
                select(TapeStep).where(TapeStep.tape_id == t.id).order_by(TapeStep.step_seq)
            ).scalars().all()
            draft = {
                "tapeName": t.tape_name,
                "tapeLabel": t.tape_label or "",
                "tapeDescr": t.tape_descr or "",
                "tapeType": func_tape_type(t.tape_name, t.tape_type or "general"),
                "category": t.category or "通用",
                "status": t.status or "draft",
                "nodes": t.nodes or [],
                "edges": t.edges or [],
                "execParams": t.exec_params or {},
                "steps": [
                    {
                        "step_id": st.id.rsplit("::", 1)[-1],
                        "step_inst": st.step_inst,
                        "step_label": st.step_label or "",
                        "step_config": st.step_config or {},
                        "step_seq": st.step_seq,
                        "pre_step_ids": st.pre_step_ids or [],
                        "next_step_ids": st.next_step_ids or [],
                        "queue_name": st.queue_name or "",
                    }
                    for st in steps
                ],
            }
            doc = {
                "type": EXPORT_TYPE,
                "schemaVersion": SCHEMA_VERSION,
                "exportedAt": datetime.now().isoformat(),
                "draft": draft,
            }
            fname = t.tape_name.replace("/", "_").replace("\\", "_")
            fp = Path(out_dir) / f"{fname}.json"
            fp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {t.tape_name}: {len(draft['nodes'])} 节点 / {len(steps)} 执行步骤 → {fp.name}")
    finally:
        db.close()
    print("done")


if __name__ == "__main__":
    main()
