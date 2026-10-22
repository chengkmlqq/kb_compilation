"""构建任务完成检查 API（2026-10-22，质量评估目录「构建任务检查」子菜单）。

背景：任务监控页把构建链任务（文档解析 / 摘要编排 / wiki构建编排 / 题材分析编排）
显示为「成功」，但历史上出现过真「假成功」：
- ORCH_RUN_* job 由 scheduler 对 execute_run 异步投递立即返回 success 标记成功，
  run 真实 failed 时任务监控仍显示成功（2026-10-21 已修 _sync_orch_job_status 回写，
  但存量漏网 + 别的形态仍可能存在）；
- run.status=success 但节点实际 failed/skipped（引擎内部矛盾）；
- run 成功但产物缺失（wiki 页没生成、摘要没落库、领域没选定）——「成功」≠「真的产出」。

本模块把构建链任务逐条拉出（modo_job 侧三类任务类 + kb_tape_run 双表编排名匹配），
对每条做 4 类检查：
1. consistency  — 页面显示状态（modo_job.state） vs 真实状态（kb_tape_run.status /
   kb_document.parse_state）是否一致；
2. internal     — run 终态 success 但节点存在 failed/skipped（内部假成功）；
3. artifact     — 产物验证：摘要落库 / wiki 页产出 / 领域编排选定；
4. zombie       — queued/running 长时间未推进（worker 挂/卡死）。

鉴权：_require_caller（登录态；与质量评估页一致，只读检查不涉及编排操作）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import ENV_DEV, ENV_PROD, env_scope, get_db
from api.models.framework import Job
from api.services.identity import decode_identity_cookie
from api.services.scope import is_admin

logger = logging.getLogger("kb.quality_build_check")

router = APIRouter(prefix="/quality/build-check", tags=["quality"])

# ── 范围与分类 ────────────────────────────────────────────────
# 解析类任务（无编排 run，纯 modo_job 形态）
PARSE_TASK_CLASSES = ("KbDocumentProcessTask", "KbPreChunkedIngestTask")
# 编排承接任务（task_params.runId 指向 kb_tape_run）
ORCH_TASK_CLASS = "KbOrchestrationRunTask"

KIND_LABELS: dict[str, str] = {
    "doc_parse": "文档解析",
    "summary": "摘要生成",
    "build": "Wiki构建",
    "domain_resolve": "题材分析",
    "unknown": "无法判定",
}

RESULT_LABELS: dict[str, str] = {
    "ok": "真实完成",
    "fake_success": "疑似假成功",
    "failed": "失败",
    "running": "进行中",
    "stale": "疑似卡死",
    "unknown": "无法判定",
}

# 僵尸阈值：同 NODE_STALE_S（orch_runner）6h——超过仍未推进视为执行进程已消失
STALE_TD = timedelta(hours=6)

_RUN_TO_JOB = {
    "queued": "PENDING",
    "running": "RUNNING",
    "success": "SUCCESS",
    "failed": "FAILED",
    "cancelled": "STOPPED",
}
_JOB_TO_RUN = {v: k for k, v in _RUN_TO_JOB.items()}

# wiki构建编排的建页产物统计键（seed_tape_data/wiki构建-*.json 实测：
# n11 关键词建页 kw_created/kw_merged、n13 规则页 rule_created、n14 实体页
# biz_created/biz_merged、n06 摘要页 summary_slug）。
BUILD_ARTIFACT_KEYS = (
    "summary_slug",
    "kw_created",
    "kw_merged",
    "rule_created",
    "rule_merged",
    "biz_created",
    "biz_merged",
)


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return {"user_id": identity.user_id, "team_name": identity.team_name or "",
            "is_admin": is_admin(db, identity.user_id)}


# ── 基础工具 ──────────────────────────────────────────────────

def _classify_run(tape_name: str | None, inputs: dict | None) -> str | None:
    """按编排名/输入把编排 run 归类为构建链类型；非构建链返回 None。

    顺序：摘要（purpose 标记优先）→ 题材分析 → wiki构建/表构建 → 其他。
    """
    name = (tape_name or "").strip()
    if (inputs or {}).get("purpose") == "summary" or name.startswith("摘要"):
        return "summary"
    if "题材分析" in name or "领域判定" in name:
        return "domain_resolve"
    if "wiki构建" in name or "表构建" in name or "构建" in name:
        return "build"
    return None


def _parse_job_params(job: Job) -> dict:
    try:
        p = json.loads(job.task_params or "{}")
        return p if isinstance(p, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _find_run(db: Session, run_id: str) -> tuple[Any | None, str | None]:
    """双表（prod/_dev）查编排 run，返回 (run, env)。"""
    from api.models.orchestration import TapeRun

    for env in (ENV_PROD, ENV_DEV):
        with env_scope(env):
            r = db.execute(select(TapeRun).where(TapeRun.id == run_id)).scalars().first()
            if r is not None:
                return r, env
    return None, None


def _val_nonempty(v: Any) -> bool:
    """产物统计值「非空」判定：数字 > 0 / 字符串非空 / 其他有值。"""
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return bool(v)


def _fmt_value(v: Any) -> str:
    if isinstance(v, str) and len(v) > 30:
        return v[:30] + "…"
    return str(v)


def _ts_utc(v: Any) -> datetime | None:
    """aware/naive 统一为 UTC aware（MySQL DATETIME 秒精度 naive 列）。"""
    if v is None or not isinstance(v, datetime):
        return None
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


def _iso(ts: Any) -> str | None:
    """任意时间值安全序列化（Job.create_time 等列类型标注为 object）。"""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    if hasattr(ts, "isoformat"):
        try:
            return ts.isoformat()
        except Exception:  # noqa: BLE001
            return None
    return None


# ── 四项检查 ──────────────────────────────────────────────────

def _check_consistency(display_state: str | None, real_state: str | None) -> dict:
    """检查 1：页面显示状态 vs 真实状态。

    display_state：任务监控展示值（modo_job.state 大写 / run 映射大写）；
    real_state：真实状态（run.status 小写 / 由 parse_state 推断的 success|failed|running）。
    返回 {"ok": bool, "detail": str}。
    """
    disp = (display_state or "").upper()
    real = (real_state or "").lower()
    if not disp or not real:
        return {"ok": None, "detail": "缺少状态信息，无法对比"}
    if disp == "SUCCESS" and real not in ("success", "ready"):
        return {"ok": False, "detail": f"任务监控显示「成功」，但真实状态为 {real}（{_RUN_TO_JOB.get(real, real)}）"}
    if disp in ("PENDING", "RUNNING") and real in ("success", "failed", "cancelled"):
        return {"ok": False, "detail": f"任务监控显示「{disp}」，但真实状态已终态（{real}），job 未回写"}
    if disp == "FAILED" and real == "success":
        return {"ok": False, "detail": "任务监控显示「失败」，但真实状态为 success（假失败）"}
    return {"ok": True, "detail": f"显示 {disp} ↔ 真实 {real}，一致"}


def _check_run_internal(steps: list | None) -> dict:
    """检查 2：run 内部节点一致性——终态 success 但节点存在 failed/skipped。"""
    results = [s for s in (steps or []) if isinstance(s, dict)]
    if not results:
        return {"ok": None, "detail": "无节点执行记录（空编排或未执行）"}
    states: dict[str, int] = {}
    for r in results:
        st = str(r.get("status") or "")
        states[st] = states.get(st, 0) + 1
    failed = states.get("failed", 0)
    skipped = states.get("skipped", 0)
    if failed:
        return {"ok": False, "detail": f"节点存在 {failed} 个失败（success {states.get('success', 0)} / skipped {skipped}）——run 成功与节点矛盾"}
    if skipped and states.get("success", 0) == 0:
        return {"ok": False, "detail": f"节点全部 skipped（{skipped} 个）——编排空转无实际执行"}
    if skipped:
        return {"ok": True, "detail": f"节点 {states.get('success', 0)} 成功 / {skipped} skipped（阻断跳过，属正常传播）"}
    return {"ok": True, "detail": f"节点全部成功（{states.get('success', 0)} 个）"}


def _check_artifacts(
    kind: str, bindings: dict | None, inputs: dict | None,
    kb_id: str, doc_id: str, run_env: str, db: Session,
) -> dict:
    """检查 3：产物验证——run 成功 ≠ 真的产出。

    按类型核对落库产物（与 run 同环境路由，dev run 查 _dev 表）：
    - summary：kb_document.summary_status == READY
    - build：bindings 建页统计键，兜底 wiki_page 页面计数
    - domain_resolve：bindings.resolved_tape_id 是否选定构建编排
    """
    from api.models.knowledge import KbDocument, WikiPage

    bindings = bindings or {}
    inputs = inputs or {}
    if kind == "summary":
        did = doc_id or str(inputs.get("document_id") or inputs.get("doc_id") or "").strip()
        if not did:
            return {"ok": None, "detail": "无 document_id，跳过摘要产物检查"}
        with env_scope(run_env):
            row = db.execute(
                select(KbDocument.summary_status, KbDocument.summary).where(KbDocument.id == did)
            ).first()
        if row is None:
            return {"ok": False, "detail": "目标文档不存在或已删除"}
        st, summary = row
        if st == "READY":
            return {"ok": True, "detail": f"文档摘要已落库（{len(summary or '')} 字符）"}
        if st == "FAILED":
            return {"ok": False, "detail": "文档摘要状态为 FAILED"}
        return {"ok": False, "detail": f"摘要编排成功但 kb_document.summary_status={st or '空'}——摘要未落库"}
    if kind == "build":
        hits = {k: v for k, v in bindings.items() if k in BUILD_ARTIFACT_KEYS and _val_nonempty(v)}
        if hits:
            detail = "，".join(f"{k}={_fmt_value(v)}" for k, v in hits.items())
            return {"ok": True, "detail": f"构建产物统计：{detail}"}
        # 兜底：wiki 页面计数（单文档 run 按 source_refs 含 doc_id 过滤）
        with env_scope(run_env):
            if doc_id:
                refs_rows = db.execute(
                    select(WikiPage.source_refs).where(
                        WikiPage.kb_id == kb_id, WikiPage.status == "active")
                ).scalars().all()
                page_n = sum(1 for refs in refs_rows if doc_id in (refs or []))
            else:
                page_n = db.execute(
                    select(func.count()).select_from(WikiPage).where(
                        WikiPage.kb_id == kb_id, WikiPage.status == "active")
                ).scalar() or 0
        if page_n > 0:
            scope_txt = "含本文档" if doc_id else "整库"
            return {"ok": True, "detail": f"无建页统计键，按 wiki 页面判定：{page_n} 页（{scope_txt}）"}
        return {"ok": False, "detail": "run 成功但无 wiki 页面产出（bindings 无建页统计且页面计数为 0）"}
    if kind == "domain_resolve":
        tid = str(bindings.get("resolved_tape_id") or "").strip()
        if tid:
            name = str(bindings.get("resolved_tape_name") or "").strip()
            return {"ok": True, "detail": f"已选定构建编排：{name or tid}"}
        return {"ok": False, "detail": "题材分析完成但未选定构建编排（resolved_tape_id 为空），下游构建未触发"}
    return {"ok": None, "detail": "该类型无产物校验规则"}


def _decide_result(checks: list[dict], display_state: str | None,
                   real_state: str | None) -> str:
    """汇总判定 result 优先级：stale > unknown > fake_success > failed > running > ok。"""
    if any(c["key"] == "zombie" and c.get("ok") is False for c in checks):
        return "stale"
    real = (real_state or "").lower()
    if real == "unknown":
        return "unknown"  # 关联 run 不存在等无法核对场景
    disp = (display_state or "").upper()
    real_success = real in ("success", "ready")
    if not real_success:
        real_terminal = real in ("failed", "cancelled")
        if disp == "SUCCESS":
            return "fake_success"  # 显示成功但真实失败/未完成
        if not real_terminal:
            return "running"
        if disp == "FAILED":
            return "failed"
        return "failed" if real == "failed" else "unknown"
    # 真实成功
    if disp != "SUCCESS":
        return "running"  # job 未回写（页面仍显示进行中/失败）
    if any(c.get("ok") is False and c["key"] in ("internal", "artifact") for c in checks):
        return "fake_success"  # 真实成功但节点/产物有问题
    if all(c.get("ok") is not False for c in checks if c["key"] in ("consistency", "internal", "artifact")):
        return "ok"
    return "unknown"


# ── 任务形态检查（解析 job / 编排 run） ────────────────────────

def _inspect_parse_task(job: Job, params: dict, db: Session) -> dict:
    """解析类任务（无 run）：真实状态由 kb_document.parse_state 推断。"""
    from api.models.knowledge import KbDocument

    kb_id = str(params.get("kbId") or params.get("kb_id") or job.kb_id or "").strip()
    doc_id = str(params.get("documentId") or params.get("document_id") or job.doc_id or "").strip()
    display = job.state or "PENDING"

    doc_state, chunk_cnt, doc_exists, doc_name = None, 0, False, ""
    if doc_id:
        row = db.execute(
            select(KbDocument.parse_state, KbDocument.chunk_count, KbDocument.file_name).where(
                KbDocument.id == doc_id)
        ).first()
        if row is not None:
            doc_exists = True
            doc_state, chunk_cnt, doc_name = row[0], row[1] or 0, row[2] or ""

    if doc_state == "READY":
        real, real_label = "success", "success"
    elif doc_state == "FAILED":
        real, real_label = "failed", "failed"
    else:
        real, real_label = "running", "running"

    if doc_state:
        if display == "SUCCESS" and real != "success":
            cons = {"ok": False, "detail": f"任务监控显示「{display}」但文档解析状态为 {doc_state}（{real_label}）"}
        else:
            cons = {"ok": True, "detail": f"显示 {display} ↔ 文档解析 {doc_state}，一致"}
    else:
        cons = {"ok": None, "detail": "文档不存在，无法核对解析状态"}

    if doc_state == "READY" and chunk_cnt > 0:
        art = {"ok": True, "detail": f"文档解析 READY（{chunk_cnt} 切片）"}
    elif doc_state == "READY":
        art = {"ok": True, "detail": "文档解析 READY（0 切片，空文档/纯图片可能合法）"}
    else:
        art = {"ok": False, "detail": f"文档解析状态 {doc_state or '不存在'}，解析产物未就绪"}

    checks: list[dict] = [
        {"key": "consistency", "label": "状态一致性", **cons},
        {"key": "artifact", "label": "产物验证", **art},
    ]
    # 僵尸：PENDING/RUNNING 且长时间无终态
    zombie = False
    if display in ("PENDING", "RUNNING"):
        ts = _ts_utc(job.create_time)
        if ts and datetime.now(timezone.utc) - ts > STALE_TD and not job.end_time:
            zombie = True
    checks.append({"key": "zombie", "label": "僵尸检测",
                   "ok": not zombie,
                   "detail": "解析任务长时间未推进（疑似 worker 挂起）" if zombie else "未超时"})
    return {
        "id": job.id,
        "kind": "doc_parse",
        "task_class": job.task_class or "",
        "tape_name": "",
        "kb_id": kb_id,
        "kb_name": "",
        "doc_id": doc_id,
        "doc_name": doc_name,
        "env": ENV_PROD,
        "display_state": display,
        "real_state": real_label,
        "checks": checks,
        "artifact_summary": f"parse_state={doc_state or 'N/A'} · {chunk_cnt} 切片",
        "created_at": _iso(job.create_time),
        "has_run": False,
        "error": job.error_message or "",
    }


def _inspect_run_task(kind: str, job: Job | None, run: Any, run_env: str,
                      db: Session) -> dict:
    """编排 run 任务：四类检查全做。job 可为 None（独立执行/手动跑构建编排）。"""
    inputs = run.inputs or {}
    kb_id = str(inputs.get("kb_id") or (job.kb_id if job else "") or "").strip()
    doc_id = str(inputs.get("document_id") or inputs.get("doc_id") or inputs.get("kid")
                 or (job.doc_id if job else "") or "").strip()

    display = (job.state if job and job.state else _RUN_TO_JOB.get(run.status, "PENDING"))
    real = run.status or "queued"

    checks: list[dict] = []
    checks.append({"key": "consistency", "label": "状态一致性", **_check_consistency(display, real)})

    steps = run.step_results or []
    checks.append({"key": "internal", "label": "节点一致性",
                   **(_check_run_internal(steps) if real == "success" else
                      {"ok": None, "detail": f"run 状态 {real}，跳过节点一致性检查"})})

    if real == "success":
        checks.append({"key": "artifact", "label": "产物验证",
                       **_check_artifacts(kind, run.bindings, inputs, kb_id, doc_id, run_env, db)})
    else:
        checks.append({"key": "artifact", "label": "产物验证",
                       "ok": None, "detail": f"run 状态 {real}，不校验产物"})

    zombie = False
    if real in ("queued", "running"):
        ts = _ts_utc(run.updated_at or run.created_at)
        if ts and datetime.now(timezone.utc) - ts > STALE_TD:
            zombie = True
    checks.append({"key": "zombie", "label": "僵尸检测",
                   "ok": not zombie,
                   "detail": f"run 停留 {real} 超过 {STALE_TD.total_seconds() // 3600:.0f}h 未推进（疑似 worker 挂起）"
                   if zombie else "未超时"})

    # 节点明细里的失败/产物错误
    failed_nodes = [s for s in steps if isinstance(s, dict) and s.get("status") == "failed"]
    step_err = "; ".join(f"{s.get('step_label') or s.get('step_id')}: {s.get('error', '')}"[:120]
                         for s in failed_nodes[:3])

    return {
        "id": job.id if job else run.id,
        "kind": kind,
        "task_class": ORCH_TASK_CLASS if job else "OrchestrationRun",
        "tape_name": run.tape_name or "",
        "kb_id": kb_id,
        "kb_name": "",
        "doc_id": doc_id,
        "doc_name": "",
        "env": run_env,
        "display_state": display,
        "real_state": real,
        "checks": checks,
        "artifact_summary": f"{len(steps)} 节点",
        "created_at": (run.created_at.isoformat() if run.created_at else None),
        "has_run": True,
        "run_id": run.id,
        "error": step_err or run.error or "",
    }


def _fmt_item(raw: dict, kb_names: dict[str, str], doc_names: dict[str, str]) -> dict:
    """补 kb/doc 名称 + 汇总 result（检查项 ok 全 True → ok；含 False → 按判定）。"""
    kb_id, doc_id = raw["kb_id"], raw["doc_id"]
    raw["kb_name"] = kb_names.get(kb_id, "")
    raw["doc_name"] = doc_names.get(doc_id, "") or raw.get("doc_name") or ""
    raw["result"] = _decide_result(raw["checks"], raw["display_state"], raw["real_state"])
    raw["result_label"] = RESULT_LABELS.get(raw["result"], raw["result"])
    raw["kind_label"] = KIND_LABELS.get(raw["kind"], raw["kind"])
    return raw


# ── 查询：构建链任务全量行（job 侧 + run 侧，去重） ─────────────

def _collect_rows(db: Session) -> list[dict]:
    """收集构建链任务行（未检查、未分页）。返回 dict 列表：
    {"id", "ts"(datetime|None), "kind", "job"|None, "run"|None, "run_env", "params"}

    - job 侧：解析类（doc_parse）+ 编排承接类（ORCH_RUN_*/SUMMARY_RUN_*/RESOLVE_RUN_*）；
      ORCH 行预关联 run（双表批量），run 找不到（已随删档级联清理/旧数据）kind=unknown 保留展示。
    - run 侧：双表按 构建/摘要/题材 关键词匹配，排除已被 job 承接的行（防重复）。
    - 双表去重：测试环境两轮 env_scope 可能查到同一批 run，按 id 集合去重。
    """
    from sqlalchemy import or_

    from api.models.orchestration import TapeRun

    rows: list[dict] = []
    # 1) job 侧：解析类 + 编排承接类
    jobs = db.execute(
        select(Job).where(
            Job.task_class.in_(list(PARSE_TASK_CLASSES) + [ORCH_TASK_CLASS])
        )
    ).scalars().all()
    job_run_ids: set[str] = set()
    orchs: list[tuple[Job, dict]] = []
    for j in jobs:
        params = _parse_job_params(j)
        if j.task_class == ORCH_TASK_CLASS:
            rid = str(params.get("runId") or j.run_id or "").strip()
            if rid:
                job_run_ids.add(rid)
            orchs.append((j, params))
        else:
            rows.append({"id": j.id, "ts": j.create_time, "kind": "doc_parse",
                         "job": j, "run": None, "run_env": None, "params": params})

    # 2) 为 ORCH job 行批量预关联 run（双表，id IN）
    run_by_env: dict[str, tuple[Any, str]] = {}
    if job_run_ids:
        ids = list(job_run_ids)
        for env in (ENV_PROD, ENV_DEV):
            with env_scope(env):
                runs = db.execute(
                    select(TapeRun).where(TapeRun.id.in_(ids))
                ).scalars().all()
            for r in runs:
                run_by_env.setdefault(r.id, (r, env))  # 双表同名优先保留先查到的
    rows_by_job_run: dict[str, dict] = {}
    for j, params in orchs:
        rid = str(params.get("runId") or j.run_id or "").strip()
        pair = run_by_env.get(rid) if rid else None
        if pair is not None:
            run, env = pair
            kind = _classify_run(run.tape_name, run.inputs) or "unknown"
            rows.append({"id": j.id, "ts": j.create_time, "kind": kind,
                         "job": j, "run": run, "run_env": env, "params": params})
        else:
            # run 不存在（删档级联清理/数据丢失）——保留并标 unknown
            rows.append({"id": j.id, "ts": j.create_time, "kind": "unknown",
                         "job": j, "run": None, "run_env": None, "params": params})
        rows_by_job_run[j.id] = None

    # 3) run 侧：双表按编排名关键词匹配构建链，排除已由承接 job 覆盖的
    seen_run_ids: set[str] = set()
    name_cond = or_(
        TapeRun.tape_name.like("%构建%"),
        TapeRun.tape_name.like("%摘要%"),
        TapeRun.tape_name.like("%题材%"),
    )
    for env in (ENV_PROD, ENV_DEV):
        with env_scope(env):
            runs = db.execute(select(TapeRun).where(name_cond)).scalars().all()
        for r in runs:
            if r.id in job_run_ids:
                continue  # 已由承接 job 行覆盖
            if r.id in seen_run_ids:
                continue  # 双表同名去重
            kind = _classify_run(r.tape_name, r.inputs)
            if kind is None:
                continue
            seen_run_ids.add(r.id)
            rows.append({"id": r.id, "ts": r.created_at, "kind": kind,
                         "job": None, "run": r, "run_env": env, "params": {}})
            rows_by_job_run[r.id] = None

    return rows


def _row_refs(rows: list[dict]) -> tuple[set[str], set[str]]:
    """收集全量行涉及的 kb_id / doc_id（job 冗余列 + run inputs 双路，供名称反查）。"""
    kb_ids: set[str] = set()
    doc_ids: set[str] = set()
    for r in rows:
        if r["job"]:
            if r["job"].kb_id:
                kb_ids.add(str(r["job"].kb_id))
            if r["job"].doc_id:
                doc_ids.add(str(r["job"].doc_id))
        if r["run"]:
            inp = r["run"].inputs or {}
            kb = str(inp.get("kb_id") or "").strip()
            did = str(inp.get("document_id") or inp.get("doc_id") or inp.get("kid") or "").strip()
            if kb:
                kb_ids.add(kb)
            if did:
                doc_ids.add(did)
    return kb_ids, doc_ids


# ── 端点 ──────────────────────────────────────────────────────

@router.get("/overview")
def build_check_overview(
    caller: dict = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """统计概览：构建链任务按 检查结果 / 类型 分组计数（全量，不分页）。"""
    rows = _collect_rows(db)
    kb_ids, doc_ids = _row_refs(rows)
    kb_names = _kb_names(db, kb_ids)
    doc_names = _doc_names(db, doc_ids)

    by_result: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in rows:
        raw = _inspect_row(r, db)
        item = _fmt_item(raw, kb_names, doc_names)
        by_result[item["result"]] = by_result.get(item["result"], 0) + 1
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    return {"success": True, "data": {
        "total": len(rows),
        "by_result": by_result,
        "by_kind": by_kind,
    }}


@router.get("/tasks")
def list_build_check_tasks(
    kb_id: str = Query("", max_length=64),
    kind: str = Query("", max_length=20),
    result: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    caller: dict = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """构建链任务列表（时间倒序 + 检查结果），支持 kb/kind/result 筛选。

    kind/result 筛选在检查完成后进行（kind 依赖 run 分类、result 依赖检查结果），
    构建链任务量小（几十~几百行），全量检查后再筛选/分页，保证筛选口径与展示一致。
    """
    rows = _collect_rows(db)
    if kb_id:
        rows = [r for r in rows if (r["job"] and str(r["job"].kb_id or "") == kb_id)
                or (r["run"] and str((r["run"].inputs or {}).get("kb_id") or "") == kb_id)]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]

    kb_ids, doc_ids = _row_refs(rows)
    kb_names = _kb_names(db, kb_ids)
    doc_names = _doc_names(db, doc_ids)

    # 全量检查 + result 筛选
    items: list[dict] = []
    for r in rows:
        raw = _inspect_row(r, db)
        item = _fmt_item(raw, kb_names, doc_names)
        if result and item["result"] != result:
            continue
        items.append(item)
    items.sort(key=lambda it: (it.get("created_at") or "", it["id"]), reverse=True)

    total = len(items)
    page_items = items[(page - 1) * page_size: page * page_size]
    return {"success": True, "data": {"total": total, "page": page,
                                      "page_size": page_size, "items": page_items}}


@router.get("/tasks/{task_id}")
def build_check_task_detail(
    task_id: str,
    caller: dict = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """单任务详情：完整检查明细 + 编排 run 节点执行记录。"""
    from api.models.orchestration import TapeRun

    # 可能是 job id 或 run id
    job = db.execute(select(Job).where(Job.id == task_id)).scalars().first()
    run, run_env = None, None
    if job and job.task_class == ORCH_TASK_CLASS:
        rid = _parse_job_params(job).get("runId") or job.run_id or ""
        if rid:
            run, run_env = _find_run(db, rid)
    if run is None:
        run, run_env = _find_run(db, task_id)

    if run is not None:
        kind = _classify_run(run.tape_name, run.inputs) or "unknown"
        item = _inspect_run_task(kind, job, run, run_env or ENV_PROD, db)
    elif job is not None and job.task_class in PARSE_TASK_CLASSES:
        item = _inspect_parse_task(job, _parse_job_params(job), db)
    elif job is not None and job.task_class == ORCH_TASK_CLASS:
        item = _inspect_unknown(job, _parse_job_params(job))
    else:
        raise HTTPException(status_code=404, detail="构建任务不存在")

    kb_ids = {item["kb_id"]}
    doc_ids = {item["doc_id"]}
    item = _fmt_item(item, _kb_names(db, kb_ids), _doc_names(db, doc_ids))

    steps = []
    if run is not None:
        for s in (run.step_results or []):
            if not isinstance(s, dict):
                continue
            steps.append({
                "step_id": s.get("step_id"),
                "step_label": s.get("step_label") or s.get("step_inst"),
                "step_inst": s.get("step_inst"),
                "status": s.get("status"),
                "duration_ms": s.get("duration_ms"),
                "round": s.get("round"),
                "error": s.get("error") or "",
            })
    return {"success": True, "data": {**item, "steps": steps}}


# ── 名称反查（批量，避免 N+1） ────────────────────────────────

def _kb_names(db: Session, kb_ids: set[str]) -> dict[str, str]:
    ids = {i for i in kb_ids if i}
    if not ids:
        return {}
    from api.models.knowledge import KbDatasource

    rows = db.execute(
        select(KbDatasource.id, KbDatasource.name).where(KbDatasource.id.in_(ids))
    ).all()
    return {str(i): str(n or "") for i, n in rows}


def _doc_names(db: Session, doc_ids: set[str]) -> dict[str, str]:
    ids = {i for i in doc_ids if i}
    if not ids:
        return {}
    from api.models.knowledge import KbDocument

    rows = db.execute(
        select(KbDocument.id, KbDocument.file_name).where(KbDocument.id.in_(ids))
    ).all()
    return {str(i): str(n or "") for i, n in rows}


def _inspect_unknown(job: Job, params: dict) -> dict:
    """ORCH 承接 job 但关联 run 不存在（删档级联清理/数据丢失）——页面仍显示该行。"""
    kb_id = str(params.get("kbId") or params.get("kb_id") or job.kb_id or "").strip()
    doc_id = str(params.get("documentId") or params.get("document_id") or job.doc_id or "").strip()
    return {
        "id": job.id,
        "kind": "unknown",
        "task_class": job.task_class or "",
        "tape_name": "",
        "kb_id": kb_id,
        "kb_name": "",
        "doc_id": doc_id,
        "doc_name": "",
        "env": ENV_PROD,
        "display_state": job.state or "PENDING",
        "real_state": "unknown",
        "checks": [
            {"key": "consistency", "label": "状态一致性", "ok": None,
             "detail": "关联的编排 run 不存在（可能已随文档删除级联清理），无法核对真实状态"},
            {"key": "internal", "label": "节点一致性", "ok": None, "detail": "无 run 记录"},
            {"key": "artifact", "label": "产物验证", "ok": None, "detail": "无 run 记录"},
            {"key": "zombie", "label": "僵尸检测", "ok": True, "detail": "无 run 记录"},
        ],
        "artifact_summary": "run 不存在",
        "created_at": _iso(job.create_time),
        "has_run": False,
        "error": job.error_message or "",
    }


def _inspect_row(r: dict, db: Session) -> dict:
    if r["run"] is not None:
        kind = r["kind"] or _classify_run(r["run"].tape_name, r["run"].inputs) or "build"
        return _inspect_run_task(kind, r["job"], r["run"], r["run_env"] or ENV_PROD, db)
    if r["kind"] == "unknown":
        return _inspect_unknown(r["job"], r["params"])
    return _inspect_parse_task(r["job"], r["params"], db)