"""Framework sync API routes — 从外部平台业务库同步团队/用户/角色。

端点（对齐「同步管理」页面）：
- POST  /sync/framework/run     执行同步（按数据源 dsId，admin 除外，角色保底）
- GET   /sync/framework/status  最近一次同步结果
- GET   /sync/framework/history 同步历史（分页）

鉴权：与 datasources 一致，按 x-next-identity cookie 强制登录（401 未登录）；
受控路由 /api/v1/sync → /system/sync 菜单（middleware rbac_guard 校验角色-菜单授权）。
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.framework_sync import (
    get_last_sync,
    list_sync_history,
    run_framework_sync,
)
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/sync", tags=["sync"])


def _caller_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


class SyncRunRequest(BaseModel):
    dsId: str = Field(..., description="数据源 ID（数据源管理中已配置的 mysql 类型数据源）")


@router.post("/framework/run")
def run_sync(
    req: SyncRunRequest,
    _user_id: str = Depends(_caller_id),
    db: Session = Depends(get_db),
) -> dict:
    """执行框架表同步（团队/用户/角色，admin 除外）。"""
    try:
        result = run_framework_sync(db, req.dsId, caller_user_id=_user_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": result.get("success", False), "data": result}


@router.get("/framework/status")
def sync_status(_user_id: str = Depends(_caller_id), db: Session = Depends(get_db)) -> dict:
    """最近一次同步结果（含各表统计）。"""
    return {"success": True, "data": get_last_sync(db)}


@router.get("/framework/history")
def sync_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    _user_id: str = Depends(_caller_id),
    db: Session = Depends(get_db),
) -> dict:
    """同步历史（分页，新→旧）。"""
    return {"success": True, "data": list_sync_history(db, page, page_size)}
