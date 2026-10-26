"""孤儿文档任务记录定期清理（KbOrphanDocCleanupTask）。

背景：任务监控页残留「文档已删除但任务记录还在」的链头任务。删除文档时
api/services/kb_admin.py::_cleanup_document_jobs 会清理关联任务行，但该逻辑
2026-10-21 才上线——此前删除的文档，其 DOC_* 任务行（KbDocumentProcessTask）
永远残留在 modo_job（实测 QA 库 57 条，PENDING/STOPPED/SUCCESS/FAILED 皆有）。
用户要求增加定时任务兜底扫描，每 10 分钟清理一次。

机制：扫描 KbDocumentProcessTask（链头）任务，解析 task_params.documentId/\
document_id → 查 kb_document 是否存在；不存在（文档已删）→ 级联删除，与
_cleanup_document_jobs 同口径：
  - 链头（id LIKE 'DOC_<docid>%'，覆盖重解析轮次）及其子任务（parent_id 指向链头）
  - task_params 含该 document_id 的其他任务（WIKI_*/VERIFY_* 等）
  - 该文档的编排 run（kb_tape_run.inputs 含 document_id）

fire_params（cron 任务参数 JSON）::

    {
      "dry_run": false        # true 只统计不删除
    }

建议 cron：每 10 分钟 ``*/10 * * * *``（用户 2026-10-21 指定）。

与 KbLogCleanupTask 的差异：后者按「保留天数」删历史终态任务；本任务按
「文档是否还存在」删孤儿链头——文档已删的任务行无意义（审计价值 < 页面干净，
与 _cleanup_document_jobs 同一取舍）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TASK_CLASS_ORPHAN_DOC_CLEANUP = "KbOrphanDocCleanupTask"


def _extract_document_id(task_params: str | None) -> str:
    """从 task_params JSON 提取 documentId/document_id；无则返回空串。"""
    if not task_params:
        return ""
    try:
        params = json.loads(task_params)
    except (TypeError, ValueError):
        return ""
    if not isinstance(params, dict):
        return ""
    for k in ("documentId", "document_id", "kid"):
        v = params.get(k)
        if v:
            return str(v).strip()
    return ""


def cleanup_orphan_doc_jobs(task_params: dict | None = None) -> dict:
    """扫描文档已删除的链头任务并级联清理。返回删除统计。

    口径与 kb_admin._cleanup_document_jobs 一致：链头 + 子任务 + 关联其他
    任务 + 编排 run。仅处理 KbDocumentProcessTask（文件处理链头）；编排承接
    行（SUMMARY_RUN_* 等）随链头 parent_id 级联，不单独按 runId 反查。
    """
    from sqlalchemy import delete, select

    from api.db import get_sessionmaker
    from api.models.framework import Job
    from api.models.knowledge import KbDocument

    params = task_params or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            params = {}
    dry_run = bool(params.get("dry_run"))

    db = get_sessionmaker()()
    stats: dict[str, int | bool | str] = {"dry_run": dry_run}
    child_ids: list[str] = []
    run_id_rows: list[str] = []
    all_job_ids: set[str] = set()
    try:
        # 1) 收集孤儿链头：KbDocumentProcessTask 且 documentId 指向的文档已删
        heads = db.execute(
            select(Job).where(Job.task_class == "KbDocumentProcessTask")
        ).scalars().all()
        orphan_heads: list[Job] = []
        orphan_doc_ids: set[str] = set()
        for j in heads:
            did = _extract_document_id(j.task_params)
            if not did:
                continue
            doc_exists = (
                db.execute(
                    select(KbDocument.id).where(KbDocument.id == did)
                ).scalars().first()
                is not None
            )
            if not doc_exists:
                orphan_heads.append(j)
                orphan_doc_ids.add(did)
        stats["orphan_heads"] = len(orphan_heads)

        # 2) 待删 job id 集合 + 编排 run id 集合
        if orphan_heads:
            from api.models.orchestration import TapeRun

            for j in orphan_heads:
                all_job_ids.add(j.id)
            # 链头子任务（parent_id 指向孤儿链头）
            child_ids = list(db.execute(
                select(Job.id).where(Job.parent_id.in_(list(all_job_ids)))
            ).scalars().all())
            all_job_ids.update(child_ids)
            # 重解析轮次链头：id LIKE 'DOC_<docid>%' 或 task_params 含 document_id
            # 的顶层任务（parent_id IS NULL）
            for did in orphan_doc_ids:
                like_rows = db.execute(
                    select(Job.id).where(Job.task_params.like(f"%{did}%"))
                ).scalars().all()
                all_job_ids.update(like_rows)
                # 重解析链头（DOC_<docid>_R2 等）的子任务
                round_children = db.execute(
                    select(Job.id).where(Job.parent_id.in_(list(like_rows)))
                ).scalars().all()
                all_job_ids.update(round_children)
                # 编排 run：inputs 含 document_id
                run_rows = db.execute(
                    select(TapeRun.id).where(TapeRun.inputs.like(f"%{did}%"))
                ).scalars().all()
                run_id_rows.extend(run_rows)

        stats["children"] = len(child_ids)
        stats["like_jobs"] = max(
            0,
            len(all_job_ids) - len(orphan_heads) - len(child_ids),
        )
        stats["runs"] = len(run_id_rows)
        stats["total_jobs"] = len(all_job_ids)

        if not dry_run and all_job_ids:
            db.execute(delete(Job).where(Job.id.in_(all_job_ids)))
            if run_id_rows:
                db.execute(delete(TapeRun).where(TapeRun.id.in_(run_id_rows)))
            db.commit()
            logger.warning(
                "orphan doc cleanup: 删除残留任务 %d 行 / 编排 run %d 行 / 文档 %d 个",
                len(all_job_ids), len(run_id_rows), len(orphan_doc_ids),
            )
        else:
            logger.info("orphan doc cleanup (dry_run=%s): %s", dry_run, stats)
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _handle_orphan_doc_cleanup(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：清理孤儿文档任务记录。"""
    result = cleanup_orphan_doc_jobs(task_params)
    return {"success": True, "output": f"orphan doc cleanup: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_ORPHAN_DOC_CLEANUP] = _handle_orphan_doc_cleanup