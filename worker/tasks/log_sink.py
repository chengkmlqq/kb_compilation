"""任务日志落盘（MinIO 对象存储）——文档构建/agent 任务的运行日志。

worker 任务每步进度通过 `append_job_log` 写入：
  1. MinIO 对象 `logs/<job_id>.log`（追加，读-改-写整对象——对象存储无 append，
     但日志量小、频率低，整写可接受；MinIO 未启用时静默跳过）
  2. modo_job.error_message 槽位（保持任务监控页 / SSE log-stream 兼容）

SSE 读取端（api/routers/jobs.py `_read_incremental`）优先读 MinIO 日志对象
增量，回退 error_message。全部 best-effort：任何失败都不使任务失败。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# MinIO 日志对象路径前缀（桶内 `logs/` 目录，与文档/技能/文件管理隔离）
LOG_KEY_PREFIX = "logs/"


def _log_key(job_id: str) -> str:
    return f"{LOG_KEY_PREFIX}{job_id}.log"


def _append_remote(job_id: str, message: str) -> None:
    """把一行消息追加到 MinIO logs/<job_id>.log（读-改-写，best-effort）。"""
    try:
        from api.services import storage as storage_svc

        if not storage_svc.remote_enabled():
            return
        path = f"minio://{storage_svc.get_settings().MINIO_BUCKET or 'kb-compilation'}/{_log_key(job_id)}"
        old = b""
        try:
            old = storage_svc.get_bytes(path)
        except Exception:  # noqa: BLE001 — 对象不存在 = 首次写入
            old = b""
        storage_svc.put_bytes(path, old + message.encode("utf-8") + b"\n")
    except Exception as exc:  # noqa: BLE001 — 日志落盘失败绝不使任务失败
        logger.debug("job log sink(remote) failed job=%s: %s", job_id, exc)


def _write_error_slot(job_id: str, message: str) -> None:
    """写入 modo_job.error_message 槽位（任务监控页实时可见，best-effort）。"""
    try:
        from api.db import get_sessionmaker
        from api.models.framework import Job
        from sqlalchemy import select

        db = get_sessionmaker()()
        try:
            job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
            if job is not None and job.state not in ("SUCCESS", "FAILED"):
                job.error_message = message[:2000]
                db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("job log sink(slot) failed job=%s: %s", job_id, exc)


def append_job_log(job_id: str, message: str) -> None:
    """任务运行日志：追加到 MinIO logs/<job_id>.log + error_message 槽位。"""
    if not job_id or not message:
        return
    _append_remote(job_id, message)
    _write_error_slot(job_id, message)


__all__ = ["append_job_log"]
