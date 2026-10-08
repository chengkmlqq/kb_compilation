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
    "KbSkillDirectBuildTask": "build",
    "KbAgentGatewayTask": "default",
}

# wiki 构建任务正常耗时 20-40 分钟（LLM 抽取），不能用通用 10min 阈值——
# 否则长任务每 10 分钟被误判孤儿重复投递（同 job 双跑、状态错乱、超时传播）。
# 按任务类区分：构建类 60min，其余维持 10min。
_ORPHAN_AFTER_MINUTES = 10
_ORPHAN_AFTER_MINUTES_BY_CLASS: dict[str, int] = {
    "KbSkillDirectBuildTask": 60,
    "KbAgentWikiBuildTask": 60,
}


def recover_orphan_running_jobs() -> dict:
    """扫描超时 RUNNING 任务并重投。返回 {recovered: n, skipped: m}。"""
    import datetime

    from sqlalchemy import select

    from api.db import get_sessionmaker
    from api.models.framework import Job

    db = get_sessionmaker()()
    try:
        jobs = []
        for task_class, after_min in (
            (None, _ORPHAN_AFTER_MINUTES),
            *[(tc, _ORPHAN_AFTER_MINUTES_BY_CLASS[tc]) for tc in _ORPHAN_AFTER_MINUTES_BY_CLASS],
        ):
            q = (
                select(Job).where(
                    Job.state == "RUNNING",
                    Job.task_class.is_not(None),
                    Job.create_time.is_not(None),
                    Job.create_time
                    < datetime.datetime.now() - datetime.timedelta(minutes=after_min),
                )
            )
            if task_class is not None:
                q = q.where(Job.task_class == task_class)
            else:
                # 通用 10min 分支必须排除有专属阈值的构建类——它们 20-40 分钟
                # 正常耗时，10min 误判会让同一 job 每 5 分钟重复投递、多实例并发
                # 抢跑（2026-10-08 实证 ForkPoolWorker 双跑同一 job + 页面竞态）
                q = q.where(
                    Job.task_class.not_in(list(_ORPHAN_AFTER_MINUTES_BY_CLASS.keys()))
                )
            jobs.extend(db.execute(q).scalars().all())
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
                # send_task lpush 头部 + redis broker brpop 尾部(实测 2026-10-05):
                # 不挪到尾部的重投消息会被数百排队任务压在最后, 恢复形同虚设。
                _promote_to_queue_tail(job.id, queue)
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


def _promote_to_queue_tail(job_id: str, queue: str) -> None:
    """把 redis 队列中 task_id==job_id 的消息挪到尾部(brpop 下一发)。

    失败静默——消息留在头部最终也会被消费(只是慢), 不影响正确性。
    """
    try:
        import base64
        import json

        import redis

        from api.config import get_settings

        broker = get_settings().effective_broker_url()
        r = redis.Redis.from_url(broker)
        n = r.llen(queue)
        for i in range(n):
            m = r.lindex(queue, i)
            if not m:
                continue
            try:
                d = json.loads(m)
                body = json.loads(base64.b64decode(d["body"]))
                tid = body[0][0] if isinstance(body, list) else None
            except Exception:  # noqa: BLE001 - 非标准消息跳过
                continue
            if tid == job_id:
                r.lrem(queue, 1, m)
                r.rpush(queue, m)
                logger.warning("promoted %s to tail of queue %s", job_id, queue)
                return
    except Exception as exc:  # noqa: BLE001
        logger.warning("promote-to-tail failed for %s: %s", job_id, exc)


TASK_CLASS_ORPHAN_RECOVERY = "KbOrphanRecoveryTask"


def _handle_orphan_recovery(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：周期回收孤儿 RUNNING 任务。"""
    result = recover_orphan_running_jobs()
    return {"success": True, "output": f"orphan recovery: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_ORPHAN_RECOVERY] = _handle_orphan_recovery