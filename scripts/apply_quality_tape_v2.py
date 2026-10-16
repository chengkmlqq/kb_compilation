#!/usr/bin/env python3
"""把线上「质量评估-QA」编排重建为 v2 语义评估版（2026-10-16）。

背景：commit 7f8aaae（feat(quality): 评估逻辑编排化+语义评估（v2））把评估逻辑
从 worker/builtin_engine/code_rev_src/quality_*.py 脚本迁移到编排节点 code
（9 节点：def + 3 程序性内联 + 3 语义 LLM + 汇总 + print），并删除了 4 个脚本。
但线上库 kb_tape「质量评估-QA」仍是 v1（6 节点、节点 code 调 quality_*.py 脚本，
脚本已删 → 执行必 ModuleNotFoundError）。

本脚本从 seed_framework_db.QUALITY_TAPE（v2 常量）重建线上编排：
  - 保持 tape id / tape_name / label / descr / type / category / status 不变
  - 用 save_tape_design 覆盖式重建 nodes / edges / exec_params + kb_tape_step（9 步）
幂等可重放：docker cp 进 kb-api-server 执行 /srv/kb/.venv/bin/python。

用法（容器内）：
  docker cp scripts/apply_quality_tape_v2.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_quality_tape_v2.py
环境：DATABASE_URL（compose 已注入）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/srv/kb/scripts")

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape
from api.services.orchestration import save_tape_design

# v2 常量真源（容器内 /srv/kb/scripts/seed_framework_db.py 已含 7f8aaae 后的 QUALITY_TAPE）
from seed_framework_db import QUALITY_TAPE  # noqa: E402

TAPE_NAME = "质量评估-QA"


def main() -> None:
    db = get_sessionmaker()()
    try:
        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            print(f"[apply-quality-v2] 未找到编排「{TAPE_NAME}」，中止")
            return
        old_n = len(tape.nodes or [])
        save_tape_design(
            db,
            tape.id,
            QUALITY_TAPE["nodes"],
            QUALITY_TAPE["edges"],
            QUALITY_TAPE.get("exec_params") or {},
        )
        db.refresh(tape)
        print(
            f"[apply-quality-v2] 「{tape.tape_name}」重建完成: "
            f"节点 {old_n} -> {len(tape.nodes)} 边 {len(tape.edges)} "
            f"exec_params={tape.exec_params} status={tape.status} id={tape.id}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
