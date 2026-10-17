"""LLM 使用统计服务（2026-10-16 LLM 使用看板）。

数据源：kb_llm_usage（三处埋点写入的调用记录）+ 业务产出（kb_document
READY 文档数、wiki_page 活跃页数）。只读查询，不修改任何数据，无任何金额口径。

对外结构（对齐前端 TypeScript Interface）：
  summary(db, ...)  -> {totalRequests, totalTokens, processedDocs,
                       generatedKnowledge, avgLatency{ttft,total}, ...}
  distribution(db, dim) -> [{name, requests, inputTokens, outputTokens, cacheTokens}]
  trend(db, ...)    -> {timestamps[], inputTokens[], outputTokens[],
                       cacheCreationTokens[], cacheReadTokens[], cacheHitRate[]}
  details(db, ...)  -> {items[], total, page, page_size}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.models.knowledge import KbDatasource, KbDocument, LlmUsageRecord, WikiPage
from api.services.llm_usage import OP_TYPES

# 预设时间范围 → 回看时长
PRESETS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

FINAL_STATUSES = ("success", "error", "timeout", "context_overflow")
FAILED_STATUSES = ("error", "timeout", "context_overflow")

SORT_COLUMNS: dict[str, Any] = {
    "ts": LlmUsageRecord.ts,
    "model": LlmUsageRecord.model,
    "operation_type": LlmUsageRecord.operation_type,
    "kb_name": LlmUsageRecord.kb_name,
    "doc_name": LlmUsageRecord.doc_name,
    "input_tokens": LlmUsageRecord.input_tokens,
    "output_tokens": LlmUsageRecord.output_tokens,
    "cache_tokens": LlmUsageRecord.cache_read_tokens,
    "ttft_ms": LlmUsageRecord.ttft_ms,
    "total_ms": LlmUsageRecord.total_ms,
    "tps": LlmUsageRecord.tps,
    "status": LlmUsageRecord.status,
}


def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # 统一成 naive UTC（库里 ts 为 naive UTC）
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def resolve_range(
    preset: str = "24h",
    start: str | None = None,
    end: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """时间范围解析：preset(24h/7d/30d) 或 custom(start/end)。返回 naive UTC 区间。"""
    now_utc = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    if (preset or "").lower() == "custom":
        s = _parse_iso(start) or (now_utc - PRESETS["24h"])
        e = _parse_iso(end) or now_utc
        if e <= s:
            e = s + timedelta(hours=1)
        return s, e
    delta = PRESETS.get((preset or "24h").lower(), PRESETS["24h"])
    return now_utc - delta, now_utc


def _filters(kb_id: str = "", model: str = "", operation: str = "") -> list[Any]:
    conds: list[Any] = [
        LlmUsageRecord.status.in_(FINAL_STATUSES),
    ]
    if kb_id:
        conds.append(LlmUsageRecord.kb_id == kb_id)
    if model:
        conds.append(LlmUsageRecord.model == model)
    if operation:
        conds.append(LlmUsageRecord.operation_type == operation)
    return conds


def _base_where(
    start: datetime,
    end: datetime,
    kb_id: str = "",
    model: str = "",
    operation: str = "",
    status: str = "",
) -> list[Any]:
    conds = _filters(kb_id, model, operation)
    conds.append(LlmUsageRecord.ts >= start)
    conds.append(LlmUsageRecord.ts <= end)
    if status == "failed":
        conds.append(LlmUsageRecord.status.in_(FAILED_STATUSES))
    elif status in FINAL_STATUSES:
        conds.append(LlmUsageRecord.status == status)
    return conds


def _op_label(key: str) -> str:
    return OP_TYPES.get(key or "", OP_TYPES["other"])


def summary(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    kb_id: str = "",
    model: str = "",
    operation: str = "",
) -> dict:
    """KPI 概览：请求数 / Token 拆分 / 业务产出 / 平均延迟（TTFT + 总耗时）。"""
    where = _base_where(start, end, kb_id, model, operation)
    agg = db.execute(
        select(
            func.count(LlmUsageRecord.id),
            func.coalesce(func.sum(LlmUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.cache_read_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.cache_creation_tokens), 0),
            func.avg(LlmUsageRecord.ttft_ms),
            func.avg(LlmUsageRecord.total_ms),
            func.coalesce(func.sum(LlmUsageRecord.retries), 0),
        ).where(*where)
    ).one()
    (
        total_requests,
        input_tokens,
        output_tokens,
        cache_read,
        cache_creation,
        avg_ttft,
        avg_total,
        retries,
    ) = agg

    ok = db.execute(
        select(func.count(LlmUsageRecord.id)).where(
            *where, LlmUsageRecord.status == "success"
        )
    ).scalar() or 0
    failed = db.execute(
        select(func.count(LlmUsageRecord.id)).where(
            *where, LlmUsageRecord.status.in_(FAILED_STATUSES)
        )
    ).scalar() or 0

    # 业务产出：成功处理文档数（parse_state=READY 且处理完成时间在区间内）
    processed_docs = (
        db.execute(
            select(func.count(KbDocument.id)).where(
                KbDocument.parse_state == "READY",
                KbDocument.updated_at >= start,
                KbDocument.updated_at <= end,
            )
        ).scalar()
        or 0
    )
    # 生成知识条目：区间内新建的活跃 wiki 页
    generated_knowledge = (
        db.execute(
            select(func.count(WikiPage.id)).where(
                WikiPage.status == "active",
                WikiPage.created_at >= start,
                WikiPage.created_at <= end,
            )
        ).scalar()
        or 0
    )

    total_tokens = int(input_tokens) + int(output_tokens)
    return {
        "totalRequests": int(total_requests or 0),
        "totalTokens": total_tokens,
        "inputTokens": int(input_tokens or 0),
        "outputTokens": int(output_tokens or 0),
        "cacheReadTokens": int(cache_read or 0),
        "cacheCreationTokens": int(cache_creation or 0),
        "cacheHitRate": round(int(cache_read or 0) / int(input_tokens), 4)
        if int(input_tokens or 0) > 0
        else 0.0,
        "processedDocs": int(processed_docs),
        "generatedKnowledge": int(generated_knowledge),
        "avgLatency": {
            "ttft": round(float(avg_ttft), 2) if avg_ttft is not None else None,
            "total": round(float(avg_total), 2) if avg_total is not None else None,
        },
        "successCount": int(ok),
        "failedCount": int(failed),
        "errorRate": round(int(failed) / int(total_requests), 4) if total_requests else 0.0,
        "retries": int(retries or 0),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
    }


def distribution(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    dim: str = "kb",
    kb_id: str = "",
    model: str = "",
    operation: str = "",
    limit: int = 20,
) -> list[dict]:
    """分布统计（环形图 + 伴随表格）：dim = kb / model / operation。"""
    where = _base_where(start, end, kb_id, model, operation)
    if dim == "kb":
        key_col, label_col = LlmUsageRecord.kb_id, LlmUsageRecord.kb_name
    elif dim == "model":
        key_col, label_col = LlmUsageRecord.model, LlmUsageRecord.model
    elif dim == "operation":
        key_col, label_col = LlmUsageRecord.operation_type, LlmUsageRecord.operation_type
    else:
        raise ValueError(f"unknown dim: {dim}")

    rows = db.execute(
        select(
            key_col,
            func.max(label_col),
            func.count(LlmUsageRecord.id),
            func.coalesce(func.sum(LlmUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.cache_read_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.cache_creation_tokens), 0),
        )
        .where(*where)
        .group_by(key_col)
        .order_by(func.count(LlmUsageRecord.id).desc())
        .limit(limit)
    ).all()

    items: list[dict] = []
    for key, label, requests, inp, out, cache_read, cache_create in rows:
        name = (label or key or "未知")
        if dim == "operation":
            name = _op_label(str(key or "other"))
        items.append(
            {
                "key": key or "",
                "name": name or "未知",
                "requests": int(requests or 0),
                "inputTokens": int(inp or 0),
                "outputTokens": int(out or 0),
                "cacheTokens": int(cache_read or 0) + int(cache_create or 0),
            }
        )
    return items


def _bucket_key(ts: datetime, granularity: str) -> datetime:
    if granularity == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(minute=0, second=0, microsecond=0)


def _next_bucket(ts: datetime, granularity: str) -> datetime:
    return ts + (timedelta(days=1) if granularity == "day" else timedelta(hours=1))


def trend(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    granularity: str = "hour",
    kb_id: str = "",
    model: str = "",
    operation: str = "",
) -> dict:
    """Token 使用趋势：按小时/天分桶，含缓存命中率（cacheRead / inputTokens）。

    空桶补 0，保证 X 轴连续。时间为 naive UTC，前端按本地时区展示。
    """
    gran = "day" if (granularity or "").lower() == "day" else "hour"
    where = _base_where(start, end, kb_id, model, operation)
    rows = db.execute(
        select(
            LlmUsageRecord.ts,
            LlmUsageRecord.input_tokens,
            LlmUsageRecord.output_tokens,
            LlmUsageRecord.cache_read_tokens,
            LlmUsageRecord.cache_creation_tokens,
        ).where(*where)
    ).all()

    buckets: dict[datetime, dict[str, int]] = {}
    cursor = _bucket_key(start, gran)
    last = _bucket_key(end, gran)
    while cursor <= last:
        buckets[cursor] = {"input": 0, "output": 0, "read": 0, "creation": 0}
        cursor = _next_bucket(cursor, gran)

    for ts, inp, out, read, creation in rows:
        if ts is None:
            continue
        key = _bucket_key(ts, gran)
        slot = buckets.get(key)
        if slot is None:  # 越界（时间精度差异）→ 归入最近桶
            ts_naive: datetime = ts
            key = min(buckets, key=lambda k: abs((k - ts_naive).total_seconds())) if buckets else None
            if key is None:
                continue
            slot = buckets[key]
        slot["input"] += int(inp or 0)
        slot["output"] += int(out or 0)
        slot["read"] += int(read or 0)
        slot["creation"] += int(creation or 0)

    keys = sorted(buckets)
    timestamps = [k.strftime("%Y-%m-%dT%H:%M:%SZ") for k in keys]
    input_tokens = [buckets[k]["input"] for k in keys]
    output_tokens = [buckets[k]["output"] for k in keys]
    cache_read = [buckets[k]["read"] for k in keys]
    cache_creation = [buckets[k]["creation"] for k in keys]
    hit_rate = [
        round(buckets[k]["read"] / buckets[k]["input"], 4) if buckets[k]["input"] else 0.0
        for k in keys
    ]
    return {
        "granularity": gran,
        "timestamps": timestamps,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read,
        "cacheCreationTokens": cache_creation,
        "cacheHitRate": hit_rate,
    }


def details(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    page: int = 1,
    page_size: int = 20,
    sort: str = "ts",
    order: str = "desc",
    kb_id: str = "",
    model: str = "",
    operation: str = "",
    status: str = "",
    keyword: str = "",
) -> dict:
    """明细表：知识库操作明细（无任何费用/计费/API 密钥列）。"""
    where = _base_where(start, end, kb_id, model, operation, status)
    if keyword:
        like = f"%{keyword}%"
        where.append(
            LlmUsageRecord.trace_id.like(like)
            | LlmUsageRecord.kb_name.like(like)
            | LlmUsageRecord.doc_name.like(like)
            | LlmUsageRecord.model.like(like)
            | LlmUsageRecord.error_msg.like(like)
        )

    total = db.execute(select(func.count(LlmUsageRecord.id)).where(*where)).scalar() or 0

    col = SORT_COLUMNS.get(sort or "ts", LlmUsageRecord.ts)
    order_by = col.desc() if (order or "desc").lower() != "asc" else col.asc()
    rows = (
        db.execute(
            select(LlmUsageRecord)
            .where(*where)
            .order_by(order_by, LlmUsageRecord.id.desc())
            .offset(max(0, (page - 1) * page_size))
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    items = [
        {
            "id": r.trace_id or f"#{r.id}",
            "ts": r.ts.isoformat() if r.ts else None,
            "source": r.source,
            "kbName": r.kb_name or "-",
            "kbId": r.kb_id or "",
            "docName": r.doc_name or "-",
            "docId": r.doc_id or "",
            "model": r.model or "-",
            "operationType": r.operation_type or "other",
            "operationLabel": _op_label(r.operation_type or ""),
            "phase": r.phase or "",
            "inputTokens": int(r.input_tokens or 0),
            "outputTokens": int(r.output_tokens or 0),
            "cacheTokens": int(r.cache_read_tokens or 0) + int(r.cache_creation_tokens or 0),
            "cacheReadTokens": int(r.cache_read_tokens or 0),
            "latency": {
                "ttft": round(float(r.ttft_ms), 2) if r.ttft_ms is not None else None,
                "total": int(r.total_ms) if r.total_ms is not None else None,
                "tps": round(float(r.tps), 1) if r.tps is not None else None,
            },
            "status": r.status or "",
            "errorMessage": r.error_msg or "",
            "retries": int(r.retries or 0),
        }
        for r in rows
    ]

    # 错误分类计数（错误请求 Tab 的快速定位）
    error_groups = db.execute(
        select(LlmUsageRecord.status, func.count(LlmUsageRecord.id))
        .where(*_base_where(start, end, kb_id, model, operation, "failed"))
        .group_by(LlmUsageRecord.status)
    ).all()
    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "errorBreakdown": {str(s or ""): int(c or 0) for s, c in error_groups},
    }


def options(db: Session, *, days: int = 30) -> dict:
    """筛选项候选：知识库 / 模型 / 操作类型（近 N 天内出现过的 + 知识库全量）。"""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    kbs = (
        db.execute(select(KbDatasource.id, KbDatasource.name).order_by(KbDatasource.name))
        .all()
    )
    used_models = [
        m
        for (m,) in db.execute(
            select(LlmUsageRecord.model).where(LlmUsageRecord.ts >= since).distinct()
        ).all()
        if m
    ]
    used_ops = [
        o
        for (o,) in db.execute(
            select(LlmUsageRecord.operation_type).where(LlmUsageRecord.ts >= since).distinct()
        ).all()
        if o
    ]
    return {
        "knowledgeBases": [{"id": str(i or ""), "name": str(n or i or "")} for i, n in kbs],
        "models": sorted(set(used_models)),
        "operations": [
            {"value": key, "label": label}
            for key, label in OP_TYPES.items()
            if key in set(used_ops) or key in ("qa", "agent", "summary")
        ],
    }
