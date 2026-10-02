"""System management API routes — users / roles / teams / menus / logs.

Read queries + role/menu mutations: role CRUD, role-menu assignment
(menu authorization), role-user assignment, menu CRUD, and the current
user's navigation menus (my-menus, backing the frontend Sider).
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.services.identity import decode_identity_cookie
from api.services.system_admin import (
    delete_menu,
    delete_role,
    get_role_menus,
    get_role_users,
    is_platform_admin,
    list_menus,
    list_operation_logs,
    list_roles,
    list_teams,
    list_users,
    my_menus,
    save_menu,
    save_role,
    save_role_menus,
    save_role_users,
)

router = APIRouter(prefix="/system", tags=["system"])


class RoleMenusRequest(BaseModel):
    menuIds: list[str] = []


class RoleUsersRequest(BaseModel):
    userIds: list[str] = []


def _current_identity(x_next_identity: str | None) -> str | None:
    identity = decode_identity_cookie(x_next_identity or "")
    return identity.user_id if identity else None


@router.get("/users")
def get_users(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_users(db, page, page_size, keyword)}


@router.get("/roles")
def get_roles(page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_roles(db, page, page_size)}


@router.post("/roles")
def create_role(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Create a role (is_edit=false). Mirrors saveRoleAction."""
    return save_role(db, {**payload, "is_edit": False})


@router.put("/roles/{role_id}")
def update_role(role_id: str, payload: dict, db: Session = Depends(get_db)) -> dict:
    """Update an existing role (is_edit=true). Mirrors saveRoleAction(edit)."""
    return save_role(db, {**payload, "role_id": role_id, "is_edit": True})


@router.delete("/roles/{role_id}")
def remove_role(role_id: str, db: Session = Depends(get_db)) -> dict:
    return delete_role(db, role_id)


@router.get("/roles/{role_id}/menus")
def role_menus(role_id: str, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": get_role_menus(db, role_id)}


@router.put("/roles/{role_id}/menus")
def assign_role_menus(role_id: str, req: RoleMenusRequest, db: Session = Depends(get_db)) -> dict:
    return save_role_menus(db, role_id, req.menuIds)


@router.get("/roles/{role_id}/users")
def role_users(role_id: str, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": get_role_users(db, role_id)}


@router.put("/roles/{role_id}/users")
def assign_role_users(role_id: str, req: RoleUsersRequest, db: Session = Depends(get_db)) -> dict:
    return save_role_users(db, role_id, req.userIds)


@router.get("/teams")
def get_teams(page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_teams(db, page, page_size)}


@router.get("/menus")
def get_menus(db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_menus(db)}


@router.post("/menus")
def create_menu(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Create a menu (is_edit=false). Mirrors saveMenuAction."""
    return save_menu(db, {**payload, "is_edit": False})


@router.put("/menus/{menu_id}")
def update_menu(menu_id: str, payload: dict, db: Session = Depends(get_db)) -> dict:
    """Update an existing menu (is_edit=true). Mirrors saveMenuAction(edit)."""
    return save_menu(db, {**payload, "menu_id": menu_id, "is_edit": True})


@router.delete("/menus/{menu_id}")
def remove_menu(menu_id: str, db: Session = Depends(get_db)) -> dict:
    return delete_menu(db, menu_id)


@router.get("/my-menus")
def get_my_menus(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """Navigation menus for the current user (frontend Sider). Admin bypass
    mirrors the source proxy: AUTH_ADMIN_USERS env or plat-mgr role."""
    user_id = _current_identity(x_next_identity)
    if not user_id:
        return {"success": False, "message": "未登录", "data": None}
    admin_users = _admin_users()
    is_admin = is_platform_admin(db, user_id, admin_users)
    return {"success": True, "data": my_menus(db, user_id, is_admin=is_admin)}


@router.get("/operation-logs")
def get_operation_logs(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_operation_logs(db, page, page_size, keyword)}


def _admin_users() -> list[str]:
    raw = get_settings().AUTH_ADMIN_USERS
    return [u.strip() for u in raw.split(",") if u.strip()] if raw else []


__all__ = ["router"]