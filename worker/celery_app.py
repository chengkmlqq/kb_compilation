"""Celery application for the KB compilation platform.

Mirrors the source platform's scheduler pattern: a beat worker scans the
modo_cron_task table and dispatches jobs; a client app is used by the API
service to enqueue tasks (doc-parse / embed / wiki-build / graph-extract).
"""

from __future__ import annotations

from celery import Celery

from api.config import get_settings

settings = get_settings()


def _build_app(name: str) -> Celery:
    app = Celery(
        name,
        broker=settings.effective_broker_url(),
        backend=settings.effective_result_backend(),
    )
    app.conf.update(
        accept_content=["json"],
        enable_utc=True,
        result_serializer="json",
        task_serializer="json",
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        timezone=settings.CELERY_TIMEZONE,
        task_default_queue="default",
    )
    return app


# The main Celery app: workers import this and auto-discover tasks from
# worker/tasks/*.py (use `include` to control discovery).
celery_app = _build_app("kb_compilation")
celery_app.conf.update(
    include=[
        "worker.tasks.scheduler",
        "worker.tasks.doc_process",
        "worker.tasks.wiki_graph",
        "worker.tasks.agent_gateway",
        "worker.tasks.agent_worker",
    ],
    # 队列隔离（2026-10 WeKnora 对齐）：wiki 构建的 agent 任务投到独立 agent 队列，
    # 由专用 celery-agent-worker（-Q agent）消费；文档解析留在 default 队列的
    # celery-worker。两者物理隔离——分钟级~几十分钟的 agent 长任务不会堵住解析，
    # 且两类 worker 在 Flower 均可见、可独立扩容。
    # 注意：实际被投递的 Celery 任务名是 scheduler.execute_modo_job（task_class
    # 只是它的参数），所以路由由 doc_process._enqueue_wiki_skill_task 显式传
    # queue= 决定，task_routes 按任务名匹配在此不适用。
)

# Beat app: scans modo_cron_task and dispatches. Run via:
#   celery -A worker.celery_app beat
# (mirrors the source platform's beat_app.py scan-modo-cron-tasks)
celery_app.conf.beat_schedule = {
    "scan-modo-cron-tasks": {
        "task": "worker.tasks.scheduler.scan_cron_tasks",
        "schedule": settings.JOB_BEAT_SCAN_INTERVAL_SECONDS,
    },
}
# Force import all task modules to register handlers in TASK_CLASS_REGISTRY
try:
    from worker.tasks import (
        agent_gateway,
        doc_process,
        log_cleanup,
        orphan_recovery,
        wiki_build,
        wiki_graph,
    )
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY
    print(f"Tasks registered: {list(TASK_CLASS_REGISTRY.keys())}")
except Exception as e:
    print(f"Warning: Failed to register tasks: {e}")


# ── 孤儿任务恢复（2026-10-05 实测补丁）────────────────────────────
# 背景: celery-agent-worker 每次 docker restart（热同步代码 / 容器重建）都会
# 中断正在执行的 agent 长任务: worker 崩溃时 acks_late 消息要等 redis
# visibility_timeout(默认1h) 才重投, 而 DB 状态已置 RUNNING, 期间任务既不
# 执行也不终结, 表现为「卡 RUNNING 数十分钟~数小时」。手动重投可救, 但
# 每次重启都会再造一个孤儿。这里在 worker 启动完成后一次性扫描并重投
# 所有 RUNNING 的 execute_modo_job 任务(重投后 execute_modo_job 允许
# RUNNING 重入, 见 scheduler.execute_modo_job 的状态守卫), 从源头消除。
from celery.signals import worker_ready
from celery.utils.log import get_task_logger

_recover_logger = get_task_logger(__name__)


@worker_ready.connect
def _recover_orphan_running_jobs(**kwargs: object) -> None:
    """worker 完全启动后: 把本队列遗留 RUNNING 的 execute_modo_job 任务重置重投。

    只处理本 worker 消费的队列(task queue 由 -Q 参数决定): agent-worker/-Q agent
    只捞 agent 队列, celery-worker/-Q default 只捞 default 队列, 避免跨 worker
    误伤(对方正在正常执行的任务被重复投递)。
    """
    import sys

    # 解析启动命令里的 -Q a,b,c
    queues: set[str] = set()
    argv = sys.argv or []
    for i, a in enumerate(argv):
        if a in ("-Q", "--queues") and i + 1 < len(argv):
            queues.update(q.strip() for q in argv[i + 1].split(",") if q.strip())
    if not queues:
        queues = {"default"}
    _recover_logger.warning("orphan recovery active for queue(s): %s", ",".join(sorted(queues)))

    try:
        from api.db import get_sessionmaker
        from api.models.framework import Job
        from sqlalchemy import select

        db = get_sessionmaker()()
        try:
            jobs = (
                db.execute(
                    select(Job).where(
                        Job.state == "RUNNING",
                        Job.task_class.is_not(None),
                        Job.queue_name.in_(sorted(queues)),
                    )
                )
                .scalars()
                .all()
            )
        finally:
            db.close()
        if not jobs:
            return
        _recover_logger.warning("found %s RUNNING job(s) to recover", len(jobs))
        for job in jobs:
            try:
                celery_app.send_task(
                    "worker.tasks.scheduler.execute_modo_job",
                    args=[job.id],
                    task_id=job.id,
                    queue=job.queue_name or "default",
                )
                # send_task 走 lpush 头部, 而 redis broker 消费端 brpop 尾部
                # (实测 2026-10-05): 新消息若留在头部会被数百排队任务压在最后,
                # 恢复失去意义 → 立即把该消息从头部挪到队列尾部(下一个被消费)。
                _promote_to_queue_tail(job.id, job.queue_name or "default")
                _recover_logger.warning("re-queued orphan RUNNING job %s -> %s", job.id, job.queue_name or "default")
            except Exception as exc:  # noqa: BLE001
                _recover_logger.error("failed to re-queue orphan job %s: %s", job.id, exc)
    except Exception as exc:  # noqa: BLE001 — 恢复失败不阻塞 worker 启动
        _recover_logger.warning("orphan recovery skipped: %s", exc)


def _promote_to_queue_tail(job_id: str, queue: str) -> None:
    """把 redis 队列中 task_id==job_id 的消息从任意位置挪到尾部(brpop 下一发)。

    失败静默——消息即使留在头部也最终会被消费(只是慢), 不影响正确性。
    """
    try:
        import base64
        import json as _json

        import redis as _redis

        from api.config import get_settings

        broker = get_settings().effective_broker_url()  # redis://host:port/db
        r = _redis.Redis.from_url(broker)
        n = r.llen(queue)
        for i in range(n):
            m = r.lindex(queue, i)
            if not m:
                continue
            try:
                d = _json.loads(m)
                body = _json.loads(base64.b64decode(d["body"]))
                tid = body[0][0] if isinstance(body, list) else None
            except Exception:  # noqa: BLE001 - 非标准消息跳过
                continue
            if tid == job_id:
                r.lrem(queue, 1, m)
                r.rpush(queue, m)
                _recover_logger.warning("promoted %s to tail of queue %s", job_id, queue)
                return
        _recover_logger.warning("message for %s not found in queue %s (maybe already consumed)", job_id, queue)
    except Exception as exc:  # noqa: BLE001
        _recover_logger.warning("promote-to-tail failed for %s: %s", job_id, exc)
