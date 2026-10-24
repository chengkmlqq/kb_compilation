"""wiki 反馈修正定时任务（KbFeedbackFixTask）。

每天触发一次（cron 0 2 * * *，由 apply_feedback_fix_tape.py 配置）：扫描全部
知识库的维护中心待处理反馈（wiki_feedback status=pending 且未软删），对每个
有反馈的知识库串行执行「wiki反馈修正」编排（env=prod，与上传链同环境，写正式
wiki 表），并轮询编排 run 至终态后再发起下一个库（execute_tape 对同一编排有
防重：同 tape 同时只能有一个 queued/running run）。

编排处理语义（见种子 scripts/seed_tape_data/wiki反馈修正.json）：
- 高频词标注（preset kw_*）自动置 resolved，不改页面；
- 内容类反馈按页面分组，LLM 依据反馈修正页面内容，修正成功反馈置 resolved、
  依据不足置 ignored；报告写 /tmp/fbfix/report.jsonl。

fire_params（cron 任务参数 JSON）::

    {
      "dry_run": false,          # true 只扫描统计不执行编排
      "wait_timeout_s": 1800     # 单库编排 run 等待上限（默认 30 分钟）
    }

与 KbOrphanDocCleanupTask 同构：handler 注册进 TASK_CLASS_REGISTRY，由
scheduler.execute_modo_job 分发（cron 触发 / 任务监控手动执行均可）。
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

TASK_CLASS_FEEDBACK_FIX = "KbFeedbackFixTask"
TAPE_NAME = "wiki反馈修正"
POLL_INTERVAL_S = 5
DEFAULT_WAIT_TIMEOUT_S = 1800

TERMINAL_RUN_STATES = ("success", "failed", "cancelled")


def _params(task_params) -> dict:
    if isinstance(task_params, dict):
        return task_params
    try:
        return json.loads(task_params) if task_params else {}
    except (TypeError, ValueError):
        return {}


def _list_kbs_with_pending_feedback(db) -> list[str]:
    """全部有待处理反馈（内容类或 kw 标注）的知识库 id 列表。"""
    from sqlalchemy import text

    rows = db.execute(
        text(
            "select distinct kb_id from wiki_feedback "
            "where status='pending' and deleted_at is null and kb_id is not null "
            "and kb_id != '' order by kb_id"
        )
    ).all()
    return [str(r[0]) for r in rows]


def _find_tape(db):
    from sqlalchemy import select

    from api.models.orchestration import Tape

    return db.execute(
        select(Tape).where(
            Tape.tape_name == TAPE_NAME,
            Tape.status != "offline",
        )
    ).scalars().first()


def _wait_run_terminal(run_id: str, timeout_s: int) -> str:
    """轮询编排 run 至终态；超时返回 'timeout'。run 落在 prod 表（env=prod）。"""
    from api.db import ENV_PROD, env_scope, get_sessionmaker
    from sqlalchemy import select

    from api.models.orchestration import TapeRun

    deadline = time.time() + timeout_s
    with env_scope(ENV_PROD):
        db = get_sessionmaker()()
        try:
            while time.time() < deadline:
                status = db.execute(
                    select(TapeRun.status).where(TapeRun.id == run_id)
                ).scalar()
                if status in TERMINAL_RUN_STATES:
                    return str(status)
                time.sleep(POLL_INTERVAL_S)
            return "timeout"
        finally:
            db.close()


def run_feedback_fix(task_params: dict | None = None) -> dict:
    """扫描全库待处理反馈，逐库执行「wiki反馈修正」编排（env=prod）。返回统计。"""
    from api.db import env_scope
    from api.services.orchestration import execute_tape

    params = _params(task_params)
    dry_run = bool(params.get("dry_run"))
    wait_timeout_s = int(params.get("wait_timeout_s") or DEFAULT_WAIT_TIMEOUT_S)

    from api.db import get_sessionmaker

    db = get_sessionmaker()()
    results: list[dict] = []
    try:
        kbs = _list_kbs_with_pending_feedback(db)
        stats: dict = {
            "dry_run": dry_run,
            "kbs_with_feedback": len(kbs),
            "kbs": kbs,
            "runs": [],
        }
        if not kbs:
            stats["message"] = "无待处理反馈"
            return {"success": True, "output": stats}

        tape = None
        if not dry_run:
            tape = _find_tape(db)
            if tape is None:
                return {"success": False, "error": f"未找到编排「{TAPE_NAME}」（先跑 apply_feedback_fix_tape.py）"}

        for kb_id in kbs:
            if dry_run:
                results.append({"kb_id": kb_id, "action": "dry_run"})
                continue
            if tape is None:  # 类型守卫（dry_run 之外已确认非 None）
                results.append({"kb_id": kb_id, "error": "编排未找到"})
                break
            try:
                created = execute_tape(db, tape.id, {"kb_id": kb_id}, env="prod")
            except ValueError as e:
                # execute_tape 防重 / 参数错误：记录后中止（防重意味着上一个
                # run 仍在跑，继续下一个库也会被拒）
                results.append({"kb_id": kb_id, "error": str(e)})
                stats["blocked"] = True
                break
            run_id = created.get("run_id") or ""
            if not run_id:
                results.append({"kb_id": kb_id, "error": "execute_tape 未返回 run_id"})
                break
            status = _wait_run_terminal(run_id, wait_timeout_s)
            results.append({"kb_id": kb_id, "run_id": run_id, "status": status})
            if status == "timeout":
                logger.warning(
                    "feedback fix run 超时: kb=%s run=%s（>%ss），中止本轮后续库",
                    kb_id, run_id, wait_timeout_s,
                )
                break
        stats["runs"] = results
        return {"success": True, "output": stats}
    finally:
        db.close()


def _handle_feedback_fix(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：执行反馈修正。"""
    result = run_feedback_fix(task_params)
    if not result.get("success"):
        return result
    return {"success": True, "output": json.dumps(result.get("output") or {}, ensure_ascii=False)}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_FEEDBACK_FIX] = _handle_feedback_fix