"""日志/任务记录定期清理（KbLogCleanupTask）。

背景：modo_oper_log（操作日志）与 modo_job（任务记录）目前只增不删，长期
运行会无限累积。系统此前没有任何清理定时任务。

机制：按保留天数删除过期记录——

- 操作日志 modo_oper_log：oper_time 是字符串 'YYYY-MM-DD HH:MM:SS'
  （见 OperLog.oper_time: String(32)），字典序即时间序，可直接字符串比较；
- 任务记录 modo_job：只删终态（SUCCESS/FAILED/STOPPED），RUNNING/PENDING
  永不删（避免删掉在途任务及其审计线索）。

fire_params（cron 任务参数 JSON）::

    {
      "retention_days": 90,          # 操作日志保留天数，默认 90
      "job_retention_days": 90,      # 任务记录保留天数，默认 90（<1 视为不清理）
      "dry_run": false               # true 只统计不删除
    }

建议 cron：每天 03:00（避开业务高峰）。crontab 表达式 ``0 3 * * *``。
"""
from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

TASK_CLASS_LOG_CLEANUP = "KbLogCleanupTask"

DEFAULT_RETENTION_DAYS = 90
# 任务终态：只有这些状态的历史记录可删（RUNNING/PENDING 保留）
TERMINAL_JOB_STATES = ("SUCCESS", "FAILED", "STOPPED")


def cleanup_logs(task_params: dict | None = None) -> dict:
    """按保留天数清理操作日志与任务记录。返回删除统计。"""
    from sqlalchemy import delete, func, select

    from api.db import get_sessionmaker
    from api.models.framework import Job, OperLog

    params = task_params or {}
    dry_run = bool(params.get("dry_run"))
    log_days = _pos_int(params.get("retention_days"), DEFAULT_RETENTION_DAYS)
    job_days = _pos_int(params.get("job_retention_days"), DEFAULT_RETENTION_DAYS)
    now = datetime.datetime.now()
    stats: dict[str, int | bool | str] = {
        "dry_run": dry_run,
        "oper_log_days": log_days,
        "job_days": job_days,
        "cutoff": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    db = get_sessionmaker()()
    try:
        # --- 操作日志：oper_time 为字符串，按字典序比较 ---
        if log_days > 0:
            log_cutoff = (now - datetime.timedelta(days=log_days)).strftime("%Y-%m-%d %H:%M:%S")
            cnt = db.execute(
                select(func.count()).select_from(OperLog).where(
                    OperLog.oper_time.is_not(None),
                    OperLog.oper_time < log_cutoff,
                )
            ).scalar() or 0
            stats["oper_logs"] = int(cnt)
            if cnt and not dry_run:
                db.execute(
                    delete(OperLog).where(
                        OperLog.oper_time.is_not(None),
                        OperLog.oper_time < log_cutoff,
                    )
                )

        # --- 任务记录：仅终态 ---
        if job_days > 0:
            job_cutoff = now - datetime.timedelta(days=job_days)
            cnt = db.execute(
                select(func.count()).select_from(Job).where(
                    Job.state.in_(TERMINAL_JOB_STATES),
                    Job.create_time.is_not(None),
                    Job.create_time < job_cutoff,
                )
            ).scalar() or 0
            stats["jobs"] = int(cnt)
            if cnt and not dry_run:
                db.execute(
                    delete(Job).where(
                        Job.state.in_(TERMINAL_JOB_STATES),
                        Job.create_time.is_not(None),
                        Job.create_time < job_cutoff,
                    )
                )

        if not dry_run:
            db.commit()
        logger.info("log cleanup done (dry_run=%s): %s", dry_run, stats)
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _pos_int(v: object, default: int) -> int:
    """解析正整数参数；非法/缺省用默认，<=0 表示不清理该项。"""
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n


def _handle_log_cleanup(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：清理过期日志与任务记录。"""
    result = cleanup_logs(task_params)
    return {"success": True, "output": f"log cleanup: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_LOG_CLEANUP] = _handle_log_cleanup