#!/usr/bin/env python3
"""把「wiki反馈修正」编排 + 每日定时任务灌入线上库（幂等可重放）。

编排种子真源 = scripts/seed_tape_data/wiki反馈修正.json（tape_name=wiki反馈修正，
tape_type=feedback_fix，category=知识维护）。本脚本按 tape_name 幂等建编排；
编排已存在则跳过（不覆盖线上设计）。

定时任务（modo_cron_task，KbFeedbackFixTask）：每天 02:00 触发一次，扫描全库
待处理反馈并逐库执行「wiki反馈修正」编排（env=prod）。cron 行按 task_class
幂等 upsert（存在则对齐 cron_expression/queue_name/fire_params，不重复插入）。

用法（容器内，需 scripts/ 在 PYTHONPATH）：
  docker cp scripts/apply_feedback_fix_tape.py kb-api-server:/tmp/
  docker cp scripts/seed_tape_data/wiki反馈修正.json kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_feedback_fix_tape.py
环境：DATABASE_URL（compose 已注入）。幂等：重复执行不产生重复编排/重复 cron。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/srv/kb/scripts")

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.framework import CronTask
from api.models.orchestration import Tape
from api.services.orchestration import ensure_tape_category, save_tape_design

TAPE_NAME = "wiki反馈修正"
CRON_TASK_CLASS = "KbFeedbackFixTask"
CRON_EXPRESSION = "0 2 * * *"  # 每天 02:00
SEED_FILE = "/tmp/wiki反馈修正.json"  # docker cp 的目标路径


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


def _seed_tape(db) -> bool:
    """编排按 tape_name 幂等建；返回是否新建。"""
    src = Path(SEED_FILE)
    if not src.is_file():
        print(f"[apply-fbfix] 种子文件不存在: {SEED_FILE}（先 docker cp seed_tape_data/wiki反馈修正.json）")
        return False
    doc = json.loads(src.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    existing = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
    if existing:
        print(f"[apply-fbfix] 编排「{TAPE_NAME}」已存在 (skip) id={existing.id[:12]}…")
        return False
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
    print(f"[apply-fbfix] 编排「{TAPE_NAME}」新建 OK id={tape.id[:12]}… 节点 {len(nodes)} 边 {len(edges)} 步骤 {len(steps)}")
    return True


def _seed_cron(db) -> bool:
    """每日定时任务按 task_class 幂等 upsert；返回 True 表示本次写入/调整。"""
    existing = db.execute(
        select(CronTask).where(CronTask.task_class == CRON_TASK_CLASS)
    ).scalars().first()
    fire_params = json.dumps({"dry_run": False}, ensure_ascii=False)
    if existing:
        changed = False
        if (existing.cron_expression or "") != CRON_EXPRESSION:
            existing.cron_expression = CRON_EXPRESSION
            changed = True
        if (existing.queue_name or "") != "default":
            existing.queue_name = "default"
            changed = True
        if (existing.fire_params or "") != fire_params:
            existing.fire_params = fire_params
            changed = True
        db.commit()
        print(
            f"[apply-fbfix] 定时任务 {CRON_TASK_CLASS} 已存在"
            + ("，已对齐参数" if changed else " (skip)")
        )
        return changed
    db.add(
        CronTask(
            id=uuid.uuid4().hex,
            name="wiki反馈修正",
            label="每天自动按维护中心反馈修正 wiki 页面（内容错误/过时/链接失效等）",
            cron_expression=CRON_EXPRESSION,
            state="1",
            task_class=CRON_TASK_CLASS,
            fire_params=fire_params,
            queue_name="default",
        )
    )
    db.commit()
    print(f"[apply-fbfix] 定时任务 {CRON_TASK_CLASS} 新建 OK: {CRON_EXPRESSION}（每天 02:00）")
    return True


def main() -> None:
    db = get_sessionmaker()()
    try:
        _seed_tape(db)
        _seed_cron(db)
        print("[apply-fbfix] 完成。部署提示：worker 容器需含 worker/tasks/feedback_fix.py（重建镜像或 docker cp + 重启 kb-celery-* worker）")
    finally:
        db.close()


if __name__ == "__main__":
    main()