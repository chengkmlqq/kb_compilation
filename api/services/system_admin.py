"""System management read service — users, roles, teams, menus, operation logs.

Read-only queries backing the frontend "系统管理" pages. Kept deliberately
read-only for this phase: the source platform's user/role editing is
admin-scoped, and identity/RBAC mutations are a later hardening item
(the current auth layer only handles login + decode).

All functions take an explicit framework-store session.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models.framework import Menu, OperLog, Team, User, UserRole, UserRoleRela

logger = logging.getLogger(__name__)


def _paginate(rows: list[dict], total: int, page: int, page_size: int) -> dict:
    return {"items": rows, "total": total, "page": page, "pageSize": page_size}


def list_users(db: Session, page: int = 1, page_size: int = 10, keyword: str = "") -> dict:
    stmt = select(User)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where((User.user_id.ilike(like)) | (User.user_name.ilike(like)))
    all_rows = db.execute(stmt).scalars().all()
    total = len(all_rows)
    rows = [
        {
            "id": u.id,
            "user_id": u.user_id,
            "user_name": u.user_name,
            "email": u.email,
            "phone": u.phone,
            "default_team": u.default_team,
            "state": u.state,
            "create_dt": u.create_dt,
        }
        for u in all_rows[(page - 1) * page_size : page * page_size]
    ]
    return _paginate(rows, total, page, page_size)


def list_roles(db: Session, page: int = 1, page_size: int = 10) -> dict:
    rows_all = db.execute(select(UserRole)).scalars().all()
    total = len(rows_all)
    rows = [
        {
            "role_id": r.role_id,
            "role_name": r.role_name,
            "role_descr": r.role_descr,
            "role_type": r.role_type,
            "state": r.state,
            "create_date": r.create_date,
        }
        for r in rows_all[(page - 1) * page_size : page * page_size]
    ]
    return _paginate(rows, total, page, page_size)


def list_teams(db: Session, page: int = 1, page_size: int = 10) -> dict:
    rows_all = db.execute(select(Team)).scalars().all()
    total = len(rows_all)
    rows = [
        {
            "team_id": t.team_id,
            "team_name": t.team_name,
            "label": t.label,
            "descr": t.descr,
            "parent_team_name": t.parent_team_name,
            "state": t.state,
            "create_dt": t.create_dt,
        }
        for t in rows_all[(page - 1) * page_size : page * page_size]
    ]
    return _paginate(rows, total, page, page_size)


def list_menus(db: Session) -> dict:
    rows = db.execute(select(Menu).order_by(Menu.sort_num, Menu.create_date)).scalars().all()
    items = [
        {
            "menu_id": m.menu_id,
            "menu_name": m.menu_name,
            "menu_label": m.menu_label,
            "menu_type": m.menu_type,
            "menu_icon": m.menu_icon,
            "route": m.route,
            "parent_id": m.parent_id,
            "sort_num": m.sort_num,
            "state": m.state,
        }
        for m in rows
    ]
    return {"items": items, "total": len(items)}


def list_operation_logs(
    db: Session, page: int = 1, page_size: int = 20, keyword: str = ""
) -> dict:
    stmt = select(OperLog)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            (OperLog.user_name.ilike(like))
            | (OperLog.oper_type.ilike(like))
            | (OperLog.oper_content.ilike(like))
        )
    all_rows = db.execute(stmt).scalars().all()
    total = len(all_rows)
    rows = [
        {
            "id": r.id,
            "user_name": r.user_name,
            "user_id": r.user_id,
            "team_name": r.team_name,
            "oper_type": r.oper_type,
            "oper_content": r.oper_content,
            "oper_url": r.oper_url,
            "oper_time": r.oper_time,
        }
        for r in all_rows[(page - 1) * page_size : page * page_size]
    ]
    return _paginate(rows, total, page, page_size)


def user_roles(db: Session, user_id: str) -> list[str]:
    """Role names bound to a user (via modo_user_role_rela)."""
    rela = db.execute(
        select(UserRoleRela.role_id).where(UserRoleRela.user_id == user_id)
    ).scalars().all()
    if not rela:
        return []
    names = db.execute(
        select(UserRole.role_name).where(UserRole.role_id.in_(rela))
    ).scalars().all()
    return list(names)


__all__ = [
    "list_menus",
    "list_operation_logs",
    "list_roles",
    "list_teams",
    "list_users",
    "user_roles",
]
