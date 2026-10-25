"""知识聚合定时任务（KbAggregationRunTask，2026-10-25）。

知识库开启「主题聚类 / 版本聚类」后，平台在 modo_cron_task 自动建每日
定时任务（id=agg_topic_{kb_id} / agg_version_{kb_id}，api/services/
aggregation.py::sync_aggregation_crons 写入）。cron 扫描器（scan_cron_tasks）
按表达式触发后投递本任务——按 fire_params 里编排名与 kb_id 触发一次编排：

  fire_params = {
    "kb_id": "...",
    "tape_name": "聚类知识-主题聚合" | "制度版本演进",
    "topics": [...],          # 主题聚类专用：用户填写主题列表
    "env": "prod"             # 编排执行环境（聚合读写正式库数据）
  }

实现与 API「立即聚合」共用 api.services.aggregation.enqueue_aggregation_run
（建 kb_tape_run + 写 KbOrchestrationRunTask modo_job + 投 orch 队列），
编排不存在/未发布、同编排已有 queued/running run 时跳过（幂等，不重复跑）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TASK_CLASS_AGGREGATION = "KbAggregationRunTask"


def run_aggregation(job_id: str, task_params: str | None = None) -> dict:
    """按 fire_params 触发一次聚合编排（scheduler.execute_modo_job 分派）。"""
    try:
        params = json.loads(task_params) if task_params else {}
    except Exception:
        params = {}
    kb_id = str(params.get("kb_id") or "")
    tape_name = str(params.get("tape_name") or "")
    if not kb_id or not tape_name:
        return {"success": False, "error": "fire_params 缺 kb_id/tape_name"}

    from api.db import get_sessionmaker
    from api.services.aggregation import enqueue_aggregation_run

    db = get_sessionmaker()()
    try:
        result = enqueue_aggregation_run(
            db,
            tape_name,
            kb_id,
            topics=params.get("topics") or None,
        )
    except Exception as exc:  # noqa: BLE001 — 编排缺失/未发布等按失败返回
        logger.exception("aggregation cron run failed: kb=%s tape=%s", kb_id, tape_name)
        return {"success": False, "error": str(exc)}
    finally:
        db.close()

    if result.get("skipped"):
        logger.info(
            "aggregation cron skipped (已有运行中任务): kb=%s tape=%s run=%s",
            kb_id, tape_name, result.get("run_id"),
        )
    return result


def register_task_handlers() -> None:
    """注册到 TASK_CLASS_REGISTRY（cron 页「任务类」下拉自动出现）。"""
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY

    TASK_CLASS_REGISTRY[TASK_CLASS_AGGREGATION] = run_aggregation
    logger.info("aggregation handlers registered: %s", TASK_CLASS_REGISTRY.keys())


register_task_handlers()