"""元数据采集任务（KbMetadataCollectionTask）——在 celery worker 内执行。

与 kb 其他任务一致：execute_modo_job 按 task_class 从 TASK_CLASS_REGISTRY 找 handler，
handler 签名 (job_id, task_params) -> dict，并把进度/错误写回 modo_job 行。

task_params JSON::

    {
      "datasource_id": "xxx",
      "collection_mode": "full" | "incremental",
      "targets": [{"schema_name": "...", "table_name": "..."}]
    }
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TASK_CLASS_METADATA = "KbMetadataCollectionTask"


def _handle_metadata_collection(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：采集数据源表结构 + 字段元数据。"""
    from api.db import session_scope
    from api.models.framework import Job
    from api.services.metadata_collector import run_metadata_collection
    from sqlalchemy import select

    params = task_params or {}
    datasource_id = params.get("datasource_id") or params.get("datasourceId") or ""
    mode = params.get("collection_mode") or params.get("collectionMode") or "full"
    targets = params.get("targets") or []

    def progress(msg: str) -> None:
        logger.info("[metadata][%s] %s", job_id, msg)

    with session_scope() as db:
        job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
        if job is not None:
            job.state = "RUNNING"

    try:
        with session_scope() as db:
            summary = run_metadata_collection(
                db, datasource_id=datasource_id, mode=mode, targets=targets, progress=progress
            )
        with session_scope() as db:
            job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
            if job is not None:
                # 结果摘要写回 task_params，前端「采集状态/记录」页可读
                merged = json.loads(job.task_params or "{}")
                merged["summary"] = summary
                job.task_params = json.dumps(merged, ensure_ascii=False)
                job.state = "SUCCESS"
                job.error_message = None
        return {"success": True, "output": json.dumps(summary, ensure_ascii=False)}
    except Exception as e:  # noqa: BLE001
        logger.exception("[metadata][%s] collection failed", job_id)
        with session_scope() as db:
            job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
            if job is not None:
                job.state = "FAILED"
                job.error_message = str(e)[:2000]
        return {"success": False, "error": str(e)}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_METADATA] = _handle_metadata_collection