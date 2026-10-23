"""首页总览统计 API —— 平台知识构建情况一屏看板。

端点：
  GET /overview/summary            核心指标聚合（知识库/文档/分块/Wiki/任务/质量/反馈）
  GET /overview/trend?days=7       构建趋势（按天新增 文档/分块/Wiki 页）

鉴权：登录态（x-next-identity cookie）。统计口径按调用方**可见范围**——
知识库及其文档/分块/Wiki 统计只覆盖 personal(属主)/team(同队)/system 可见的库；
任务与质量评估为平台级指标，登录即可见。未登录 401。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.framework import Job
from api.models.knowledge import (
    DocChunk,
    KbDatasource,
    KbDocument,
    QualityRun,
    WikiLink,
    WikiPage,
    WikiPageIssue,
)
from api.services.feedback_stats import feedback_stats
from api.services.identity import decode_identity_cookie
from api.services.scope import is_admin, visible_clauses

router = APIRouter(prefix="/overview", tags=["overview"])


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return {
        "user_id": identity.user_id,
        "team_name": identity.team_name or "",
        "is_admin": is_admin(db, identity.user_id),
    }


def _visible_kb_ids(db: Session, caller: dict) -> list[str]:
    """调用方可见的知识库 id 列表（state=1）。可见性 union：属主 / 同队 / system。"""
    stmt = select(KbDatasource.id).where(KbDatasource.state == "1")
    clauses = visible_clauses(
        caller.get("user_id") or "",
        caller.get("team_name") or "",
        bool(caller.get("is_admin")),
        KbDatasource,
    )
    if clauses:
        stmt = stmt.where(clauses[0])
    return [str(r) for r in db.execute(stmt).scalars().all()]


def _scoped_count(db: Session, model, kb_ids: list[str], *extra_where) -> int:
    """对含 kb_id 的业务表按可见 kb 集合计数（空集合直接返回 0）。"""
    if not kb_ids:
        return 0
    stmt = select(func.count()).select_from(model).where(model.kb_id.in_(kb_ids))
    if extra_where:
        stmt = stmt.where(*extra_where)
    return int(db.execute(stmt).scalar() or 0)


@router.get("/summary")
def overview_summary(
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """核心指标聚合（一次请求返回全部统计卡所需数据）。

    2026-10-23 性能审计：整段聚合结果走 30s 进程内缓存——内部多条计数会全扫
    含 MEDIUMTEXT content 的大表（doc_chunk/wiki_page/wiki_link），远端库
    缓冲池小、冷索引页读取秒级；看板统计容忍短时陈旧，缓存后响应毫秒级。
    """
    from api.services.agg_cache import agg_cached_swr

    key = "ov_summary:%s:%s:%s" % (
        caller.get("user_id") or "",
        caller.get("team_name") or "",
        bool(caller.get("is_admin")),
    )

    def _load(s) -> dict:
        return _summary_data(s, caller)

    data = agg_cached_swr(key, 30.0, _load, db=db)
    return {"success": True, "data": data}


def _summary_data(db: Session, caller: dict) -> dict:
    kb_ids = _visible_kb_ids(db, caller)

    # ── 知识库：总数 + 按 scope 分组 ─────────────────────────────
    kb_scope_counts: dict[str, int] = {}
    if kb_ids:
        rows = db.execute(
            select(KbDatasource.scope, func.count())
            .where(KbDatasource.id.in_(kb_ids))
            .group_by(KbDatasource.scope)
        ).all()
        kb_scope_counts = {str(s or "system"): int(c) for s, c in rows}

    # ── 文档：总数 + 按解析状态 + 摘要生成情况 ───────────────────
    doc_state_counts: dict[str, int] = {}
    summary_ready = 0
    summary_failed = 0
    doc_total = 0
    if kb_ids:
        rows = db.execute(
            select(KbDocument.parse_state, func.count())
            .where(KbDocument.kb_id.in_(kb_ids))
            .group_by(KbDocument.parse_state)
        ).all()
        doc_state_counts = {str(s or "PENDING"): int(c) for s, c in rows}
        doc_total = sum(doc_state_counts.values())
        rows = db.execute(
            select(KbDocument.summary_status, func.count())
            .where(
                KbDocument.kb_id.in_(kb_ids),
                KbDocument.summary_status.in_(("READY", "FAILED")),
            )
            .group_by(KbDocument.summary_status)
        ).all()
        sm = {str(s or ""): int(c) for s, c in rows}
        summary_ready = sm.get("READY", 0)
        summary_failed = sm.get("FAILED", 0)

    # ── 分块总数 ────────────────────────────────────────────────
    chunk_total = _scoped_count(db, DocChunk, kb_ids)

    # ── Wiki：页数 / 链接数 / 未处理问题数 ───────────────────────
    wiki_pages = _scoped_count(db, WikiPage, kb_ids, WikiPage.status == "active")
    wiki_links = _scoped_count(db, WikiLink, kb_ids)
    wiki_issues_open = _scoped_count(db, WikiPageIssue, kb_ids, WikiPageIssue.status == "pending")

    # ── 任务：按状态 + 成功率 + 平均耗时（平台级）────────────────
    job_counts: dict[str, int] = {}
    rows = db.execute(select(Job.state, func.count()).group_by(Job.state)).all()
    for state, count in rows:
        job_counts[str(state or "")] = int(count)

    avg_duration = db.execute(
        select(func.avg(Job.duration_ms)).where(Job.duration_ms.is_not(None))
    ).scalar()
    job_total = sum(job_counts.values())
    job_success = job_counts.get("SUCCESS", 0)
    job_failed = job_counts.get("FAILED", 0)
    job_finished = job_success + job_failed
    success_rate = round(job_success / job_finished * 100, 1) if job_finished else 0.0

    # ── 质量评估：可见 kb 的评估次数 + 三维度均分（取 final_score/health_score）──
    quality_runs = 0
    quality_score = 0.0
    if kb_ids:
        q_runs = db.execute(
            select(QualityRun).where(QualityRun.kb_id.in_(kb_ids))
        ).scalars().all()
        quality_runs = len(q_runs)
        scores: list[float] = []
        for r in q_runs:
            for key in ("chunk_metrics", "wiki_metrics", "graph_metrics"):
                m = getattr(r, key) or {}
                if not isinstance(m, dict):
                    continue
                v = m.get("final_score")
                if v is None:
                    v = m.get("health_score")
                if isinstance(v, (int, float)):
                    scores.append(float(v))
        if scores:
            quality_score = round(sum(scores) / len(scores), 1)

    # ── 问答反馈（复用现有服务，按调用方口径）────────────────────
    fb = feedback_stats(db, caller.get("user_id") or "")
    fb_data = fb.get("data") or {}

    return {
        "kbs": {"total": len(kb_ids), "by_scope": kb_scope_counts},
            "docs": {
                "total": doc_total,
                "by_state": doc_state_counts,
                "summary_ready": summary_ready,
                "summary_failed": summary_failed,
            },
            "chunks": {"total": chunk_total},
            "wiki": {
                "pages": wiki_pages,
                "links": wiki_links,
                "issues_open": wiki_issues_open,
            },
            "jobs": {
                "total": job_total,
                "running": job_counts.get("RUNNING", 0),
                "success": job_success,
                "failed": job_failed,
                "queued": job_counts.get("PENDING", 0),
                "success_rate": success_rate,
                "avg_duration_ms": int(avg_duration) if avg_duration is not None else 0,
            },
            "quality": {"runs": quality_runs, "avg_score": quality_score},
            "feedback": {
                "total": fb_data.get("total_feedback", 0),
                "like_rate": fb_data.get("like_rate", 0),
                "week_count": fb_data.get("week_count", 0),
            },
    }


@router.get("/kb-stats")
def overview_kb_stats(
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """知识库维度统计：每个可见知识库的 文档数（按解析状态）/ 分块数 /
    Wiki 页数 / 待处理问题数 / 最近更新时间。总览页「知识库维度」区块数据源。

    聚合方式：全部按 kb_id 一次 GROUP BY 分批取，避免每库 N+1 查询。
    2026-10-23 性能审计：整段结果走 30s 进程内缓存（大表计数，缓冲池冷热波动）。
    """
    from api.services.agg_cache import agg_cached_swr

    key = "ov_kb_stats:%s:%s:%s" % (
        caller.get("user_id") or "",
        caller.get("team_name") or "",
        bool(caller.get("is_admin")),
    )

    def _load(s) -> list:
        return _kb_stats_data(s, caller)

    items = agg_cached_swr(key, 30.0, _load, db=db)
    return {"success": True, "data": {"items": items}}


def _kb_stats_data(db: Session, caller: dict) -> list:
    kb_ids = _visible_kb_ids(db, caller)
    if not kb_ids:
        return []

    # 库基础信息（名称 + 最近文档更新时间）
    kb_rows = db.execute(
        select(KbDatasource.id, KbDatasource.name)
        .where(KbDatasource.id.in_(kb_ids))
    ).all()
    kb_map: dict[str, str] = {str(i): n for i, n in kb_rows}

    # 文档：总数 + 解析状态分布 + 最近更新时间
    doc_rows = db.execute(
        select(
            KbDocument.kb_id,
            KbDocument.parse_state,
            func.count(),
            func.max(KbDocument.created_at),
        )
        .where(KbDocument.kb_id.in_(kb_ids))
        .group_by(KbDocument.kb_id, KbDocument.parse_state)
    ).all()
    doc_agg: dict[str, dict] = {}
    for kb_id, state, count, last_at in doc_rows:
        key = str(kb_id)
        ent = doc_agg.setdefault(key, {"total": 0, "by_state": {}, "last_doc_at": None})
        ent["total"] += int(count)
        ent["by_state"][str(state or "PENDING")] = int(count)
        if last_at and (ent["last_doc_at"] is None or last_at > ent["last_doc_at"]):
            ent["last_doc_at"] = last_at

    # 分块：按 kb_id 计数
    chunk_rows = db.execute(
        select(DocChunk.kb_id, func.count())
        .where(DocChunk.kb_id.in_(kb_ids))
        .group_by(DocChunk.kb_id)
    ).all()
    chunk_agg = {str(k): int(c) for k, c in chunk_rows}

    # Wiki 页（仅 active）
    wiki_rows = db.execute(
        select(WikiPage.kb_id, func.count())
        .where(WikiPage.kb_id.in_(kb_ids), WikiPage.status == "active")
        .group_by(WikiPage.kb_id)
    ).all()
    wiki_agg = {str(k): int(c) for k, c in wiki_rows}

    # 待处理问题（pending）
    issue_rows = db.execute(
        select(WikiPageIssue.kb_id, func.count())
        .where(WikiPageIssue.kb_id.in_(kb_ids), WikiPageIssue.status == "pending")
        .group_by(WikiPageIssue.kb_id)
    ).all()
    issue_agg = {str(k): int(c) for k, c in issue_rows}

    items = []
    for kb_id, name in kb_map.items():
        d = doc_agg.get(kb_id, {"total": 0, "by_state": {}, "last_doc_at": None})
        items.append(
            {
                "id": kb_id,
                "name": name,
                "docs": {
                    "total": d["total"],
                    "by_state": d["by_state"],
                },
                "chunks": chunk_agg.get(kb_id, 0),
                "wiki_pages": wiki_agg.get(kb_id, 0),
                "issues_open": issue_agg.get(kb_id, 0),
                "last_doc_at": d["last_doc_at"].isoformat() if d["last_doc_at"] else None,
            }
        )
    # 按文档总数降序，便于总览一眼看到负载最高的库
    items.sort(key=lambda i: i["docs"]["total"], reverse=True)
    return items


def _days_back(days: int) -> list[str]:
    """最近 N 天日期列表（含今天），本地时区，升序；供趋势补齐空日期。"""
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def _since_dt(date_str: str) -> datetime:
    """''YYYY-MM-DD'' → 当天零点 naive datetime（trend 裸列范围比较用）。"""
    return datetime.strptime(date_str, "%Y-%m-%d")


@router.get("/trend")
def overview_trend(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """构建趋势：最近 N 天按天新增 文档数 / 分块数 / Wiki 页数（按可见范围）。

    2026-10-21 性能修复：WHERE 不再用 func.date(created_at) >= since 包裹列
    （函数包裹列 → 索引失效 → kb_document/doc_chunk/wiki_page 全表扫），
    改为裸列 created_at >= 当天零点，配合 0029 迁移的 (kb_id, created_at)
    复合索引命中范围扫描；GROUP BY 仍按 DATE() 分桶（行数已被范围过滤收窄）。
    2026-10-23 性能审计：分组聚合会扫大表（doc_chunk/wiki_page），远端库
    缓冲池冷热波动秒级——看板趋势容忍短时陈旧，整段结果走 30s 进程内缓存。
    """
    from api.services.agg_cache import agg_cached_swr

    key = "ov_trend:%s:%s:%s:%d" % (
        caller.get("user_id") or "",
        caller.get("team_name") or "",
        bool(caller.get("is_admin")),
        days,
    )

    def _load(s) -> list:
        return _trend_days(s, caller, days)

    days_out = agg_cached_swr(key, 30.0, _load, db=db)
    return {"success": True, "data": {"days": days_out}}


def _trend_days(db: Session, caller: dict, days: int) -> list:
    kb_ids = _visible_kb_ids(db, caller)
    date_list = _days_back(days)
    buckets = {d: {"docs": 0, "chunks": 0, "wiki_pages": 0} for d in date_list}

    if kb_ids:
        since_dt = _since_dt(date_list[0])
        # 文档：按 created_at 的日期分桶
        rows = db.execute(
            select(func.date(KbDocument.created_at), func.count())
            .where(KbDocument.kb_id.in_(kb_ids), KbDocument.created_at >= since_dt)
            .group_by(func.date(KbDocument.created_at))
        ).all()
        for day, count in rows:
            key = str(day)
            if key in buckets:
                buckets[key]["docs"] = int(count)

        # 分块：doc_chunk.created_at
        rows = db.execute(
            select(func.date(DocChunk.created_at), func.count())
            .where(DocChunk.kb_id.in_(kb_ids), DocChunk.created_at >= since_dt)
            .group_by(func.date(DocChunk.created_at))
        ).all()
        for day, count in rows:
            key = str(day)
            if key in buckets:
                buckets[key]["chunks"] = int(count)

        # Wiki 页：wiki_page.created_at（仅 active）
        rows = db.execute(
            select(func.date(WikiPage.created_at), func.count())
            .where(
                WikiPage.kb_id.in_(kb_ids),
                WikiPage.status == "active",
                WikiPage.created_at >= since_dt,
            )
            .group_by(func.date(WikiPage.created_at))
        ).all()
        for day, count in rows:
            key = str(day)
            if key in buckets:
                buckets[key]["wiki_pages"] = int(count)

    return [{"date": d, **buckets[d]} for d in date_list]


__all__ = ["router"]
