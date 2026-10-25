"""文档异步删除：级联清理任务 + beat 定时扫描兜底（2026-10-23）。

删除链路（异步化改造）：
  API（kb_admin.delete_document）标记 kb_document.delete_state=DELETING
  → enqueue_doc_delete 投 Celery → purge_document 后台执行完整级联清理
  （wiki 页/链接/目录、chunks、向量、存储文件、Neo4j 图谱、任务监控残留，
  逻辑在 api.services.kb_admin.purge_document_resources）→ 物理删除文档行。

失败兜底（双保险）：
  1. purge_document 自带 Celery autoretry（3 次 + backoff）；
  2. 重试仍失败 → delete_state=DELETE_FAILED 落库 → beat 的
     scan_stale_deletes 每 5 分钟扫描重投（同时兜底「任务丢失/worker 崩溃」
     导致的 DELETING 超时卡死）。purge 幂等，重投/双跑安全。

删除任务不走 Job 表（不注册 execute_modo_job）：删除清理本就无监控价值，
且 _cleanup_document_jobs 会按 document_id 清理关联 Job 行，注册进 Job 表
会把自己误删。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.knowledge import KbDocument
from api.services.kb_admin import purge_document_resources
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# DELETING 状态超过该时长无进展 → 视为清理任务丢失（消息/worker 崩溃），重投
STALE_DELETE_MINUTES = 10

# 兜底扫描间隔（秒，与 celery_app.beat_schedule 保持一致）
SCAN_INTERVAL_SECONDS = 300


def enqueue_doc_delete(kb_id: str, document_id: str) -> None:
    """把文档异步清理任务投到 default 队列（API 删除 / beat 兜底共用）。

    broker 不可达时抛异常，由调用方决定同步兜底（API 侧）。
    """
    celery_app.send_task(
        "worker.tasks.doc_delete.purge_document",
        kwargs={"kb_id": str(kb_id), "document_id": str(document_id)},
        queue="default",
    )


@celery_app.task(
    bind=True,
    name="worker.tasks.doc_delete.purge_document",
    acks_late=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_backoff_max=300,
)
def purge_document(self, kb_id: str, document_id: str) -> dict:
    """执行一个文档的完整级联清理并物理删除（幂等，可重复入队）。

    状态机：
      - 文档行不存在 → 已清理完成，直接成功（防重复投递噪音）；
      - delete_state 不是 DELETING/DELETE_FAILED → 删除被取消/恢复，跳过；
      - 执行前重置为 DELETING 并刷新 updated_at（失败重试恢复 + beat 超时
        判定以本轮为准）；
      - 清理失败 → 落 DELETE_FAILED + 原因，抛异常走 Celery autoretry；
        重试耗尽后保持 DELETE_FAILED，由 scan_stale_deletes 定期重投。
    """
    db = get_sessionmaker()()
    try:
        doc = db.execute(
            select(KbDocument).where(
                KbDocument.id == document_id, KbDocument.kb_id == kb_id
            )
        ).scalars().first()
        if doc is None:
            return {"success": True, "skipped": "document already gone"}
        if doc.delete_state not in ("DELETING", "DELETE_FAILED"):
            return {
                "success": True,
                "skipped": f"not scheduled for delete (delete_state={doc.delete_state!r})",
            }
        doc.delete_state = "DELETING"
        doc.delete_error = None
        doc.updated_at = datetime.utcnow()
        db.commit()
        return purge_document_resources(db, kb_id, document_id)
    except Exception as exc:  # noqa: BLE001 — 落 DELETE_FAILED 后交给 autoretry/beat
        db.rollback()
        try:
            doc_failed = db.execute(
                select(KbDocument).where(
                    KbDocument.id == document_id, KbDocument.kb_id == kb_id
                )
            ).scalars().first()
            if doc_failed is not None:
                doc_failed.delete_state = "DELETE_FAILED"
                doc_failed.delete_error = str(exc)[:500]
                db.commit()
        except Exception:  # noqa: BLE001 — 状态回填失败不掩盖原始异常
            db.rollback()
        logger.exception(
            "doc delete purge failed (will retry/beat): kb=%s doc=%s", kb_id, document_id
        )
        raise
    finally:
        db.close()


def _as_naive_utc(value: datetime | None) -> datetime | None:
    """统一按 UTC 字面比较（项目约定：DB 时间存 UTC naive；aware 时剥 tz）。"""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


@celery_app.task(name="worker.tasks.doc_delete.scan_stale_deletes", bind=False)
def scan_stale_deletes() -> dict:
    """兜底扫描：重投卡住的异步删除（beat 每 5 分钟，见 beat_schedule）。

    - DELETE_FAILED：celery 重试耗尽（清理持续失败）→ 重投再试一轮；
    - DELETING 且 updated_at 超过 STALE_DELETE_MINUTES 无进展 → 清理任务
      丢失（acks_late 窗口内 worker 崩溃 / 消息丢失）→ 重投。

    purge_document 幂等，重投与并发双跑安全；单条入队失败仅告警不阻断。
    """
    db = get_sessionmaker()()
    try:
        rows = db.execute(
            select(
                KbDocument.id,
                KbDocument.kb_id,
                KbDocument.delete_state,
                KbDocument.updated_at,
            ).where(KbDocument.delete_state.in_(["DELETING", "DELETE_FAILED"]))
        ).all()
    finally:
        db.close()

    cutoff = datetime.utcnow() - timedelta(minutes=STALE_DELETE_MINUTES)
    reenqueued = 0
    skipped = 0
    for rid, rkb, rstate, rupdated in rows:
        updated = _as_naive_utc(rupdated)
        stale = rstate == "DELETE_FAILED" or updated is None or updated < cutoff
        if not stale:
            skipped += 1
            continue
        try:
            enqueue_doc_delete(rkb, rid)
            reenqueued += 1
            logger.warning(
                "re-enqueued stale doc delete: doc=%s state=%s updated=%s",
                rid, rstate, rupdated,
            )
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断扫描
            logger.warning("failed to re-enqueue stale doc delete doc=%s: %s", rid, exc)
    return {
        "success": True,
        "scanned": len(rows),
        "reenqueued": reenqueued,
        "skipped": skipped,
    }