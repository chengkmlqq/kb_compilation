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
    from worker.tasks import doc_process, wiki_graph, wiki_build, agent_gateway
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY
    print(f"Tasks registered: {list(TASK_CLASS_REGISTRY.keys())}")
except Exception as e:
    print(f"Warning: Failed to register tasks: {e}")
