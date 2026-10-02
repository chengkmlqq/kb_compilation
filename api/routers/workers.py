"""Worker monitor API — Celery Flower 监控数据代理（对齐 data-synth 主机监控）。

后端直连 Flower HTTP API（容器内 http://flower:5555，Basic Auth），把 Flower
的原始 JSON 归一化成前端可直接渲染的结构：

- GET  /workers                          → workers 概览（状态/队列/任务数/心跳）
- GET  /workers/{name}/tasks             → 单 worker 任务快照（active/reserved/scheduled/recent）
- GET  /workers/{name}/registered-tasks  → 注册任务 + modo_cron_task 定时配置对照

Flower 数据来源：`/api/workers?refresh=`（worker 详情）+ `/api/workers?status=true`
（在线状态）+ `/api/tasks?limit=&workername=`（最近任务）。只读，不做任何控制操作。
"""

from __future__ import annotations

import base64
import json
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.models.framework import CronTask
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/workers", tags=["workers"])


def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


# ---------------------------------------------------------------------------
# Flower HTTP client（纯 urllib，无额外依赖）
# ---------------------------------------------------------------------------


def _flower_base_url() -> str:
    settings = get_settings()
    url = settings.FLOWER_API_BASE_URL or "http://flower:5555"
    return url.rstrip("/")


def _flower_auth_header() -> str:
    settings = get_settings()
    auth = settings.FLOWER_BASIC_AUTH or "admin:admin"
    token = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _flower_timeout_s() -> int:
    settings = get_settings()
    return settings.FLOWER_API_TIMEOUT_S or 15


def _call_flower(path: str) -> Any:
    """GET Flower API path，返回解析后的 JSON（失败抛 HTTPException）。"""
    url = f"{_flower_base_url()}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": _flower_auth_header(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_flower_timeout_s()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # urllib.error.URLError / socket.timeout 等
        raise HTTPException(status_code=502, detail=f"Flower API 不可达: {exc}") from exc
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 归一化（对齐 data-synth src/lib/flower-client.ts 语义）
# ---------------------------------------------------------------------------


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _safe_number(value: Any, fallback: int = 0) -> int:
    try:
        parsed = int(value)
        return parsed
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> str | None:
    """Flower 时间戳（秒/毫秒/ISO 字符串）→ ISO8601。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        millis = value * 1000 if value < 1e11 else value
        try:
            return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _registered_names(raw: Any) -> list[str]:
    return sorted(
        {_text(_as_record(item).get("name") or item) for item in _as_array(raw) if _text(item)},
        key=str.lower,
    )


def _active_queue_names(raw: Any) -> list[str]:
    return sorted(
        {_text(_as_record(item).get("name")) for item in _as_array(raw)},
        key=str.lower,
    )


def _processed_count(stats: dict[str, Any]) -> int:
    total = _as_record(stats.get("total"))
    return sum(_safe_number(v) for v in total.values())


def _concurrency(stats: dict[str, Any]) -> int:
    pool = _as_record(stats.get("pool"))
    return _safe_number(pool.get("max-concurrency", pool.get("max_concurrency")))


def _snapshot_tasks(raw: Any) -> list[dict[str, Any]]:
    """worker 快照任务（active/reserved/scheduled）→ 简表。"""
    out: list[dict[str, Any]] = []
    for item in _as_array(raw):
        record = _as_record(item)
        request = _as_record(record.get("request"))
        merged = {**record, **request}
        out.append(
            {
                "taskId": _text(merged.get("uuid") or merged.get("id")),
                "taskName": _text(merged.get("name") or merged.get("type")) or "-",
                "state": _text(merged.get("state")) or "UNKNOWN",
                "queueName": _text(_as_record(merged.get("delivery_info")).get("routing_key")),
                "receivedAt": _parse_ts(merged.get("received") or merged.get("timestamp")),
                "startedAt": _parse_ts(merged.get("started") or merged.get("time_start")),
                "runtimeSeconds": _safe_float(merged.get("runtime")),
            }
        )
    return out


def _recent_tasks(tasks_map: dict[str, Any], worker_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for task_id, raw in tasks_map.items():
        record = _as_record(raw)
        request = _as_record(record.get("request"))
        merged = {**record, **request}
        worker = _text(merged.get("worker") or merged.get("hostname"))
        if worker and worker != worker_name:
            continue
        delivery = _as_record(merged.get("delivery_info"))
        succeeded = _parse_ts(merged.get("succeeded"))
        failed = _parse_ts(merged.get("failed"))
        out.append(
            {
                "taskId": _text(merged.get("uuid") or merged.get("id")) or task_id,
                "taskName": _text(merged.get("name") or merged.get("type")) or "-",
                "state": _text(merged.get("state")) or "UNKNOWN",
                "queueName": _text(delivery.get("routing_key")),
                "argsText": _json_text(merged.get("args")),
                "kwargsText": _json_text(merged.get("kwargs")),
                "receivedAt": _parse_ts(merged.get("received") or merged.get("timestamp")),
                "startedAt": _parse_ts(merged.get("started") or merged.get("time_start")),
                "finishedAt": succeeded or failed,
                "runtimeSeconds": _safe_float(merged.get("runtime")),
                "resultText": None if merged.get("result") is None else _json_text(merged.get("result")),
                "exceptionText": None if merged.get("exception") is None else _json_text(merged.get("exception")),
            }
        )
    out.sort(
        key=lambda t: t["receivedAt"] or t["startedAt"] or t["finishedAt"] or "",
        reverse=True,
    )
    return out


def _json_text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value.strip() or "-"
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _normalize_worker(name: str, raw: dict[str, Any], is_online: bool) -> dict[str, Any]:
    stats = _as_record(raw.get("stats"))
    return {
        "workerName": name,
        "status": "ONLINE" if is_online else "OFFLINE",
        "activeQueueNames": _active_queue_names(raw.get("active_queues")),
        "registeredTaskCount": len(_as_array(raw.get("registered"))),
        "registeredTaskNames": _registered_names(raw.get("registered")),
        "processedTaskCount": _processed_count(stats),
        "concurrency": _concurrency(stats),
        "prefetchCount": _safe_number(stats.get("prefetch_count")),
        "activeTaskCount": len(_as_array(raw.get("active"))),
        "reservedTaskCount": len(_as_array(raw.get("reserved"))),
        "scheduledTaskCount": len(_as_array(raw.get("scheduled"))),
        "lastHeartbeatAt": _parse_ts(raw.get("timestamp")),
    }


def _build_summary(workers: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(workers),
        "online": sum(1 for w in workers if w["status"] == "ONLINE"),
        "offline": sum(1 for w in workers if w["status"] == "OFFLINE"),
    }


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("")
def list_workers(
    _user_id: str = Depends(_require_user_id),
    force_refresh: bool = Query(False),
) -> dict:
    """Worker 概览：Flower /api/workers + /api/workers?status=true 合并。"""
    refresh = "true" if force_refresh else "false"
    workers_map = _call_flower(f"/api/workers?refresh={refresh}") or {}
    status_map = _call_flower("/api/workers?status=true") or {}

    names = sorted(set(_as_record(workers_map).keys()) | set(_as_record(status_map).keys()))
    workers = [
        _normalize_worker(
            name,
            _as_record(workers_map.get(name)),
            bool(_as_record(status_map).get(name)),
        )
        for name in names
    ]
    workers.sort(key=lambda w: (w["status"] != "ONLINE", w["workerName"].lower()))

    return {
        "success": True,
        "data": {
            "workers": workers,
            "summary": _build_summary(workers),
            "collectedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


@router.get("/{worker_name}/tasks")
def worker_tasks(
    worker_name: str,
    _user_id: str = Depends(_require_user_id),
    force_refresh: bool = Query(False),
    recent_limit: int = Query(50, ge=1, le=200),
) -> dict:
    """单 worker 任务快照：active/reserved/scheduled + 最近任务。"""
    refresh = "true" if force_refresh else "false"
    worker_path = f"/api/workers?refresh={refresh}&workername={worker_name}"
    tasks_path = f"/api/tasks?limit={recent_limit}&workername={worker_name}"

    workers_map = _call_flower(worker_path) or {}
    worker_raw: dict[str, Any] = {}
    if worker_name in _as_record(workers_map):
        worker_raw = _as_record(workers_map[worker_name])
    else:
        entries = list(_as_record(workers_map).items())
        if len(entries) == 1:
            worker_raw = _as_record(entries[0][1])

    active = _snapshot_tasks(worker_raw.get("active"))
    reserved = _snapshot_tasks(worker_raw.get("reserved"))
    scheduled = _snapshot_tasks(worker_raw.get("scheduled"))
    tasks_map = _call_flower(tasks_path) or {}
    recent = _recent_tasks(_as_record(tasks_map), worker_name)

    return {
        "success": True,
        "data": {
            "workerName": worker_name,
            "activeTasks": active,
            "reservedTasks": reserved,
            "scheduledTasks": scheduled,
            "recentTasks": recent,
            "summary": {
                "active": len(active),
                "reserved": len(reserved),
                "scheduled": len(scheduled),
                "recent": len(recent),
            },
            "collectedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


@router.get("/{worker_name}/registered-tasks")
def worker_registered_tasks(
    worker_name: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """注册任务 + modo_cron_task 定时配置对照（对齐 ds registered-task drawer）。"""
    workers_map = _call_flower("/api/workers?refresh=false") or {}
    worker_raw = _as_record(workers_map.get(worker_name)) or {}
    task_classes = sorted(_registered_names(worker_raw.get("registered")))

    cron_rows: list[dict[str, Any]] = []
    if task_classes:
        rows = db.execute(
            select(CronTask).where(CronTask.task_class.in_(task_classes))
        ).scalars().all()
        cron_rows = [
            {
                "id": row.id,
                "name": row.name,
                "label": row.label,
                "cronExpression": row.cron_expression,
                "queueName": row.queue_name,
                "state": row.state,
                "nextFireTime": row.next_fire_time,
                "taskClass": row.task_class,
            }
            for row in rows
        ]

    cron_map: dict[str, list[dict[str, Any]]] = {}
    for row in cron_rows:
        key = _text(row["taskClass"])
        if not key:
            continue
        cron_map.setdefault(key, []).append(row)

    tasks = [
        {
            "taskName": (cron_map.get(cls, [{}])[0].get("label") if cron_map.get(cls) else cls),
            "taskClass": cls,
            "cronConfigs": [
                {k: v for k, v in cfg.items() if k != "taskClass"}
                for cfg in cron_map.get(cls, [])
            ],
        }
        for cls in task_classes
    ]

    return {
        "success": True,
        "data": {
            "workerName": worker_name,
            "tasks": tasks,
            "summary": {
                "taskCount": len(tasks),
                "configuredTaskCount": sum(1 for t in tasks if t["cronConfigs"]),
                "cronConfigCount": sum(len(t["cronConfigs"]) for t in tasks),
            },
        },
    }
