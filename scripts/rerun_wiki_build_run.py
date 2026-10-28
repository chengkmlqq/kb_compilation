#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重发指定 kb_tape_run（2026-10-28 动态构建 kp_paths 事故修复后重跑）。

读取原 run 的 tape_id/inputs，新建 run（env=prod）+ Job + 投递 orch。
原 failed run 保留不动（仅新增一个 corrected run）。

用法（kb-api-server 容器内）：
  docker cp scripts/rerun_wiki_build_run.py kb-api-server:/tmp/
  docker exec kb-api-server python /tmp/rerun_wiki_build_run.py --run <old_run_id>
"""
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, "/srv/kb")

from api.db import get_sessionmaker
from api.models.orchestration import TapeRun
from api.models.framework import Job
from sqlalchemy import select


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="原 failed run id（取其 tape_id/inputs 重建）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = get_sessionmaker()()
    try:
        old = db.execute(select(TapeRun).where(TapeRun.id == args.run)).scalars().first()
        if not old:
            print("run not found:", args.run); return
        tape_id = old.tape_id
        inputs = old.inputs or {}
        print(f"原 run={old.id[:12]} tape={tape_id[:12]} doc={inputs.get('doc_name')} kb={inputs.get('kb_id')}")
        if args.dry_run:
            print("[dry-run] 跳过"); return

        from api.services.orchestration import _create_tape_run
        created = _create_tape_run(db, tape_id, inputs=inputs, env="prod")
        run_id, run_env = created["run_id"], created["run_env"]
        db.commit()
        print(f"新 run={run_id[:12]} env={run_env}")

        job_id = f"ORCH_RUN_{uuid.uuid4().hex[:12]}"
        task_params = json.dumps({"runId": run_id, "env": run_env}, ensure_ascii=False)
        db.add(Job(id=job_id, task_id=job_id, task_class="KbOrchestrationRunTask",
                   queue_name="orch", task_params=task_params, trigger_type="API",
                   state="PENDING"))
        db.commit()
        print(f"job={job_id}")

        from worker.celery_app import celery_app
        celery_app.send_task("worker.tasks.scheduler.execute_modo_job",
                             args=[job_id], task_id=job_id, queue="orch")
        print("已投递 orch。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
