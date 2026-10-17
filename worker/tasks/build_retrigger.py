"""构建失败补位自愈任务（cron 每 6 小时，可页面配置）。

背景：批量 wiki 构建（编排 tape）依赖宿主 retrigger_batched.py 滚动补位——
宿主进程被杀/崩溃后无自愈，failed 文档永远停滞。本任务把补位逻辑搬进
平台内，由 modo_cron_task 调度（默认每 6h 一次），定时任务管理页可改。

机制（与 retrigger_batched.py 同口径，去掉 60s 轮询改成一次性补位）：
1. 收集 status='failed' 的 run → 按 (doc_id, kb_id) 去重（取最新 tape_id）
2. 在途水位 = status='running' 的 run 数；目标文档若已有 running/queued
   run 则跳过（防 6h 轮次间重复补位同一文档）
3. 差额 = watermark - 在途；逐目标 _enqueue_build_engine_task（新建 run 从头跑）
4. 无 failed 目标 → no-op

不依赖宿主进程/HTTP cookie（编排节点已 KB_INTERNAL_DIRECT 直连）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_WATERMARK = 40


def _db():
    from api.db import get_sessionmaker

    return get_sessionmaker()()


def collect_failed_targets() -> list[dict]:
    """failed run → [(doc_id, kb_id, tape_id)] 去重（取最新 updated_at 的 tape_id）。"""
    from sqlalchemy import text

    db = _db()
    try:
        rows = db.execute(
            text(
                "SELECT inputs, tape_id, updated_at FROM kb_tape_run "
                "WHERE status='failed' AND inputs IS NOT NULL"
            )
        ).all()
    finally:
        db.close()
    best: dict[tuple[str, str], dict] = {}
    for inputs_raw, tape_id, updated_at in rows:
        if not inputs_raw:
            continue
        try:
            inp = json.loads(inputs_raw)
        except Exception:  # noqa: BLE001
            continue
        doc_id = inp.get("doc_id") or inp.get("document_id") or inp.get("kid")
        kb_id = inp.get("kb_id")
        if not doc_id or not kb_id:
            continue
        key = (doc_id, kb_id)
        prev = best.get(key)
        if prev is None or (updated_at or "") > (prev.get("updated_at") or ""):
            best[key] = {"doc_id": doc_id, "kb_id": kb_id, "tape_id": tape_id, "updated_at": updated_at}
    return list(best.values())


def _running_and_queued_keys() -> set[tuple[str, str]]:
    """在途/排队 run 的 (doc_id, kb_id) 集合（补位跳过已触发的）。"""
    from sqlalchemy import text

    db = _db()
    try:
        rows = db.execute(
            text(
                "SELECT inputs FROM kb_tape_run "
                "WHERE status IN ('running','queued') AND inputs IS NOT NULL"
            )
        ).all()
    finally:
        db.close()
    keys: set[tuple[str, str]] = set()
    for (inputs_raw,) in rows:
        try:
            inp = json.loads(inputs_raw)
        except Exception:  # noqa: BLE001
            continue
        doc_id = inp.get("doc_id") or inp.get("document_id") or inp.get("kid")
        kb_id = inp.get("kb_id")
        if doc_id and kb_id:
            keys.add((doc_id, kb_id))
    return keys


def retrigger_build(watermark: int = DEFAULT_WATERMARK) -> dict:
    """滚动补位一次（到水位即止）。返回统计。"""
    from worker.tasks.doc_process import _enqueue_build_engine_task

    targets = collect_failed_targets()
    if not targets:
        return {"targets": 0, "enqueued": 0, "skipped": 0, "failed": 0, "note": "no failed targets"}

    in_flight = _running_and_queued_keys()
    candidates = [t for t in targets if (t["doc_id"], t["kb_id"]) not in in_flight]
    # 只统计被 in_flight 挡掉的数量（重复保护）
    skipped_dup = len(targets) - len(candidates)

    from api.db import get_sessionmaker
    from api.models.orchestration import TapeRun

    db = get_sessionmaker()()
    try:
        running = db.query(TapeRun).filter(TapeRun.status == "running").count()
    finally:
        db.close()
    need = max(0, watermark - running)
    if need == 0:
        return {
            "targets": len(targets),
            "enqueued": 0,
            "skipped": skipped_dup,
            "failed": 0,
            "note": f"水位已满 running={running}/{watermark}",
        }

    ok = fail = 0
    for t in candidates[:need]:
        try:
            r = _enqueue_build_engine_task(t["kb_id"], t["doc_id"], t["tape_id"])
            ok += 1 if r else 0
            fail += 0 if r else 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("build retrigger enqueue fail doc=%s: %s", t["doc_id"], exc)
            fail += 1
    return {
        "targets": len(targets),
        "enqueued": ok,
        "skipped": skipped_dup,
        "failed": fail,
        "note": f"补位到水位 {min(running + need, watermark)}",
    }


TASK_CLASS_BUILD_RETRIGGER = "KbBuildRetriggerTask"


def _handle_build_retrigger(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：构建失败补位自愈（cron 每 6h）。"""
    params = task_params or {}
    try:
        watermark = int(params.get("watermark", DEFAULT_WATERMARK))
    except (TypeError, ValueError):
        watermark = DEFAULT_WATERMARK
    result = retrigger_build(watermark=watermark)
    return {"success": True, "output": f"build retrigger: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_BUILD_RETRIGGER] = _handle_build_retrigger
