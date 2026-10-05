"""孤儿 RUNNING 任务周期回收（beat 每 5 分钟）。

背景：worker 容器重启会中断 in-flight 长任务（agent wiki 5-10 分钟、DOC
解析 <1 分钟），中断后 DB 状态停在 RUNNING；celery broker 默认要等
visibility_timeout(1h) 才自动重投，期间监控页显示「运行中」虚高（并发
2 却看到 4-5 个）。

机制：把 state=RUNNING 且 create_time 早于 now-N 分钟的任务按 task_class
映射队列重新 send_task（execute_modo_job 允许 RUNNING 重入，重投即续跑）。
N = 10 分钟：DOC 解析秒级、wiki 构建最长约 10 分钟，超时不结束必是孤儿，
不会误伤正常长任务。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# task_class -> 队列（对齐 doc_process._agent_task_target 的默认 inline 映射）
QUEUE_BY_TASK_CLASS: dict[str, str] = {
    "KbDocumentProcessTask": "default",
    "KbGraphBuildTask": "default",
    "KbAgentWikiBuildTask": "agent",
    "KbAgentGatewayTask": "default",
}
_ORPHAN_AFTER_MINUTES = 10


def recover_orphan_running_jobs() -> dict:
    """扫描超时 RUNNING 任务并重投。返回 {recovered: n, skipped: m}。"""
    import datetime

    from sqlalchemy import select

    from api.db import get_sessionmaker
    from api.models.framework import Job

    db = get_sessionmaker()()
    try:
        cutoff = datetime.datetime.now() - datetime.timedelta(minutes=_ORPHAN_AFTER_MINUTES)
        jobs = (
            db.execute(
                select(Job).where(
                    Job.state == "RUNNING",
                    Job.task_class.is_not(None),
                    Job.create_time.is_not(None),
                    Job.create_time < cutoff,
                )
            )
            .scalars()
            .all()
        )
        if not jobs:
            return {"recovered": 0, "skipped": 0}
        from worker.celery_app import celery_app

        recovered = 0
        for job in jobs:
            queue = QUEUE_BY_TASK_CLASS.get(job.task_class or "", "") or job.queue_name or "default"
            try:
                celery_app.send_task(
                    "worker.tasks.scheduler.execute_modo_job",
                    args=[job.id],
                    task_id=job.id,
                    queue=queue,
                )
                recovered += 1
                logger.warning(
                    "orphan re-queued job=%s task=%s -> queue=%s",
                    job.id,
                    job.task_class,
                    queue,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("orphan re-queue failed job=%s: %s", job.id, exc)
        return {"recovered": recovered, "skipped": len(jobs) - recovered}
    finally:
        db.close()


TASK_CLASS_ORPHAN_RECOVERY = "KbOrphanRecoveryTask"


def _handle_orphan_recovery(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：周期回收孤儿 RUNNING 任务。"""
    result = recover_orphan_running_jobs()
    return {"success": True, "output": f"orphan recovery: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_ORPHAN_RECOVERY] = _handle_orphan_recovery