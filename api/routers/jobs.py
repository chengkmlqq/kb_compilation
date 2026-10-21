"""Task monitor API — modo_job 列表/详情，供前端「任务监控」页使用。

任务（modo_job）由 scheduler 创建（CRON 触发）或上传/API 触发，worker 执行时
更新 state / start_time / end_time / duration_ms / error_message（agent-gateway
任务在轮询期间把进度写入 error_message 槽位）。本路由只读查询，不做任何
调度/取消操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence, cast

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import ENV_DEV, ENV_PROD, env_scope, get_db, get_sessionmaker
from api.config import get_settings
from api.models.framework import Job
from api.services.identity import decode_identity_cookie

if TYPE_CHECKING:  # 编排模型运行期仍在函数内局部 import（避免循环依赖）
    from api.models.orchestration import TapeRun

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _job_to_dict(job: Job) -> dict:
    """Serialize one modo_job row for the monitor page."""
    params: dict = {}
    try:
        params = json.loads(job.task_params or "{}")
    except (TypeError, json.JSONDecodeError):
        params = {"_raw": job.task_params}
    return {
        "id": job.id,
        "task_id": job.task_id,
        "task_class": job.task_class,
        "queue_name": job.queue_name,
        "trigger_type": job.trigger_type,
        "state": job.state,
        "start_time": job.start_time.isoformat() if job.start_time else None,
        "end_time": job.end_time.isoformat() if job.end_time else None,
        "duration_ms": job.duration_ms,
        "error_message": job.error_message,
        "log_path": job.log_path,
        "create_time": job.create_time.isoformat() if job.create_time else None,
        "params": params,
        # 2026-10-12 父子任务：parent_id（NULL=父任务）+ 子任务计数
        "parent_id": job.parent_id,
    }


def _parent_job_to_dict(job: Job, child_count: int, child_states: dict) -> dict:
    """父任务序列化：子任务聚合（数量 + 各状态计数）附加在父任务行上。"""
    d = _job_to_dict(job)
    d["child_count"] = child_count
    d["child_states"] = child_states
    return d


def _job_doc_names(db: Session, jobs: Sequence[Job]) -> dict[str, str]:
    """{job_id: 文档文件名}——task_params.documentId/document_id → kb_document.file_name。

    2026-10-15：任务监控首屏主任务名由「文档 <document_id 前 16 位>」改为显示文件名
    （用户反馈 UUID 不可读）。批量查一次避免 N+1；文档已删/查不到则不入映射，
    前端自动回退原 document_id 展示。
    """
    from api.models.knowledge import KbDocument

    job_doc: dict[str, str] = {}
    for j in jobs:
        try:
            params = json.loads(j.task_params or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            continue
        did = str(params.get("documentId") or params.get("document_id") or "")
        if did:
            job_doc[j.id] = did
    if not job_doc:
        return {}
    rows = (
        db.execute(
            select(KbDocument.id, KbDocument.file_name).where(
                KbDocument.id.in_(set(job_doc.values()))
            )
        )
        .all()
    )
    names = {str(did): str(fname or "") for did, fname in rows if fname}
    return {jid: names[did] for jid, did in job_doc.items() if did in names}


# ── 2026-10-12 父子任务视图 ─────────────────────────────────────────
# 父任务 = 文档处理链头（modo_job.parent_id IS NULL 的 API 触发任务）+
# 编排执行（kb_tape_run，虚拟父任务，子任务 = step_results）。
def _child_agg(db: Session, parent_ids: list[str]) -> dict[str, dict]:
    """按父任务 id 聚合子任务：{parent_id: {"count": N, "states": {...}}}

    2026-10-15 语义修正：摘要/构建编排子任务（KbOrchestrationRunTask）的 job
    状态是「投递成功」语义（handler 建完 run 即返回 SUCCESS），不代表编排执行
    结果——主任务解析完成即 SUCCESS 而子任务编排还在后台跑（用户反馈「子任务
    还在处理，主任务就显示成功了」）。这类子任务的 states 改按其编排 run
    （task_params.runId → kb_tape_run.status）的真实状态归一化（大写）；run
    查不到时回退 job 自身状态。

    2026-10-20 耗时口径：顺带收集 KbOrchestrationRunTask 子任务对应 run 的
    真实起止时间范围（earliest created_at → latest updated_at），供主任务行
    覆盖耗时使用——链头 job 的 start/end 是「投递」秒级（解析完成即 SUCCESS），
    子任务却已按 run 真实执行时长展示（见 children 接口），导致主任务耗时远
    小于子任务耗时总和，对不上账。
    """
    if not parent_ids:
        return {}
    rows = db.execute(
        select(Job.parent_id, Job.state, Job.task_class, Job.task_params).where(
            Job.parent_id.in_(parent_ids)
        )
    ).all()
    # 编排 run 真实状态 + 起止时间（窄列查询 kb_tape_run，避免宽行 filesort）
    run_meta_by_id: dict[str, tuple[str | None, datetime | None, datetime | None]] = {}
    orch_ids: list[str] = []
    for _pid, _st, cls, params in rows:
        if cls == "KbOrchestrationRunTask":
            try:
                rid = str((json.loads(params or "{}") or {}).get("runId") or "")
            except (TypeError, json.JSONDecodeError):
                rid = ""
            if rid:
                orch_ids.append(rid)
    if orch_ids:
        from api.models.orchestration import TapeRun

        # 2026-10-20 双表改造：run 可能落正式表（上传链）或 _dev 表（测试执行），
        # 同一 stmt 在两个 env 下执行 → 自动路由到对应表，合并状态与起止时间。
        for env in (ENV_PROD, ENV_DEV):
            with env_scope(env):
                for rid, st, created, updated in db.execute(
                    select(
                        TapeRun.id, TapeRun.status, TapeRun.created_at, TapeRun.updated_at
                    ).where(TapeRun.id.in_(orch_ids))
                ).all():
                    run_meta_by_id[str(rid)] = (
                        _run_state_to_job(str(st or "")),
                        created,
                        updated,
                    )
    agg: dict[str, dict] = {}
    for pid, state, cls, params in rows:
        a = agg.setdefault(str(pid), {"count": 0, "states": {}, "run_span": (None, None)})
        st = str(state or "")
        if cls == "KbOrchestrationRunTask":
            try:
                rid = str((json.loads(params or "{}") or {}).get("runId") or "")
            except (TypeError, json.JSONDecodeError):
                rid = ""
            meta = run_meta_by_id.get(rid)
            if meta:
                st, run_created, run_updated = meta
                if st:
                    a["states"][st] = a["states"].get(st, 0) + 1
                    a["count"] += 1
                    # 时间范围聚合：最早 created_at → 最晚 updated_at（tz 容错同
                    # _run_duration_ms，比较前统一剥 tzinfo，避免 aware/naive 混比）。
                    cur_start, cur_end = a["run_span"]
                    if run_created:
                        c = _naive(run_created)
                        if cur_start is None or c < cur_start:
                            cur_start = c
                    if run_updated:
                        u = _naive(run_updated)
                        if cur_end is None or u > cur_end:
                            cur_end = u
                    a["run_span"] = (cur_start, cur_end)
                    continue
        a["count"] += 1
        a["states"][st] = a["states"].get(st, 0) + 1
    return agg


@router.get("/parents")
def list_parent_jobs(
    _user_id: str = Depends(_require_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = Query(None),
    keyword: str | None = Query(None, description="job id / 文档 id / 编排名模糊匹配"),
    include_system: bool = Query(False, description="包含系统周期任务"),
    db: Session = Depends(get_db),
) -> dict:
    """父任务列表（2026-10-12 任务监控首屏，2026-10-13 合并分页重写）：

    两种父任务统一展示，且同页混排（时间倒序）：
      1. modo_job 链头（parent_id IS NULL 且非系统周期任务）
      2. 编排执行 kb_tape_run（虚拟父任务，子任务 = step_results 节点）

    每个父任务带 child_count（子任务数）与 child_states（各状态聚合），
    供前端首屏显示「进度 X/Y 完成」。

    分页实现（2026-10-13）：kb_tape_run 含大 JSON 列（step_results/bindings
    实测单行可达 ~940KB），SQL 全列 ORDER BY 会 filesort 爆 sort_buffer
    （1038 Out of sort memory，见 dc63463）。因此窄列全量载入
    （只取 id + 时间戳，不触碰大列）+ 应用层合并排序 + 切片 + 按 id 回表：
    既不爆 sort_buffer，也让编排 run 真正混入时间线（此前 run 只在 page==1
    出现，翻页即消失）。状态筛选经 _job_state_to_run 归一化（前端筛选值是
    modo_job 大写枚举，TapeRun.status 是小写——不映射则「选状态就看不到编排」）。
    """
    from api.models.orchestration import TapeRun

    # ── 2026-10-21 性能修复：SQL 侧分页，替代原「窄列全量载入 + 应用层合并排序」──
    # 原实现每次请求把全部父任务 + 全部 run（双表）拉进内存再排序切片，任务量
    # 随累积线性变慢。现两侧各按 (时间,id) 倒序取 top-k（k=offset+page_size），
    # 应用层合并切片 —— 全局前 k 的元素必然在自己表内也排前 k（反证：否则有 k 个
    # 同表元素排在它前面，全局排名 > k），故精确等价全局排序分页，且只触碰窄列
    # （无 filesort 宽行风险，保留 dc63463 的 1038 防护语义）。
    offset = (page - 1) * page_size
    top_k = offset + page_size

    job_stmt = select(Job.id, Job.create_time).where(Job.parent_id.is_(None))
    if not include_system:
        job_stmt = job_stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
    if state:
        job_stmt = job_stmt.where(Job.state == state)
    if keyword:
        like = f"%{keyword}%"
        job_stmt = job_stmt.where(Job.id.like(like) | Job.task_id.like(like))
    job_total = int(db.execute(select(func.count()).select_from(job_stmt.subquery())).scalar() or 0)
    job_rows = db.execute(
        job_stmt.order_by(Job.create_time.desc(), Job.id.desc()).limit(top_k)
    ).all()  # [(id, create_time), ...]

    # 2026-10-14：被链头任务承接的编排 run 不作为顶层父任务行。此前在应用层
    # 用 `task_params LIKE '%runId%'` 全量扫出 attached run id 再过滤；2026-10-21
    # 冗余列后直接下沉为 SQL NOT EXISTS（modo_job 不在双表隔离名单，dev 环境下
    # 子查询仍查正式表——attached 判定与 run 落哪张表无关，job 都在 modo_job）。
    run_stmt = select(TapeRun.id, TapeRun.created_at).where(
        ~(
            select(Job.id)
            .where(Job.parent_id.is_not(None), Job.run_id == TapeRun.id)
            .exists()
        )
    )
    run_state = _job_state_to_run(state) if state else None
    if run_state:
        run_stmt = run_stmt.where(TapeRun.status == run_state)
    if keyword:
        like = f"%{keyword}%"
        run_stmt = run_stmt.where(TapeRun.id.like(like) | TapeRun.tape_name.like(like))
    # 2026-10-20 双表改造：正式表（上传链）+ _dev 表（测试执行）分别窄列查询
    # 合并（同一 stmt 在两个 env 下自动路由到对应表），再进应用层排序。
    run_rows: list[tuple[str, object]] = []
    run_total = 0
    for env in (ENV_PROD, ENV_DEV):
        with env_scope(env):
            run_rows += list(
                db.execute(
                    run_stmt.order_by(TapeRun.created_at.desc(), TapeRun.id.desc()).limit(top_k)
                ).all()
            )  # [(id, created_at), ...]
            run_total += int(
                db.execute(select(func.count()).select_from(run_stmt.subquery())).scalar() or 0
            )

    # ── 应用层合并排序：时间倒序（job.create_time / run.created_at），同刻按 id 倒序 ──
    merged: list[tuple[str, object | None, str]] = [
        (rid, create_time, "job") for rid, create_time in job_rows
    ]
    merged += [(rid, created_at, "run") for rid, created_at in run_rows]
    merged.sort(key=lambda t: (t[1] or datetime.min, t[0]), reverse=True)

    # 两侧各取 top_k（= offset+page_size）合并后切片：精确等价全局排序分页
    page_merged = merged[offset : offset + page_size]

    # ── 按 id 回表取全行 ──
    job_ids = [rid for rid, _ts, kind in page_merged if kind == "job"]
    run_ids = [rid for rid, _ts, kind in page_merged if kind == "run"]
    jobs = (
        db.execute(select(Job).where(Job.id.in_(job_ids))).scalars().all() if job_ids else []
    )
    # 2026-10-20 双表改造：run 回表也按两环境分别查（正式表 + _dev 表）
    runs: list[TapeRun] = []
    if run_ids:
        for env in (ENV_PROD, ENV_DEV):
            with env_scope(env):
                runs += list(
                    db.execute(select(TapeRun).where(TapeRun.id.in_(run_ids))).scalars().all()
                )
    jobs_by_id = {j.id: j for j in jobs}
    runs_by_id = {r.id: r for r in runs}

    agg = _child_agg(db, job_ids)
    doc_names = _job_doc_names(db, jobs)  # 2026-10-15：主任务名显示文档文件名
    items: list[dict] = []
    for rid, _ts, kind in page_merged:
        if kind == "job":
            j = jobs_by_id.get(rid)
            if not j:
                continue
            item = _parent_job_to_dict(
                j,
                child_count=agg.get(j.id, {}).get("count", 0),
                child_states=agg.get(j.id, {}).get("states", {}),
            )
            # 2026-10-20 耗时口径与子任务对齐：链头 job 的 start/end 是「投递」
            # 秒级，其 KbOrchestrationRunTask 子任务已按 run 真实执行时长展示
            # （earliest run.created_at → latest run.updated_at 聚合）。主任务
            # 同样用 run 时间范围覆盖，否则「主任务耗时 << 子任务耗时总和」
            # 对不上账；原始 job 投递耗时保留在 job_start_time/job_end_time/
            # job_duration_ms 供排障对比（与 children 接口同款约定）。
            # Job.start_time/end_time 声明为 object（框架模型 Mapped[object]），
            # 运行期为 naive DATETIME，cast 成 datetime 供 Pyright 精确推导。
            span_start, span_end = agg.get(j.id, {}).get("run_span", (None, None))
            job_start = cast("datetime | None", j.start_time)
            job_end = cast("datetime | None", j.end_time)
            job_start = _naive(job_start)
            job_end = _naive(job_end)
            start = span_start or job_start
            end = span_end or job_end
            if (span_start or span_end) and start and end:
                item["job_start_time"] = item["start_time"]
                item["job_end_time"] = item["end_time"]
                item["job_duration_ms"] = item["duration_ms"]
                item["start_time"] = start.isoformat()
                item["end_time"] = end.isoformat()
                item["duration_ms"] = max(0, int((end - start).total_seconds() * 1000))
            if j.id in doc_names:
                item["doc_name"] = doc_names[j.id]
            items.append(item)
        else:
            r = runs_by_id.get(rid)
            if r:
                items.append(_run_to_job_dict(r))

    return {
        "success": True,
        "data": {
            "items": items,
            "total": job_total + run_total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/{job_id}/children")
def list_job_children(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """父任务的子任务列表（2026-10-12；2026-10-20 编排粒度修正）：

    - modo_job 父任务 → 其 parent_id 指向它的所有子任务
    - 编排 run（虚拟父任务）→ 该编排 run 自身一行概要（**不再把编排内部
      step_results 节点拆成子任务**——编排节点是 run 内部执行步骤，
      归编排详情/日志视图，不作为任务监控子任务粒度）
    """
    from api.models.orchestration import TapeRun

    # 先查是否编排 run（虚拟父任务）；2026-10-20 双表改造：正式 + _dev 双查
    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if not run:
        with env_scope(ENV_DEV):
            run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        return {
            "success": True,
            "data": {
                "kind": "orchestration",
                "items": [_run_to_job_dict(run)],
                "total": 1,
            },
        }
    # modo_job 父任务
    parent = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not parent:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    children = (
        db.execute(select(Job).where(Job.parent_id == job_id).order_by(Job.create_time.asc()))
        .scalars()
        .all()
    )
    # 2026-10-14 编排子任务真实状态：job 状态是「投递成功」语义（建完 run 即
    # SUCCESS），展示层改用编排 run 的真实状态（大写归一化），否则子任务还在
    # 后台跑就显示「成功」（用户反馈）。原 job 状态保留在 job_state 供排障对比。
    # 2026-10-20 粒度修正：不再向子任务列表注入 step_results/run_tape_name——
    # 编排内部节点归编排详情/日志视图，不作为任务监控子任务粒度。
    # 2026-10-21 补 tape_name：用户反馈摘要子任务只显示「编排执行」，看不出是
    # 哪个编排——子任务行附带 run.tape_name（如「摘要-Map-Reduce（分块合并）」），
    # 前端任务类列优先展示编排名（回退 taskClassLabel）。
    items: list[dict] = []
    for c in children:
        d = _job_to_dict(c)
        if c.task_class == "KbOrchestrationRunTask":
            try:
                rid = str((json.loads(c.task_params or "{}") or {}).get("runId") or "")
                if rid:
                    run = db.execute(select(TapeRun).where(TapeRun.id == rid)).scalars().first()
                    if not run:  # 2026-10-20 双表：_dev 表兜底
                        with env_scope(ENV_DEV):
                            run = db.execute(
                                select(TapeRun).where(TapeRun.id == rid)
                            ).scalars().first()
                    if run:
                        d["job_state"] = d["state"]
                        d["state"] = _run_state_to_job(run.status)
                        # 2026-10-21 子任务中文展示：携带编排名（摘要/构建编排名），
                        # runId 供日志抽屉/前端定位真实编排执行记录
                        d["tape_name"] = run.tape_name
                        d["run_id"] = rid
                        # 2026-10-19 耗时口径：编排子任务的 job 耗时是「投递」秒级
                        # （handler 建完 run 即返回），真实执行在 run 上——与 state
                        # 同口径，把耗时/起止时间也按 run（created_at → updated_at）
                        # 覆盖，否则「子任务耗时之和」与主任务总时长对不上账。
                        # 原始 job 投递耗时保留在 job_* 字段供排障对比。
                        d["job_start_time"] = d["start_time"]
                        d["job_end_time"] = d["end_time"]
                        d["job_duration_ms"] = d["duration_ms"]
                        d["start_time"] = run.created_at.isoformat() if run.created_at else None
                        d["end_time"] = run.updated_at.isoformat() if run.updated_at else None
                        d["duration_ms"] = _run_duration_ms(run)
            except (TypeError, json.JSONDecodeError):
                pass
        items.append(d)
    return {
        "success": True,
        "data": {
            "kind": "job",
            "items": items,
            "total": len(items),
        },
    }


# 系统自愈周期任务（默认从任务列表隐藏，避免每 5 分钟刷屏）
SYSTEM_TASK_CLASSES: tuple[str, ...] = ("KbOrphanRecoveryTask",)

# ── 编排 run（kb_tape_run）与任务监控枚举对齐 ──────────────────────────
# 任务监控页的状态枚举沿用 modo_job 的大写形态（PENDING/RUNNING/SUCCESS/
# FAILED/STOPPED），而 TapeRun.status 是小写（queued/running/success/failed/
# cancelled）。筛选/统计/序列化统一经此映射，避免「选了状态就看不到编排」。
_RUN_STATE_TO_JOB = {
    "queued": "PENDING",
    "running": "RUNNING",
    "success": "SUCCESS",
    "failed": "FAILED",
    "cancelled": "STOPPED",
}
_JOB_STATE_TO_RUN = {v: k for k, v in _RUN_STATE_TO_JOB.items()}


def _run_state_to_job(state: str | None) -> str:
    """TapeRun.status（小写）→ 任务监控大写枚举；未知值原样返回。"""
    return _RUN_STATE_TO_JOB.get((state or "").lower(), state or "PENDING")


def _job_state_to_run(state: str | None) -> str | None:
    """任务监控筛选值（大写）→ TapeRun.status（小写）；非编排状态返回 None。"""
    return _JOB_STATE_TO_RUN.get((state or "").upper())


def _naive(dt: datetime | None) -> datetime | None:
    """剥 tzinfo：与 _run_duration_ms 同款容错，供 aware/naive 混比前统一口径。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _run_duration_ms(run: TapeRun) -> int | None:
    """编排 run 真实耗时：updated_at − created_at（2026-10-19 子任务耗时口径）。

    run 的 created_at/updated_at 与 modo_job 的 start_time/end_time 同为 MySQL
    DATETIME 秒精度 naive 列（round-trip 后无 tzinfo）；防御 aware/naive 混用
    （内存中写入时可能带 tzinfo），与 scheduler._update_duration 同款容错。
    """
    start, end = run.created_at, run.updated_at
    if not start or not end:
        return None
    start = start.replace(tzinfo=None) if start.tzinfo else start
    end = end.replace(tzinfo=None) if end.tzinfo else end
    return max(0, int((end - start).total_seconds() * 1000))


def _run_to_job_dict(run: TapeRun) -> dict:
    """编排 run → 任务监控 JobItem 同构序列化（虚拟父任务，state 统一大写）。"""
    steps = run.step_results or []
    states: dict[str, int] = {}
    for s in steps:
        st = str(s.get("status") or "")
        states[st] = states.get(st, 0) + 1
    return {
        "id": run.id,
        "task_id": run.id,
        "task_class": "OrchestrationRun",  # 虚拟任务类（前端按此渲染「编排执行」）
        "tape_name": run.tape_name,  # 2026-10-20：顶层附带编排名，子任务行/日志时间线直接取名
        "queue_name": "orch",
        "trigger_type": "API",
        "state": _run_state_to_job(run.status),
        "start_time": (run.created_at.isoformat() if run.created_at else None),
        "end_time": (run.updated_at.isoformat() if run.updated_at else None),
        "duration_ms": None,
        "error_message": run.error,
        "log_path": None,
        "create_time": (run.created_at.isoformat() if run.created_at else None),
        "params": {"tape_id": run.tape_id, "tape_name": run.tape_name, "inputs": run.inputs or {}},
        "parent_id": None,
        "child_count": len(steps),
        "child_states": states,
    }



@router.get("")
def list_jobs(
    _user_id: str = Depends(_require_user_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    task_class: str | None = Query(None),
    state: str | None = Query(None),
    keyword: str | None = Query(None, description="job id 或 task_id 模糊匹配"),
    queue_name: str | None = Query(None, description="队列名精确匹配"),
    include_system: bool = Query(False, description="包含系统周期任务（默认隐藏，避免 OrphanRecovery 刷屏）"),
    db: Session = Depends(get_db),
) -> dict:
    """任务列表：时间倒序，支持 task_class / state / keyword / queue 过滤 + 分页。

    2026-10-13：并入编排 run（OrchestrationRun 虚拟行），与 modo_job 同页
    混排（时间倒序）。窄列全量载入 + 应用层合并排序切片 + 回表，避免
    kb_tape_run 大 JSON 列 filesort 爆 sort_buffer（1038）。

    系统周期任务（如 KbOrphanRecoveryTask 每 5 分钟一条）默认隐藏，
    include_system=true 或显式 task_class 时才展示。只读。
    """
    from api.models.orchestration import TapeRun

    # ── 2026-10-21 性能修复：SQL 侧分页（同 list_parent_jobs），替代全量载入 ──
    offset = (page - 1) * page_size
    top_k = offset + page_size

    job_stmt = select(Job.id, Job.create_time)
    if task_class:
        job_stmt = job_stmt.where(Job.task_class == task_class)
    elif not include_system:
        job_stmt = job_stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
    if state:
        job_stmt = job_stmt.where(Job.state == state)
    if keyword:
        like = f"%{keyword}%"
        job_stmt = job_stmt.where(Job.id.like(like) | Job.task_id.like(like))
    if queue_name:
        job_stmt = job_stmt.where(Job.queue_name == queue_name)
    job_total = int(db.execute(select(func.count()).select_from(job_stmt.subquery())).scalar() or 0)
    job_rows = db.execute(
        job_stmt.order_by(Job.create_time.desc(), Job.id.desc()).limit(top_k)
    ).all()

    run_rows: list = []
    run_total = 0
    # 编排 run 的任务类恒为 OrchestrationRun、队列恒为 orch：显式筛选
    # 其他值（task_class/queue_name）时 run 不参与。
    if not (task_class and task_class != "OrchestrationRun") and not (
        queue_name and queue_name != "orch"
    ):
        run_stmt = select(TapeRun.id, TapeRun.created_at)
        run_state = _job_state_to_run(state) if state else None
        if run_state:
            run_stmt = run_stmt.where(TapeRun.status == run_state)
        if keyword:
            like = f"%{keyword}%"
            run_stmt = run_stmt.where(TapeRun.id.like(like) | TapeRun.tape_name.like(like))
        # 2026-10-20 双表改造：正式表 + _dev 表分别查（同 stmt 在 env 下自动路由）
        for env in (ENV_PROD, ENV_DEV):
            with env_scope(env):
                run_rows += list(
                    db.execute(
                        run_stmt.order_by(TapeRun.created_at.desc(), TapeRun.id.desc()).limit(top_k)
                    ).all()
                )
                run_total += int(
                    db.execute(select(func.count()).select_from(run_stmt.subquery())).scalar() or 0
                )

    # ── 应用层合并排序 + 切片 + 回表（top-k 合并精确等价全局排序，见 list_parent_jobs）──
    merged: list[tuple[str, object | None, str]] = [
        (rid, create_time, "job") for rid, create_time in job_rows
    ]
    merged += [(rid, created_at, "run") for rid, created_at in run_rows]
    merged.sort(key=lambda t: (t[1] or datetime.min, t[0]), reverse=True)

    page_merged = merged[offset : offset + page_size]

    job_ids = [rid for rid, _ts, kind in page_merged if kind == "job"]
    run_ids = [rid for rid, _ts, kind in page_merged if kind == "run"]
    jobs = (
        db.execute(select(Job).where(Job.id.in_(job_ids))).scalars().all() if job_ids else []
    )
    # 2026-10-20 双表改造：run 回表按两环境分别查（正式表 + _dev 表）
    runs: list[TapeRun] = []
    if run_ids:
        for env in (ENV_PROD, ENV_DEV):
            with env_scope(env):
                runs += list(
                    db.execute(select(TapeRun).where(TapeRun.id.in_(run_ids))).scalars().all()
                )
    jobs_by_id = {j.id: j for j in jobs}
    runs_by_id = {r.id: r for r in runs}

    items: list[dict] = []
    for rid, _ts, kind in page_merged:
        if kind == "job":
            j = jobs_by_id.get(rid)
            if j:
                items.append(_job_to_dict(j))
        else:
            r = runs_by_id.get(rid)
            if r:
                items.append(_run_to_job_dict(r))

    return {
        "success": True,
        "data": {
            "items": items,
            "total": job_total + run_total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/statistics")
def job_statistics(
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """任务统计：按 state 分组计数（对齐 data-synth JobStatistics）。

    2026-10-13：并入编排 run（kb_tape_run）状态——run 状态小写枚举经
    _run_state_to_job 映射为大写后合入对应计数，统计卡与列表口径一致。

    2026-10-21：统计口径对齐「主任务」（/parents 视图），不再按「全部任务」——
      1. modo_job 只统计链头（parent_id IS NULL 且非系统周期任务），
         子任务（解析/切片等挂链头的）不单独计入；
      2. 编排 run 排除被链头任务承接的（attached_run_ids，上传链触发时
         run 已作为链头的子任务展示），与顶层列表一致；
      3. run 双表（正式 + _dev）统计，与列表同口径。
    统计卡与「主任务」表格合计可对上（此前统计含全部子任务导致卡数 > 列表行数）。
    """
    from api.models.orchestration import TapeRun

    # ── modo_job：链头 + 非系统周期任务（与 list_parent_jobs 同款筛选）──
    job_stmt = select(Job.state, func.count()).where(Job.parent_id.is_(None))
    job_stmt = job_stmt.where(Job.task_class.not_in(SYSTEM_TASK_CLASSES))
    rows = db.execute(job_stmt.group_by(Job.state)).all()
    counts: dict[str, int] = {}
    for state, count in rows:
        counts[str(state or "")] = int(count)

    # ── 编排 run：双表统计 + 排除被链头承接的（与 list_parent_jobs 同款）──
    # 双表循环防重复：测试环境 db 被 override 为同一 session 时两轮查到同一批
    # run，按 (id, status) 去重再计数（与 list_parent_jobs 按 id 回表覆盖同语义）。
    run_pairs: set[tuple[str, str]] = set()
    for env in (ENV_PROD, ENV_DEV):
        with env_scope(env):
            run_pairs.update(
                (str(rid), str(status))
                for rid, status in db.execute(
                    select(TapeRun.id, TapeRun.status)
                ).all()
            )
    run_rows: list[tuple[str, str]] = list(run_pairs)
    # 被链头任务承接的 run 不作为顶层父任务行 → 统计同样排除
    if run_rows:
        # 2026-10-21 性能修复：run_id 冗余列等值索引直查（原 task_params LIKE 全文扫）
        attached = db.execute(
            select(Job.run_id, Job.parent_id)
            .where(Job.parent_id.is_not(None), Job.run_id.is_not(None))
        ).all()
        attached_run_ids: set[str] = set()
        for rid, _pid in attached:
            if rid:
                attached_run_ids.add(str(rid))
        if attached_run_ids:
            run_rows = [r for r in run_rows if r[0] not in attached_run_ids]
    for _rid, status in run_rows:
        key = _run_state_to_job(status)
        counts[key] = counts.get(key, 0) + 1

    return {
        "success": True,
        "data": {
            "total": sum(counts.values()),
            "running": counts.get("RUNNING", 0),
            "success": counts.get("SUCCESS", 0),
            "failed": counts.get("FAILED", 0),
            "stopped": counts.get("STOPPED", 0),
            "queued": counts.get("PENDING", 0),
        },
    }


@router.get("/queues")
def job_queues(
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """队列列表：modo_job 实际出现的 queue_name 去重（供筛选下拉）。"""
    rows = db.execute(
        select(Job.queue_name)
        .where(Job.queue_name.is_not(None), Job.queue_name != "")
        .distinct()
    ).all()
    items = [
        {"queueName": str(name), "queueLabel": None}
        for (name,) in rows
        if str(name or "").strip()
    ]
    items.sort(key=lambda q: q["queueName"].lower())
    return {"success": True, "data": items}


@router.post("/{job_id}/stop")
def stop_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """停止任务：运行中（RUNNING）或排队中（PENDING）可停，置为 STOPPED。

    置 STOPPED 后 worker 执行前检查（scheduler.execute_modo_job 的
    "job not executable" 守卫）会拒执行；执行中停掉的任务，handler 跑完后
    终态保护保证不再覆盖成 SUCCESS/FAILED（2026-10-05 修复「取消复活」）。

    2026-10-13：编排 run（OrchestrationRun）也支持停止——run 在
    queued/running 时置 cancelled，引擎 advance 的终态守卫
    （status not in (queued, running) 即不再推进）保证不再执行后续节点。
    """
    from api.models.orchestration import TapeRun

    # 2026-10-20 双表改造：正式 + _dev 双查（测试执行的 run 在 dev 表）；
    # 记录 run_env 使状态回写落到同一张表（atomic flush 走对应表路由）。
    run_env: str | None = None
    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        run_env = ENV_PROD
    else:
        with env_scope(ENV_DEV):
            run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
        if run:
            run_env = ENV_DEV
    if run:
        if run.status not in ("queued", "running"):
            raise HTTPException(
                status_code=400, detail=f"编排 run 当前状态 {run.status}，不可停止"
            )
        run.status = "cancelled"
        run.error = (run.error or "") + " [任务监控页手动停止]"
        with env_scope(run_env or ENV_PROD):
            db.add(run)
            db.commit()
        return {
            "success": True,
            "data": {"id": job_id, "state": "STOPPED", "kind": "orchestration"},
        }

    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state not in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=400, detail=f"任务当前状态 {job.state}，不可停止")
    job.state = "STOPPED"
    job.end_time = func.now()
    db.add(job)
    db.commit()
    return {"success": True, "data": {"id": job_id, "state": "STOPPED"}}


_RETRYABLE_STATES = ("FAILED", "STOPPED")
_AGENT_TASK_CLASSES = ("KbAgentWikiBuildTask", "KbAgentGatewayTask")


@router.post("/{job_id}/retry")
def retry_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """重试任务：FAILED/STOPPED 可重试，重置为 PENDING 并重新入队执行。

    重新投递到任务原队列（job.queue_name，空则按 task_class 推断：
    agent 类任务 → agent 队列，其余 → default），与上传自动链的投递
    契约一致（send_task execute_modo_job + task_id=job_id）。
    """
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state not in _RETRYABLE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"任务当前状态 {job.state}，不可重试（仅 {_RETRYABLE_STATES} 可重试）",
        )
    original_state = job.state
    queue = (job.queue_name or "").strip() or (
        "agent" if (job.task_class or "") in _AGENT_TASK_CLASSES else "default"
    )
    # 先重置为 PENDING（清失败/取消痕迹），再投递；投递失败回滚状态，避免卡死 PENDING
    job.state = "PENDING"
    job.error_message = None
    job.end_time = None
    job.duration_ms = None
    db.commit()
    try:
        from worker.celery_app import celery_app

        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue=queue,
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时回滚，任务保持 FAILED/STOPPED 可再试
        db.refresh(job)
        job.state = original_state
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"重试入队失败，状态已回滚为 {original_state}: {exc}",
        ) from exc
    return {"success": True, "data": {"id": job_id, "state": "PENDING", "queue": queue}}


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """删除任务记录（modo_job 行；2026-10-13 起支持编排 run 记录）。

    2026-10-05 实测: 运行中/排队中任务直接删行，agent 仍会跑完（孤儿页）——
    删除前先 revoke celery（terminate），真正取消执行。
    """
    from api.models.orchestration import TapeRun

    # 2026-10-20 双表改造：正式 + _dev 双查，删除在对应表执行
    run_env: str | None = None
    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        run_env = ENV_PROD
    else:
        with env_scope(ENV_DEV):
            run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
        if run:
            run_env = ENV_DEV
    if run:
        with env_scope(run_env or ENV_PROD):
            db.delete(run)
            db.commit()
        return {"success": True, "data": {"id": job_id, "deleted": True, "kind": "orchestration"}}

    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    if job.state in ("PENDING", "RUNNING"):
        try:
            from worker.celery_app import celery_app

            celery_app.control.revoke(job_id, terminate=True)
        except Exception as exc:  # noqa: BLE001 — revoke 失败不阻塞删除
            logger.warning("revoke job %s failed: %s", job_id, exc)
    db.delete(job)
    db.commit()
    return {"success": True, "data": {"id": job_id, "deleted": True}}


@router.get("/{job_id}")
def get_job(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """单任务详情（含解析后的 task_params / error_message 全文）。

    2026-10-13：编排 run（OrchestrationRun）同样可查——JobLogDrawer
    打开编排行的「日志」走的就是本端点，此前 404。
    """
    from api.models.orchestration import TapeRun

    # 2026-10-20 双表改造：正式 + _dev 双查（测试执行的 run 在 dev 表）
    run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if not run:
        with env_scope(ENV_DEV):
            run = db.execute(select(TapeRun).where(TapeRun.id == job_id)).scalars().first()
    if run:
        return {"success": True, "data": _run_to_job_dict(run)}

    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    d = _job_to_dict(job)
    # 2026-10-21：run 承接 job（SUMMARY_RUN_*/ORCH_RUN_* 编排子任务）附带
    # tape_name/run_id，日志抽屉基本信息可展示编排名（与 list_job_children 同口径）
    if job.task_class == "KbOrchestrationRunTask":
        try:
            rid = str((json.loads(job.task_params or "{}") or {}).get("runId") or "")
            if rid:
                run = db.execute(select(TapeRun).where(TapeRun.id == rid)).scalars().first()
                if not run:
                    with env_scope(ENV_DEV):
                        run = db.execute(select(TapeRun).where(TapeRun.id == rid)).scalars().first()
                if run:
                    d["tape_name"] = run.tape_name
                    d["run_id"] = rid
        except (TypeError, json.JSONDecodeError):
            pass
    return {"success": True, "data": d}


@router.get("/{job_id}/trace")
def get_job_trace(
    job_id: str,
    _user_id: str = Depends(_require_user_id),
) -> dict:
    """Agent 任务 span 明细：读 logs/traces/{job_id}.json（worker 落盘）。"""
    path = os.path.join(get_settings().kb_storage_dir, f"logs/traces/{job_id}.json")
    if not os.path.isfile(path):
        # 直跑任务无 agent span，但技能 LLM 事件摘要存在（steps/tree 执行轨迹）
        ev_path = os.path.join(get_settings().kb_storage_dir, f"logs/events/{job_id}.summary.json")
        events: dict | None = None
        if os.path.isfile(ev_path):
            try:
                with open(ev_path, encoding="utf-8") as f:
                    events = json.load(f)
            except (OSError, json.JSONDecodeError):
                events = None
        return {
            "success": True,
            "data": {
                "spans": [],
                "has_trace": False,
                "trace_kind": "direct" if events else None,
                "summary": {"span_count": 0, "duration_ms": 0, "llm_calls": 0, "tools": []},
                "events": events,
            },
        }
    try:
        with open(path, encoding="utf-8") as f:
            spans = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"trace 读取失败: {exc}")

    # 摘要（与 worker/agent/trace_store.summarize_spans 同语义，api-server 无 worker 包）
    def _span_name(sp: dict) -> str:
        sd = sp.get("span_data") or {}
        return str(sd.get("name") or ("LLM generation" if sd.get("type") == "generation" else sd.get("type") or "span"))

    def _span_duration_ms(sp: dict) -> int:
        try:
            start = datetime.fromisoformat(str(sp.get("started_at")).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(sp.get("ended_at")).replace("Z", "+00:00"))
            return max(0, int((end - start).total_seconds() * 1000))
        except Exception:  # noqa: BLE001
            return 0

    total_ms = 0.0
    tools: list[str] = []
    llm_calls = 0
    enriched: list[dict] = []
    for sp in spans:
        dur = _span_duration_ms(sp)
        total_ms += dur
        sd = sp.get("span_data") or {}
        stype = str(sd.get("type") or "")
        if stype == "function":
            fn = str(sd.get("name") or "")
            if fn and fn not in tools:
                tools.append(fn)
        elif stype == "generation":
            llm_calls += 1
        # 顶层便捷字段（前端直接读）：name / type / duration_ms
        enriched.append(
            {
                **sp,
                "name": _span_name(sp),
                "type": stype or "span",
                "duration_ms": dur,
            }
        )
    summary = {
        "span_count": len(spans),
        "duration_ms": round(total_ms),
        "llm_calls": llm_calls,
        "tools": tools,
    }
    # 技能 LLM 事件摘要（logs/events/{job_id}.summary.json，worker 聚合落盘）
    events: dict | None = None
    ev_path = os.path.join(get_settings().kb_storage_dir, f"logs/events/{job_id}.summary.json")
    if os.path.isfile(ev_path):
        try:
            with open(ev_path, encoding="utf-8") as f:
                events = json.load(f)
        except (OSError, json.JSONDecodeError):
            events = None
    return {
        "success": True,
        "data": {
            "spans": enriched,
            "has_trace": True,
            "trace_kind": "agent",
            "summary": summary,
            "events": events,
        },
    }


# ---------------------------------------------------------------------------
# 实时日志流（SSE）——对齐 data-synth job-monitor/log-stream
# ---------------------------------------------------------------------------

# 全量日志（2026-10-21 重构）：黑色日志框不再只有 error_message 进度，
# 而是把任务的所有可读执行记录合并成一个「全量日志」流。数据源按展示顺序：
#   1. local  —— logs/jobs/<job_id>.log（scheduler 写：任务开始/失败/成功/取消）
#   2. minio  —— MinIO logs/<job_id>.log（worker append_job_log 追加：agent poll
#                进度 / wiki_build 进度 / LLM 汇总行；与 error_message 槽位双写，
#                MinIO 可用时以它为唯一进度源，槽位仅兜底，避免同一行推两遍）
#   3. slot   —— modo_job.error_message 槽位（无 MinIO 环境的兜底进度源）
#   4. events —— logs/events/<job_id>.jsonl（技能脚本 measure/LLM 事件，格式化
#                成可读文本：步骤开始/完成/失败 + LLM 调用成功/失败/重试）
# 每源独立字节游标（cursors dict），互不挤占；终态/空闲超时语义不变。
TERMINAL_STATES = {"SUCCESS", "FAILED", "STOPPED"}

_EVENT_STATUS_CN = {
    "start": "开始",
    "done": "完成",
    "fail": "失败",
    "interrupted": "中断",
}


def _format_event_line(ev: dict) -> str:
    """events jsonl 事件行 → 可读日志文本行（黑色框全量日志用）。"""
    ts = str(ev.get("ts") or "")
    kind = str(ev.get("kind") or "")
    phase = str(ev.get("phase") or "")
    if kind == "step":
        step = str(ev.get("step") or "?")
        status = _EVENT_STATUS_CN.get(str(ev.get("status") or ""), str(ev.get("status") or ""))
        ms = ev.get("ms")
        ms_txt = f"，耗时{int(ms)}ms" if isinstance(ms, (int, float)) else ""
        return f"[{ts}] [步骤] {step} {status}{ms_txt}"
    if kind == "ok":
        extra: list[str] = []
        ms = ev.get("llm_ms")
        if isinstance(ms, (int, float)):
            extra.append(f"耗时{int(ms)}ms")
        cl = ev.get("content_len")
        if isinstance(cl, (int, float)):
            extra.append(f"输出{int(cl)}字符")
        fin = str(ev.get("finish") or "")
        if fin:
            extra.append(f"finish={fin}")
        rts = ev.get("retries")
        if rts:
            extra.append(f"重试{rts}次")
        return f"[{ts}] [LLM:{phase or '-'}] 调用成功" + (
            f"（{'，'.join(extra)}）" if extra else ""
        )
    if kind == "error":
        err = str(ev.get("error") or ev.get("message") or "")
        ms = ev.get("llm_ms")
        ms_txt = f"，耗时{int(ms)}ms" if isinstance(ms, (int, float)) else ""
        return f"[{ts}] [LLM:{phase or '-'}] 调用失败{ms_txt}: {err}"
    if kind == "retry":
        reason = str(ev.get("reason") or "")
        attempt = ev.get("attempt")
        att_txt = f" 第{attempt}次" if attempt else ""
        bs = ev.get("backoff_s")
        bs_txt = f"，退避{int(bs)}s" if isinstance(bs, (int, float)) else ""
        return f"[{ts}] [LLM:{phase or '-'}] 重试{att_txt}（{reason}{bs_txt}）"
    # 未知 kind：紧凑 JSON 兜底，不让任何事件静默丢失
    try:
        return f"[{ts}] {json.dumps(ev, ensure_ascii=False, sort_keys=True)}"
    except (TypeError, ValueError):
        return f"[{ts}] {ev}"


def _read_events_incremental(job_id: str, cursor: int) -> tuple[str, int]:
    """logs/events/<job_id>.jsonl 增量读取，逐行格式化为可读文本。

    events jsonl 由技能脚本子进程（WIKI_EVENTS_LOG）逐行追加；同一 job 的
    events 文件名与 job_id 一致（worker agent_worker._inject_direct_env /
    runtime._inject_task_env 按 job_id/task_id 落盘）。best-effort：
    文件不存在/读取失败返回空，不使 SSE 流出错。
    """
    try:
        ev_path = os.path.join(get_settings().kb_storage_dir, f"logs/events/{job_id}.jsonl")
        if not os.path.isfile(ev_path):
            return "", cursor
        size = os.path.getsize(ev_path)
        if size <= cursor:
            return "", cursor
        with open(ev_path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(cursor)
            raw = fh.read()
        lines: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(ev, dict):
                lines.append(_format_event_line(ev))
        text = ("\n".join(lines) + "\n") if lines else ""
        return text, size
    except Exception:  # noqa: BLE001 — best-effort
        return "", cursor


def _read_incremental(
    db: Session,
    job: Job,
    cursors: dict[str, int],
) -> dict:
    """全量日志增量快照：合并 local 文件 / events / MinIO / error_message。

    cursors 为每源独立字节游标（SSE 连接级持有，跨轮累计）；返回各源
    「cursor 之后的新内容」拼接文本 + 更新后的游标。顺序即展示顺序：
    生命周期行（local）→ 进度行（minio/slot）→ 事件明细（events）。
    """
    content = ""
    source = "none"
    for _k in ("local", "minio", "slot", "events"):
        cursors.setdefault(_k, 0)

    # 1) 本地日志文件 logs/jobs/<job_id>.log（scheduler execute_modo_job 写入，
    #    Job.log_path 落库；ff91de8 起支持）——生命周期行（开始/失败/成功）
    if job.log_path:
        abs_path = job.log_path if os.path.isabs(job.log_path) else os.path.join(
            get_settings().kb_storage_dir, job.log_path
        )
        if os.path.exists(abs_path):
            try:
                size = os.path.getsize(abs_path)
                cur = cursors["local"]
                if size > cur:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(cur)
                        content = fh.read()
                    cursors["local"] = size
                    source = "local"
            except OSError:
                pass

    # 2) MinIO 日志对象 logs/<job_id>.log（worker log_sink.append_job_log 追加写入）。
    #    与 error_message 槽位双写 —— MinIO 可用时它就是进度源的唯一真相，
    #    槽位跳过（否则同一行推两遍）。
    minio_ok = False
    if not content:
        try:
            from api.services import storage as storage_svc

            if storage_svc.remote_enabled():
                minio_log = (
                    f"minio://{storage_svc.get_settings().MINIO_BUCKET or 'kb-compilation'}"
                    f"/logs/{job.id}.log"
                )
                size = storage_svc.stat_size(minio_log)
                if size is not None and size > cursors["minio"]:
                    data = storage_svc.get_bytes(minio_log)
                    text = data.decode("utf-8", errors="replace")
                    content = text[cursors["minio"]:]
                    cursors["minio"] = size
                    source = "minio"
                elif size is not None:
                    minio_ok = True
        except Exception:  # noqa: BLE001 — 日志读取失败回退 error_message
            pass

    # 3) error_message 槽位（无 MinIO 环境的实时进度兜底；agent-gateway 每轮
    #    poll 覆盖写入，log_sink._write_error_slot 截断 2000 字符）
    if not content and not minio_ok and job.error_message:
        err = str(job.error_message)
        cur = cursors["slot"]
        if len(err) > cur:
            content = err[cur:]
            cursors["slot"] = len(err)
            source = "error_message"
        elif len(err) == cur:
            source = "error_message"

    # 4) events jsonl（技能脚本 measure/LLM 事件，格式化可读文本）——追加在
    #    生命周期行/进度行后，展示「步骤+LLM 调用」全量明细。
    ev_text, ev_cursor = _read_events_incremental(job.id, cursors["events"])
    if ev_text:
        content = (content + ev_text) if content else ev_text
        cursors["events"] = ev_cursor
        if source == "none":
            source = "events"

    return {"content": content, "source": source}


def _run_log_snapshot(run) -> str:
    """编排 run → 全量日志快照文本（step_results 节点逐行 + run 级信息）。

    供 log-stream 编排 run 快照模式使用；模块级便于单测。鸭子类型参数，
    只需 tape_name / status / created_at / updated_at / error / step_results。
    """
    lines = [
        f"编排执行 {run.id}",
        f"磁带: {run.tape_name or '-'}  状态: {_run_state_to_job(run.status)}",
    ]
    if run.created_at:
        lines.append(f"创建: {run.created_at}  更新: {run.updated_at or '-'}")
    if run.error:
        lines.append(f"错误: {run.error}")
    steps = run.step_results or []
    if not steps:
        lines.append("（暂无节点执行记录）")
    for i, s in enumerate(steps, 1):
        label = str(s.get("step_label") or s.get("step_inst") or s.get("step_id") or "?")
        st = str(s.get("status") or "?")
        dur = s.get("duration_ms")
        dur_txt = f"，耗时{int(dur)}ms" if isinstance(dur, (int, float)) else ""
        err = str(s.get("error") or "")
        err_txt = f"，错误: {err}" if err else ""
        lines.append(f"[{i}] 节点 {label} {st}{dur_txt}{err_txt}")
    return "\n".join(lines)


@router.get("/{job_id}/log-stream")
async def job_log_stream(
    request: Request,
    job_id: str,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    offset: int = Query(0, ge=0),
):
    """SSE 实时日志流：全量日志增量推送（2026-10-21 重构）。

    语义对齐 data-synth /api/job-monitor/log-stream：
    - data: {"type":"chunk","payload":{content,offset,state,source,ts}} —— 增量追加
    - data: {"type":"snapshot","payload":{content,state,ts}} —— 整体快照替换
      （编排 run 用：step_results 整体变化，快照替换比增量更干净）
    - data: {"type":"warning"|"error"|"finish", ...}
    终态（SUCCESS/FAILED/STOPPED）后自动 close；1s 轮询；30s 空闲超时。

    两种任务形态：
      1. modo_job（文档处理链/agent 任务）——多源合并增量（local 日志文件 +
         events jsonl + MinIO + error_message 槽位，每源独立游标 cursors）
      2. 编排 run（kb_tape_run 虚拟任务）——step_results 快照模式：每轮把
         run 的节点执行记录格式化成文本，内容变化时发 snapshot 整体替换，
         让编排任务在黑色框里也能看到全量执行明细（此前查不到 modo_job
         行直接 finish NOT_FOUND，日志框永远是空的）。

    **连接不持有长 DB session**：每轮查询用独立短会话（get_sessionmaker()()）
    轮末即还——SSE 流可能持续数秒到数十秒，若用 Depends(get_db) 的 session，
    并发日志流会占满 QueuePool(size 10) 导致整个 api-server 的 DB 接口
    TimeoutError 挂起（实测：EventSource 重连风暴 + 多流并发 → 全部接口 30s 超时）。
    """
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")

    POLL_INTERVAL_S = 1.0
    MAX_IDLE_LOOPS = 30

    async def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def _find_run() -> object | None:
        """查 kb_tape_run（正式表 + _dev 表双查，同 get_job 语义）。

        2026-10-21 扩展：传入 id 可能是 "run 承接 job"（SUMMARY_RUN_*/ORCH_RUN_*
        等 KbOrchestrationRunTask 的 modo_job 行，task_params.runId 指向真实
        run）——按 job id 查不到 run 时，解析 task_params.runId 再查，命中即走
        run 快照流。否则摘要任务日志框只有 scheduler 生命周期两行，用户看不到
        124 条 step_results 执行明细（反馈「日志为什么只有一点点」）。
        """
        def _query_run(rid: str) -> object | None:
            from api.models.orchestration import TapeRun

            with get_sessionmaker()() as sdb:
                run = sdb.execute(
                    select(TapeRun).where(TapeRun.id == rid)
                ).scalars().first()
                if run:
                    return run
                with env_scope(ENV_DEV):
                    run = sdb.execute(
                        select(TapeRun).where(TapeRun.id == rid)
                    ).scalars().first()
                return run

        try:
            run = _query_run(job_id)
            if run:
                return run
            # run 承接 job：解析 task_params.runId 定位真实编排执行
            with get_sessionmaker()() as sdb:
                job = sdb.execute(
                    select(Job).where(Job.id == job_id)
                ).scalars().first()
            if not job or job.task_class != "KbOrchestrationRunTask":
                return None
            try:
                rid = str((json.loads(job.task_params or "{}") or {}).get("runId") or "")
            except (TypeError, json.JSONDecodeError):
                return None
            if not rid:
                return None
            return _query_run(rid)
        except Exception:  # noqa: BLE001 — 非编排环境/表缺失 → 走 modo_job 流
            return None

    async def _generate_run(run_id: str):
        """编排 run：step_results 快照流（snapshot 替换 + 终态/空闲 finish）。

        run_id 为真实 kb_tape_run id——调用方 _find_run 已处理「run 承接 job」
        （SUMMARY_RUN_* 等传入的是 modo_job id 时按 task_params.runId 解析）。
        """
        from api.models.orchestration import TapeRun  # noqa: F401 （函数内延迟导入，同 _find_run）

        last_sig: tuple | None = None
        idle_loops = 0
        terminal_grace = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                with get_sessionmaker()() as sdb:
                    run = sdb.execute(
                        select(TapeRun).where(TapeRun.id == run_id)
                    ).scalars().first()
                    if not run:
                        with env_scope(ENV_DEV):
                            run = sdb.execute(
                                select(TapeRun).where(TapeRun.id == run_id)
                            ).scalars().first()
                    if not run:
                        yield await _sse({"type": "finish", "payload": {
                            "state": "NOT_FOUND", "reason": "作业不存在",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }})
                        break
                    state = str(run.status or "").lower()
                    text = _run_log_snapshot(run)
                    sig = (state, text)
                if sig != last_sig:
                    last_sig = sig
                    idle_loops = 0
                    terminal_grace = 0
                    yield await _sse({"type": "snapshot", "payload": {
                        "content": text, "state": state,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }})
                else:
                    idle_loops += 1
                    # 终态后内容不再变化 → 快照已是最新，收尾
                    if state in ("success", "failed", "cancelled"):
                        terminal_grace += 1
                        if terminal_grace >= 1:
                            yield await _sse({"type": "finish", "payload": {
                                "state": state,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }})
                            break

                if idle_loops >= MAX_IDLE_LOOPS:
                    yield await _sse({"type": "finish", "payload": {
                        "state": state or "TIMEOUT",
                        "reason": "SSE idle timeout",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }})
                    break

                await asyncio.sleep(POLL_INTERVAL_S)
        except Exception as exc:  # noqa: BLE001
            yield await _sse({"type": "error", "payload": {
                "message": str(exc),
                "ts": datetime.now(timezone.utc).isoformat(),
            }})

    async def _generate_job():
        offset_bytes = max(0, offset)
        cursors: dict[str, int] = {
            "local": offset_bytes,
            "minio": offset_bytes,
            "slot": offset_bytes,
            "events": 0,
        }
        idle_loops = 0
        terminal_grace = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 独立短会话：每轮查询即开即关，SSE 流期间不占连接池
                with get_sessionmaker()() as sdb:
                    job = sdb.execute(
                        select(Job).where(Job.id == job_id)
                    ).scalars().first()
                    if not job:
                        yield await _sse({"type": "finish", "payload": {
                            "state": "NOT_FOUND", "reason": "作业不存在",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }})
                        break

                    state = str(job.state or "").upper()
                    read = _read_incremental(sdb, job, cursors)

                if read["content"]:
                    idle_loops = 0
                    terminal_grace = 0
                    yield await _sse({"type": "chunk", "payload": {
                        "content": read["content"],
                        "state": state, "source": read["source"],
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }})
                else:
                    idle_loops += 1

                if state in TERMINAL_STATES:
                    if not read["content"]:
                        terminal_grace += 1
                    if terminal_grace >= 1:
                        yield await _sse({"type": "finish", "payload": {
                            "state": state, "source": read["source"],
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }})
                        break

                if idle_loops >= MAX_IDLE_LOOPS:
                    yield await _sse({"type": "finish", "payload": {
                        "state": state or "TIMEOUT",
                        "reason": "SSE idle timeout",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }})
                    break

                await asyncio.sleep(POLL_INTERVAL_S)
        except Exception as exc:  # noqa: BLE001
            yield await _sse({"type": "error", "payload": {
                "message": str(exc),
                "ts": datetime.now(timezone.utc).isoformat(),
            }})

    # 编排 run（虚拟任务 / run 承接 job）→ 快照流；否则 modo_job 全量增量流。
    # _find_run 已把 SUMMARY_RUN_*/ORCH_RUN_* 等承接 job 解析到真实 run id
    run_obj = _find_run()
    if run_obj is not None:
        run_id = str(getattr(run_obj, "id"))
        generator = _generate_run(run_id)
    else:
        generator = _generate_job()

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
