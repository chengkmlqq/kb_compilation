"""知识库分类 API（知识库页左侧分类树，2026-10）。

分类以 name 关联知识库的 label（KB.label == 分类名 = 归属该分类）；kb_category 表
只持久化分类实体本身（含暂无知识库归属的「空分类」）—— 分类树 = 本表行 ∪ 可见知识库
的 label，按 name 合并，按知识库数倒序。

权限（与知识库页面现状一致，页面为平铺模型）：
- 读 / 新建 / 重命名 / 删除分类 = 登录即可（分类是全局标签，name 唯一）；
- 重命名 / 删除时对知识库 label 的批量改写，**只作用于调用者可管理的知识库**
  （can_manage：personal=属主 / team=同队 / system=管理员），他人知识库不动。

路径用 query 传分类名（`?name=xxx`）而非路径参数：分类名允许含 `/`（表单示例
「制度 / 技术」），放进路径段会被 FastAPI 路由切断。
"""
from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbCategory, KbDatasource
from api.services.identity import decode_identity_cookie
from api.services.scope import ResourceRow, can_manage, is_admin

logger = logging.getLogger("kb.categories")

router = APIRouter(prefix="/kb-categories", tags=["kb-categories"])

MAX_NAME_LEN = 64


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


class CategoryCreate(BaseModel):
    name: str


class CategoryRename(BaseModel):
    name: str  # 新分类名


def _norm(name: str) -> str:
    value = (name or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
    if len(value) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"分类名称最长 {MAX_NAME_LEN} 个字符")
    return value


def _visible_kb_stmt(caller: dict):
    """可见知识库（state='1'）查询，按 scope 三级可见性过滤。"""
    from api.services.kb_admin import kb_visible_clauses

    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    clauses = kb_visible_clauses(caller["user_id"], caller["team_name"], caller["is_admin"])
    if clauses:
        stmt = stmt.where(clauses[0])
    return stmt


def _kb_counts(db: Session, caller: dict) -> dict:
    """可见知识库按 label 聚合计数（分类树上的「N 个知识库」）。"""
    from api.services.kb_admin import kb_visible_clauses

    stmt = (
        select(KbDatasource.label, func.count())
        .where(
            KbDatasource.state == "1",
            KbDatasource.label.isnot(None),
            KbDatasource.label != "",
        )
        .group_by(KbDatasource.label)
    )
    clauses = kb_visible_clauses(caller["user_id"], caller["team_name"], caller["is_admin"])
    if clauses:
        stmt = stmt.where(clauses[0])
    return {str(label): int(cnt) for label, cnt in db.execute(stmt).all()}


def _manageable_kbs(db: Session, caller: dict, label: str) -> list:
    """可见 + 可管理的知识库（label == 分类名）。"""
    rows = db.execute(_visible_kb_stmt(caller).where(KbDatasource.label == label)).scalars().all()
    return [
        kb
        for kb in rows
        if can_manage(
            ResourceRow.from_obj(kb), caller["user_id"], caller["team_name"], caller["is_admin"]
        )
    ]


@router.get("")
def list_categories(
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """分类树数据：kb_category 行 ∪ 可见知识库的 label（含计数），按知识库数倒序。"""
    rows = db.execute(select(KbCategory)).scalars().all()
    counts = _kb_counts(db, caller)
    merged: dict[str, dict] = {}
    for r in rows:
        merged[r.name] = {"id": r.id, "name": r.name, "count": int(counts.get(r.name, 0))}
    for name, cnt in counts.items():
        item = merged.setdefault(name, {"id": None, "name": name, "count": 0})
        item["count"] = cnt
    items = sorted(merged.values(), key=lambda x: (-x["count"], x["name"]))
    return {"success": True, "data": {"items": items}}


@router.post("")
def create_category(
    req: CategoryCreate,
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """新建分类（空分类：无知识库归属也保留在树上）。"""
    name = _norm(req.name)
    exists = db.execute(select(KbCategory).where(KbCategory.name == name)).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail=f"分类已存在: {name}")
    row = KbCategory(id=uuid4().hex, name=name, created_by=caller["user_id"])
    db.add(row)
    db.commit()
    return {"success": True, "data": {"id": row.id, "name": row.name}}


@router.patch("")
def rename_category(
    name: str = Query(..., max_length=MAX_NAME_LEN, description="原分类名"),
    req: CategoryRename = Body(...),
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """重命名分类：改 kb_category 行（若存在）+ 可管理知识库的 label。"""
    old = _norm(name)
    new = _norm(req.name)
    if new == old:
        return {"success": True, "data": {"name": new, "updated_kbs": 0, "row_renamed": False}}
    if db.execute(select(KbCategory).where(KbCategory.name == new)).scalars().first():
        raise HTTPException(status_code=409, detail=f"目标分类已存在: {new}")
    row = db.execute(select(KbCategory).where(KbCategory.name == old)).scalars().first()
    kbs = _manageable_kbs(db, caller, old)
    if row is None and not kbs:
        raise HTTPException(status_code=404, detail=f"分类不存在: {old}")
    if row is not None:
        row.name = new
    for kb in kbs:
        kb.label = new
    db.commit()
    return {
        "success": True,
        "data": {"name": new, "updated_kbs": len(kbs), "row_renamed": row is not None},
    }


@router.delete("")
def delete_category(
    name: str = Query(..., max_length=MAX_NAME_LEN, description="分类名"),
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """删除分类：删 kb_category 行（若存在）+ 可管理知识库的 label 清空（回落「未分类」）。"""
    old = _norm(name)
    row = db.execute(select(KbCategory).where(KbCategory.name == old)).scalars().first()
    kbs = _manageable_kbs(db, caller, old)
    if row is None and not kbs:
        raise HTTPException(status_code=404, detail=f"分类不存在: {old}")
    for kb in kbs:
        kb.label = ""
    if row is not None:
        db.delete(row)
    db.commit()
    return {
        "success": True,
        "data": {"name": old, "updated_kbs": len(kbs), "row_deleted": row is not None},
    }
