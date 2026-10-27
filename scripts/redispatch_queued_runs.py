"""给已建行但未投递的 queued run 补投 orch 调度中枢（2026-10-27 重发补救）。

retrigger_wiki_build_failed.py v1 只建 kb_tape_run 行漏掉 send_task，
478 个 run 卡 queued。本脚本对指定时间窗内 queued 的 wiki构建 run
逐个 send_task execute_run 到 orch 队列。（幂等：重复执行无害）
"""
import sys
sys.path.insert(0, "/srv/kb")

from sqlalchemy import select
from api.db import get_sessionmaker
from api.models.orchestration import TapeRun

FROM_TS = "2026-10-27 03:20:00"  # 重发脚本建行时间窗（UTC 表）

def main():
    db = get_sessionmaker()()
    try:
        rows = db.execute(
            select(TapeRun).where(
                TapeRun.status == "queued",
                TapeRun.created_at >= FROM_TS,
                TapeRun.tape_name.like("wiki构建%"),
            )
        ).scalars().all()
        print(f"[re-dispatch] queued wiki构建 run: {len(rows)} 个")
        from worker.celery_app import celery_app
        n = 0
        for r in rows:
            env = (r.bindings or {}).get("__env__") if isinstance(r.bindings, dict) else None
            run_env = env or "prod"
            celery_app.send_task(
                "worker.tasks.orch_runner.execute_run",
                args=[r.id, run_env],
                queue="orch",
            )
            n += 1
        print(f"[re-dispatch] 已补投 {n} 个")
    finally:
        db.close()

if __name__ == "__main__":
    main()