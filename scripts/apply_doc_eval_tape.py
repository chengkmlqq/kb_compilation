#!/usr/bin/env python3
"""把线上「文档转换评估」编排重建为单文件粒度版（2026-10-23）。

改造内容（dim=doc）：
  - 评估粒度从整库汇总改为单文件明细：per_doc 每文件 文件名/状态/引擎/耗时/乱码/得分
  - 乱码检测（纯程序，无 LLM）：U+FFFD 替换符 / 非法代理区 / 编码错位特征词 / 控制字符
  - 转换引擎：doc_chunk.meta.parse_engine（兼容 dict / JSON 字符串）
  - 转换耗时：modo_job 最近一次解析任务 duration_ms
  - 汇总分 = 平均单文件分；engines / avg_duration_ms / by_state / per_doc 落 metrics

数据真源：scripts/seed_tape_data/文档转换评估.json（nodes + steps 双份已同步）。
本脚本保持 tape id / name / category / status 不变，覆盖 nodes / edges /
exec_params / label / descr，并按导出 steps 重建 kb_tape_step（执行层）。
幂等可重放。

用法（容器内，api-server 或 worker 容器均可）：
  docker cp scripts/apply_doc_eval_tape.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_doc_eval_tape.py
环境：DATABASE_URL（compose 已注入）。默认 prod 正式表。
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

TAPE_NAME = "文档转换评估"
SEED_FILE = Path("/srv/kb/scripts/seed_tape_data/文档转换评估.json")
# 本地直跑兜底（未 docker cp 到 /srv/kb 时）：
_LOCAL_SEED = Path(__file__).resolve().parent / "seed_tape_data" / "文档转换评估.json"


def main() -> None:
    seed = SEED_FILE if SEED_FILE.exists() else _LOCAL_SEED
    doc = json.loads(seed.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    assert str(d.get("tapeName") or "").strip() == TAPE_NAME, d.get("tapeName")

    db = get_sessionmaker()()
    try:
        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            print(f"[apply-doc-eval] 未找到编排「{TAPE_NAME}」，中止（先跑 seed_framework_db.py 播种）")
            return
        old_n = len(tape.nodes or [])
        tape.tape_label = str(d.get("tapeLabel") or TAPE_NAME)
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
            f"[apply-doc-eval] 「{tape.tape_name}」重建完成: "
            f"节点 {old_n} -> {len(tape.nodes)} 边 {len(tape.edges)} 步骤 {len(steps)} "
            f"status={tape.status} id={tape.id}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
