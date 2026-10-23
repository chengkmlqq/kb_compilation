"""应用「索引wiki创建」编排到线上存量库（幂等可重放，2026-10-22）。

语义：从已索引（解析入库、有切片）的文档创建 wiki 的通用编排，不依赖领域
本体 Schema。执行体复用 run_one.py（gen_summary 摘要 + build_full
长句/关键词/实体/规则/图谱/合并/链接）。数据源 = scripts/seed_tape_data/
索引wiki创建.json（与种子同源；seed_framework_db 的 _seed_tapes_from_exports
自动扫描该目录，本脚本供线上存量库手动应用/修补）。

行为：tape 不存在 → 创建（status=effective，发布态，知识库创建时可被选为
构建引擎）；已存在 → 跳过（与种子语义一致，不覆盖线上设计）。

用法：
    docker cp scripts/apply_index_wiki_tape.py kb-api-server:/tmp/ && \
    docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_index_wiki_tape.py

注意：用户在服务编排页「保存设计/发布」会覆盖式同步回 kb_tape_step，冲掉本
脚本刷入的节点配置，重跑本脚本即恢复。
"""
from __future__ import annotations

import json
import sys
import uuid as _uuid
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

TAPE_NAME = "索引wiki创建"
SEED_FILE = Path(__file__).resolve().parent / "seed_tape_data" / "索引wiki创建.json"


def _restore_steps(db, tape_id: str, steps: list) -> None:
    """按导出的执行层 steps 原样重建 kb_tape_step（对齐 seed 的 _restore_tape_steps）。"""
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
                id=sid or _uuid.uuid4().hex[:64],
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
    if not SEED_FILE.exists():
        print(f"[apply-index-wiki] 种子文件不存在: {SEED_FILE}")
        sys.exit(1)
    doc = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    nodes = list(d.get("nodes") or [])
    edges = list(d.get("edges") or [])
    exec_params = d.get("execParams") or {}
    steps = list(d.get("steps") or [])

    from api.services.orchestration import ensure_tape_category

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
                status=str(d.get("status") or "effective") or "effective",
                nodes=nodes,
                edges=edges,
                exec_params=exec_params,
                create_user=None,
            )
            db.add(tape)
            db.commit()
            print(f"[apply-index-wiki] 编排「{TAPE_NAME}」已创建 id={tape.id[:12]}… ({tape.status})")
        else:
            print(f"[apply-index-wiki] 编排「{TAPE_NAME}」已存在 id={tape.id[:12]}…，跳过（不覆盖）")

        if steps:
            _restore_steps(db, tape.id, steps)
            print(f"[apply-index-wiki] 执行层步骤已重建: {len(steps)} 个")
        else:
            print("[apply-index-wiki] JSON 无 steps 字段（应由画布推导），请检查种子文件")

        count = db.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        print(
            "[apply-index-wiki] 步骤: "
            + ", ".join(f"{s.step_label}({s.step_inst})" for s in count)
        )
        print("[apply-index-wiki] DONE：知识库可绑定该编排从已索引文档创建 wiki")
    finally:
        db.close()


if __name__ == "__main__":
    main()
