"""应用「wiki构建-代码仓库」编排（幂等可重放，2026-10）。

代码仓库知识库的专用构建编排：代码文件解析完成后，按该编排生成代码文件
说明页（模块职责/关键类函数/依赖/用法）。数据源 = scripts/seed_tape_data/
wiki构建-代码仓库.json（与种子同源，seed_framework_db 的 _seed_tapes_from_exports
自动扫描该目录，本脚本供线上存量库手动应用/修补）。

行为：tape 不存在 → 创建（status=effective，发布态，创建代码仓库知识库时可被
选为构建引擎）；已存在 → 覆盖式刷新步骤（save_tape_design），不重复创建。

用法：
    docker cp scripts/apply_code_repo_tape.py kb-api-server:/tmp/ && \
    docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_code_repo_tape.py

注意：用户在服务编排页「保存设计/发布」会覆盖式同步回 kb_tape_step，冲掉本脚本
刷入的节点配置，重跑本脚本即恢复。
"""
from __future__ import annotations

import json
import sys
import uuid as _uuid
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

TAPE_NAME = "wiki构建-代码仓库"
SEED_FILE = Path(__file__).resolve().parent / "seed_tape_data" / "wiki构建-代码仓库.json"


def main() -> None:
    if not SEED_FILE.exists():
        print(f"[apply-code-repo] 种子文件不存在: {SEED_FILE}")
        sys.exit(1)
    doc = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    nodes = list(d.get("nodes") or [])
    edges = list(d.get("edges") or [])
    exec_params = d.get("execParams") or {}

    from api.services.orchestration import ensure_tape_category, save_tape_design

    db = get_sessionmaker()()
    try:
        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            ensure_tape_category(db, "通用", cat_type="tape")
            tape = Tape(
                id=_uuid.uuid4().hex[:32],
                tape_name=TAPE_NAME,
                tape_label=str(d.get("tapeLabel") or TAPE_NAME),
                tape_descr=str(d.get("tapeDescr") or ""),
                tape_type=str(d.get("tapeType") or "kb_build") or "kb_build",
                category="通用",
                status="effective",  # 发布态：创建代码仓库知识库时可被选为构建引擎
                nodes=nodes,
                edges=edges,
                exec_params=exec_params,
                create_user=None,
            )
            db.add(tape)
            db.commit()
            print(f"[apply-code-repo] 编排「{TAPE_NAME}」已创建 id={tape.id[:12]}… (effective)")
        else:
            print(f"[apply-code-repo] 编排「{TAPE_NAME}」已存在 id={tape.id[:12]}…，覆盖式刷新步骤")
        save_tape_design(db, tape.id, nodes, edges, exec_params)

        steps = db.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        print(f"[apply-code-repo] 步骤 {len(steps)} 个: "
              + ", ".join(f"{s.step_label}({s.step_inst})" for s in steps))
        print("[apply-code-repo] DONE：代码仓库知识库可绑定该编排构建代码文件说明页")
    finally:
        db.close()


if __name__ == "__main__":
    main()
