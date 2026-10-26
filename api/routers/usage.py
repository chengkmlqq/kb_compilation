"""LLM 使用记录与性能监控看板 API（2026-10-16）。

端点：
  GET /usage/summary         KPI 概览（请求数/Token/业务产出/平均延迟）
  GET /usage/distribution    分布统计（kb / model / operation 三维，环形图+伴随表格）
  GET /usage/trend           Token 使用趋势（小时/天分桶，含缓存命中率）
  GET /usage/details         知识库操作明细分页（可排序/筛选/错误请求定位）
  GET /usage/options         筛选下拉候选（知识库/模型/操作类型）

无任何金额/费用/计费字段。需要登录；admin/ROOT 看全量（普通用户看全量，
口径与反馈统计一致——LLM 使用属平台级可观测性，不做用户级隔离）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.db import get_db
from api.services import llm_usage_stats as svc
from api.services.identity import Identity, decode_identity_cookie

router = APIRouter(prefix="/usage", tags=["usage"])


def _require_identity(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> Identity:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity


@router.get("/summary")
def summary(
    preset: str = Query("24h", description="24h / 7d / 30d / custom"),
    start: str = Query("", description="custom 起始 ISO 时间"),
    end: str = Query("", description="custom 结束 ISO 时间"),
    kb_id: str = Query(""),
    model: str = Query(""),
    operation: str = Query(""),
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    start_dt, end_dt = svc.resolve_range(preset, start or None, end or None)
    # 2026-10-23 性能审计：kb_llm_usage 聚合冷缓冲池扫描秒级——看板统计走 30s
    # stale-while-revalidate 缓存（过期先返回旧值后台刷新）。preset 非 custom
    # 时 start/end 随 now() 每次变化（微秒），不能进 key。
    from api.services.agg_cache import agg_cached_swr

    if preset == "custom":
        key = "usage_summary:custom:%s:%s:%s:%s" % (
            str(start_dt), str(end_dt), kb_id or "", f"{model}|{operation}",
        )
    else:
        key = "usage_summary:%s:%s:%s" % (preset, kb_id or "", f"{model}|{operation}")

    def _load(s) -> dict:
        return svc.summary(
            s, start=start_dt, end=end_dt, kb_id=kb_id, model=model, operation=operation
        )

    data = agg_cached_swr(key, 30.0, _load, db=db)
    return {"success": True, "data": data}


@router.get("/distribution")
def distribution(
    dim: str = Query("kb", description="kb / model / operation"),
    preset: str = Query("24h"),
    start: str = Query(""),
    end: str = Query(""),
    kb_id: str = Query(""),
    model: str = Query(""),
    operation: str = Query(""),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    start_dt, end_dt = svc.resolve_range(preset, start or None, end or None)
    if dim not in ("kb", "model", "operation"):
        raise HTTPException(status_code=422, detail="dim 仅支持 kb / model / operation")
    items = svc.distribution(
        db,
        start=start_dt,
        end=end_dt,
        dim=dim,
        kb_id=kb_id,
        model=model,
        operation=operation,
        limit=limit,
    )
    return {"success": True, "data": items}


@router.get("/trend")
def trend(
    granularity: str = Query("hour", description="hour / day"),
    preset: str = Query("24h"),
    start: str = Query(""),
    end: str = Query(""),
    kb_id: str = Query(""),
    model: str = Query(""),
    operation: str = Query(""),
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    start_dt, end_dt = svc.resolve_range(preset, start or None, end or None)
    if granularity not in ("hour", "day"):
        raise HTTPException(status_code=422, detail="granularity 仅支持 hour / day")
    # 2026-10-23 性能审计：kb_llm_usage 按天分桶冷缓冲池扫描秒级——走 30s
    # stale-while-revalidate 缓存。preset 非 custom 时 start/end 随 now() 变化，不进 key。
    from api.services.agg_cache import agg_cached_swr

    if preset == "custom":
        key = "usage_trend:%s:custom:%s:%s:%s:%s" % (
            granularity, str(start_dt), str(end_dt), kb_id or "", f"{model}|{operation}",
        )
    else:
        key = "usage_trend:%s:%s:%s:%s" % (granularity, preset, kb_id or "", f"{model}|{operation}")

    def _load(s) -> dict:
        return svc.trend(
            s,
            start=start_dt,
            end=end_dt,
            granularity=granularity,
            kb_id=kb_id,
            model=model,
            operation=operation,
        )

    data = agg_cached_swr(key, 30.0, _load, db=db)
    return {"success": True, "data": data}


@router.get("/details")
def details(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort: str = Query("ts"),
    order: str = Query("desc", description="asc / desc"),
    status: str = Query("", description="success / failed / error / timeout / context_overflow / 空=全部"),
    preset: str = Query("24h"),
    start: str = Query(""),
    end: str = Query(""),
    kb_id: str = Query(""),
    model: str = Query(""),
    operation: str = Query(""),
    keyword: str = Query("", description="匹配 trace_id/知识库/文档/模型/错误信息"),
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    start_dt, end_dt = svc.resolve_range(preset, start or None, end or None)
    data = svc.details(
        db,
        start=start_dt,
        end=end_dt,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        kb_id=kb_id,
        model=model,
        operation=operation,
        status=status,
        keyword=keyword,
    )
    return {"success": True, "data": data}


@router.get("/options")
def usage_options(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    data = svc.options(db, days=days)
    return {"success": True, "data": data}
