#!/usr/bin/env python3
"""清空旧的切片评估记录并按知识库重新触发整库评估（2026-10-23，切片评估 v3）。

用途（配合 scripts/apply_chunk_eval_tape_v3.py 在容器内执行）：
  1) 收集当前 dim=chunk 的评估任务（含其 kb_quality_report）并删除
  2) 对删除前有 chunk 评估记录的每个知识库，按 scope=all 重新触发「切片评估」
     编排（execute_tape env=prod，与页面发起等价），串行执行——编排防重复发起，
     必须等上一个 run 到终态才能发起下一个（LLM 逐文件评估，整库需较长时间）

用法（api-server 或 worker 容器内，须能连 celery broker + DATABASE_URL）：
  docker cp scripts/retrigger_chunk_eval.py kb-api-server:/tmp/
  docker exec kb-api-server nohup /srv/kb/.venv/bin/python /tmp/retrigger_chunk_eval.py \
      > /tmp/retrigger_chunk_eval.log 2>&1 &
  # 进度：docker exec kb-api-server tail -f /tmp/retrigger_chunk_eval.log
"""
from __future__ import annotations

import sys
import time
import uuid

sys.path.insert(0, "/srv/kb")

from sqlalchemy import delete, select, text  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.knowledge import QualityReport, QualityRun  # noqa: E402
from api.services.orchestration import execute_tape  # noqa: E402

TAPE_NAME = "切片评估"
POLL_INTERVAL_S = 15
RUN_TIMEOUT_S = 6 * 3600  # 单个库整库评估最久等待（LLM 逐文件）


def tape_id_by_name(db, name: str) -> str:
    row = db.execute(select(text("id")).select_from(text("kb_tape")).where(text("tape_name=:n")), {"n": name}).first()
    if row is None:
        raise SystemExit(f"编排不存在: {name}")
    return str(row[0])


def wait_run_terminal(db, run_id: str, env: str) -> str:
    """轮询 kb_tape_run 到成功/失败（超时返回 timeout）。"""
    deadline = time.monotonic() + RUN_TIMEOUT_S
    while time.monotonic() < deadline:
        row = db.execute(
            text("SELECT status, COALESCE(error,'') FROM kb_tape_run WHERE id=:rid"),
            {"rid": run_id},
        ).first()
        if row is not None and row[0] in ("success", "failed"):
            return str(row[0])
        time.sleep(POLL_INTERVAL_S)
    return "timeout"


def main() -> None:
    db = get_sessionmaker()()
    try:
        # 1) 旧的 dim=chunk 评估记录
        old_runs = db.execute(
            select(QualityRun).where(QualityRun.dim == "chunk")
        ).scalars().all()
        if not old_runs:
            print("[retrigger] 无 dim=chunk 评估记录，无需清空")
            return
        kbs = sorted({r.kb_id for r in old_runs})
        print(f"[retrigger] 待清空 chunk 评估任务 {len(old_runs)} 条（涉及知识库 {len(kbs)} 个: {kbs}）")
        old_ids = [r.id for r in old_runs]
        db.execute(delete(QualityReport).where(QualityReport.run_id.in_(old_ids)))
        db.execute(delete(QualityRun).where(QualityRun.dim == "chunk"))
        db.commit()
        print(f"[retrigger] 已删除 kb_quality_run(dim=chunk) {len(old_ids)} 条 + 对应 kb_quality_report")

        # 2) 逐库重触发整库评估（串行，等终态）
        tape_id = tape_id_by_name(db, TAPE_NAME)
        print(f"[retrigger] 编排「{TAPE_NAME}」id={tape_id}，逐库发起 scope=all ...")
        for kb_id in kbs:
            qrun = QualityRun(
                id=uuid.uuid4().hex[:32], kb_id=kb_id, scope="all", doc_ids=None,
                status="queued", dim="chunk", created_by="system",
            )
            db.add(qrun)
            db.commit()
            inputs = {
                "kb_id": kb_id,
                "doc_ids": "",
                "scope": "all",
                "quality_id": qrun.id,
                "dim": "chunk",
            }
            try:
                r = execute_tape(db, tape_id, inputs, env="prod")
                run_id = str(r.get("run_id") or "")
                print(f"[retrigger] {kb_id} 已发起 run_id={run_id} quality_id={qrun.id}，等待终态...")
                db.commit()
            except Exception as exc:  # noqa: BLE001 — 编排已有一个在跑/触发失败，标记 failed
                print(f"[retrigger] {kb_id} 发起失败: {exc}")
                qrun.status = "failed"
                qrun.error = str(exc)[:500]
                db.commit()
                continue
            status = wait_run_terminal(db, run_id, "prod")
            print(f"[retrigger] {kb_id} 评估结束: {status} (run_id={run_id})")
        print("[retrigger] 全部完成")
    finally:
        db.close()


if __name__ == "__main__":
    main()