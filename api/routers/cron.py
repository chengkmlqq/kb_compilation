"""任务管理 API — modo_cron_task（定时任务）CRUD + 启停，供前端「任务管理」页使用。

对齐 data-synth src/app/actions/cron-actions.ts 的能力：
- 分页列表（keyWord 匹配 name/label）
- 保存（新建/更新）：name/label/cronExpression/taskClass/state/fireParams/queueName
- 批量删除
- 启停（state '1'=生效 / '0'=失效）
- 可选队列列表（modo_job 实际出现的 queue_name 去重）
- 已注册任务列表（Flower /api/workers 的 registered 任务类，供下拉选择）

worker 侧 scheduler.scan_cron_tasks 按 cron_expression + next_fire_time 触发，
state=='1' 的记录生效；本路由只做配置管理，不直接触发。
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.framework import CronTask, Job
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/cron", tags=["cron"])


def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _cron_to_dict(row: CronTask) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "label": row.label,
        "cronExpression": row.cron_expression,
        "taskClass": row.task_class,
        "state": row.state,
        "fireParams": row.fire_params,
        "queueName": row.queue_name,
        "nextFireTime": row.next_fire_time,
    }


class CronTaskSave(BaseModel):
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    cronExpression: str = Field(min_length=1)
    taskClass: str = Field(min_length=1)
    state: str = "1"
    fireParams: str | None = None
    queueName: str | None = None


@router.get("")
def list_cron_tasks(
    pageNum: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=200),
    keyWord: str | None = Query(default=None),
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """分页列表（keyWord 模糊匹配 name/label）。"""
    where = None
    kw = (keyWord or "").strip()
    if kw:
        where = or_(CronTask.name.like(f"%{kw}%"), CronTask.label.like(f"%{kw}%"))
    # 注意：SQLAlchemy 2.x 的 .where(None) 会生成恒假条件（WHERE NULL），无关键词时不要调用
    count_stmt = select(func.count()).select_from(CronTask)
    if where is not None:
        count_stmt = count_stmt.where(where)
    total = int(db.execute(count_stmt).scalar() or 0)
    stmt = select(CronTask)
    if where is not None:
        stmt = stmt.where(where)
    rows = (
        db.execute(
            stmt.order_by(CronTask.id.desc())
            .offset((pageNum - 1) * pageSize)
            .limit(pageSize)
        )
        .scalars()
        .all()
    )
    return {
        "success": True,
        "data": {
            "content": [_cron_to_dict(r) for r in rows],
            "totalElements": total,
            "pageNum": pageNum,
            "pageSize": pageSize,
        },
    }


@router.post("")
def create_cron_task(
    req: CronTaskSave,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    _validate_fire_params(req.fireParams)
    task = CronTask(
        id=uuid.uuid4().hex,
        name=req.name.strip(),
        label=req.label.strip(),
        cron_expression=_validate_cron_expression(req.cronExpression),
        task_class=req.taskClass.strip(),
        state=req.state or "1",
        fire_params=req.fireParams,
        queue_name=(req.queueName or None),
        next_fire_time=None,
    )
    db.add(task)
    db.commit()
    return {"success": True, "data": _cron_to_dict(task)}


@router.put("/{task_id}")
def update_cron_task(
    task_id: str,
    req: CronTaskSave,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    _validate_fire_params(req.fireParams)
    task = db.execute(select(CronTask).where(CronTask.id == task_id)).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"定时任务不存在: {task_id}")
    task.name = req.name.strip()
    task.label = req.label.strip()
    task.cron_expression = _validate_cron_expression(req.cronExpression)
    task.task_class = req.taskClass.strip()
    task.state = req.state or "1"
    task.fire_params = req.fireParams
    task.queue_name = req.queueName or None
    # cron/状态变更 → 让 scheduler 重新计算下次触发时间
    task.next_fire_time = None
    db.commit()
    return {"success": True, "data": _cron_to_dict(task)}


@router.delete("/{task_id}")
def delete_cron_task(
    task_id: str,
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    task = db.execute(select(CronTask).where(CronTask.id == task_id)).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"定时任务不存在: {task_id}")
    db.delete(task)
    db.commit()
    return {"success": True, "message": "删除成功"}


@router.post("/{task_id}/toggle")
def toggle_cron_task(
    task_id: str,
    state: str = Query(description="1=生效 0=失效"),
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    task = db.execute(select(CronTask).where(CronTask.id == task_id)).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"定时任务不存在: {task_id}")
    task.state = state
    task.next_fire_time = None
    db.commit()
    return {"success": True, "data": _cron_to_dict(task)}


@router.get("/queues")
def cron_queues(
    _user_id: str = Depends(_require_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """可选队列：modo_job 实际出现的 queue_name 去重（同 jobs/queues）。"""
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


@router.get("/registered-tasks")
def cron_registered_tasks(
    _user_id: str = Depends(_require_user_id),
) -> dict:
    """已注册任务类（Flower /api/workers 的 registered 字段，供任务名下拉）。"""
    tasks: list[dict] = []
    try:
        from api.routers.workers import _call_flower

        workers_map = _call_flower("/api/workers?refresh=false") or {}
        if isinstance(workers_map, dict):
            for raw in workers_map.values():
                if not isinstance(raw, dict):
                    continue
                registered = raw.get("registered") or []
                names = registered if isinstance(registered, list) else list(registered)
                for entry in names:
                    name = str(entry if isinstance(entry, str) else entry.get("name", "")).strip()
                    if name and name not in {t["taskClass"] for t in tasks}:
                        tasks.append({"taskClass": name, "name": name})
    except Exception:  # noqa: BLE001 — Flower 不可达时返回空列表，不阻塞页面
        tasks = []
    tasks.sort(key=lambda t: t["taskClass"])
    return {"success": True, "data": tasks}


def _validate_cron_expression(expr: str) -> str:
    """校验 cron 表达式合法性（2026-10-09 回归修复）。

    原实现只查 min_length=1，非法表达式（如 "99 99 * * *"）能入库，
    后果是 worker 调度器每轮扫描都抛
    `scan_cron_tasks failed to parse cron expr` 刷屏刷日志，且该任务永不触发。
    worker 侧用 croniter，这里同一依赖前置校验，非法直接 400。
    """
    text = str(expr or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="cron 表达式不能为空")
    try:
        from croniter import croniter

        if not croniter.is_valid(text):
            raise HTTPException(status_code=400, detail=f"cron 表达式非法: {text}")
    except ImportError:  # croniter 不可用则跳过校验，不阻断保存
        pass
    return text


def _validate_fire_params(fire_params: str | None) -> None:
    """fireParams 必须是合法 JSON（worker 触发时按 dict 解析）。"""
    text = (fire_params or "").strip()
    if not text:
        return
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"扩展参数不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="扩展参数必须是 JSON 对象")