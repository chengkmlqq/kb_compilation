#!/usr/bin/env python3
"""把线上「切片评估」编排重建为 v3 版（2026-10-23）。

v3 改动（单文件粒度 + Wiki 对照原文评估）：
  - 评估对象从「切片自身质量」改为「该文档构建出的 Wiki 页面」：LLM 对照文档
    完整原文（切片全文）检查 ① 内容错误 ② 内容缺失 ③ Wiki 是否有意义；
  - 提取每文件 Wiki 构建起始时间与耗时（优先 kb_tape_run 编排 run 记录，
    回落 wiki 页创建时间窗），随 per_doc 落库供切片评估页面展示；
  - 新增问题类型：wiki_no_pages / wiki_content_error / wiki_missing_content /
    wiki_not_meaningful（保留 no_chunks / sem_eval_error）。

数据真源：scripts/seed_tape_data/切片评估.json（nodes + steps 双份已同步）。
本脚本保持 tape id / name / category / status 不变，覆盖 nodes / edges /
exec_params / label / descr，并按导出 steps 重建 kb_tape_step（执行层）。
幂等可重放。

用法（容器内，api-server 或 worker 容器均可）：
  docker cp scripts/apply_chunk_eval_tape_v3.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_chunk_eval_tape_v3.py
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

TAPE_NAME = "切片评估"
SEED_FILE = Path("/srv/kb/scripts/seed_tape_data/切片评估.json")
# 本地直跑兜底（未 docker cp 到 /srv/kb 时）：
_LOCAL_SEED = Path(__file__).resolve().parent / "seed_tape_data" / "切片评估.json"


def main() -> None:
    seed = SEED_FILE if SEED_FILE.exists() else _LOCAL_SEED
    doc = json.loads(seed.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    assert str(d.get("tapeName") or "").strip() == TAPE_NAME, d.get("tapeName")

    db = get_sessionmaker()()
    try:
        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            print(f"[apply-chunk-eval-v3] 未找到编排「{TAPE_NAME}」，中止（先跑 seed_framework_db.py 播种）")
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
            f"[apply-chunk-eval-v3] 「{tape.tape_name}」重建完成: "
            f"节点 {old_n} -> {len(tape.nodes)} 边 {len(tape.edges)} 步骤 {len(steps)} "
            f"status={tape.status} id={tape.id}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
