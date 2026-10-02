"""System management API routes — users / roles / teams / menus / logs.

Read queries + role/menu mutations: role CRUD, role-menu assignment
(menu authorization), role-user assignment, menu CRUD, the current
user's navigation menus (my-menus, backing the frontend Sider), the
user's own teams / team switching (aligned with data-synth
team-actions), and the menu-icon catalog (aligned with icon-actions).
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.models.framework import Menu, Team, TeamMember, User
from api.services.identity import (
    Identity,
    decode_identity_cookie,
    encode_identity_cookie,
    find_user_team_membership,
)
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

# 菜单图标目录（对齐 data-synth ICON_MAP 可用图标；前端 Select 用）
MENU_ICONS: list[str] = [
    "AppstoreOutlined",
    "BookOutlined",
    "CommentOutlined",
    "DatabaseOutlined",
    "RobotOutlined",
    "SettingOutlined",
    "CloudServerOutlined",
    "DashboardOutlined",
    "FileOutlined",
    "FolderOutlined",
]


class RoleMenusRequest(BaseModel):
    menuIds: list[str] = []


class RoleUsersRequest(BaseModel):
    userIds: list[str] = []


class SwitchTeamRequest(BaseModel):
    teamName: str


class SetDefaultTeamRequest(BaseModel):
    teamName: str


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


# ============================================================================
# 我的团队 / 切换团队 / 默认团队（对齐 data-synth team-actions）
# ============================================================================


@router.get("/my-teams")
def get_my_teams(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """Teams the current user belongs to (getUserTeamsAction equivalent)."""
    uid = _current_identity(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    rows = db.execute(
        select(TeamMember.team_name, Team.team_id, Team.label)
        .join(Team, Team.team_name == TeamMember.team_name)
        .where(TeamMember.user_id == uid)
    ).all()
    data = [
        {"teamName": r[0], "teamId": r[1], "teamLabel": r[2] or ""}
        for r in rows
    ]
    return {"success": True, "data": data}


@router.post("/switch-team")
def switch_team(
    req: SwitchTeamRequest,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """Switch the current user's active team.

    Mirrors rawSwitchTeamAction: validates membership, then re-encodes the
    identity cookie with the new team and returns the new identity_cookie —
    the frontend persists it exactly like login (contract parity).
    """
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        return {"success": False, "message": "未登录", "data": None}
    team = db.execute(select(Team).where(Team.team_name == req.teamName)).scalars().first()
    if not team:
        return {"success": False, "message": "团队不存在", "data": None}
    membership = find_user_team_membership(db, identity.user_id, req.teamName)
    if not membership:
        return {"success": False, "message": "无权限切换到该团队", "data": None}

    new_identity = Identity(
        login_id=identity.login_id,
        user_id=identity.user_id,
        user_name=identity.user_name,
        team_name=team.team_name,
        team_id=team.team_id,
        team_label=team.label or "",
    )
    return {
        "success": True,
        "message": "切换成功",
        "identity_cookie": encode_identity_cookie(new_identity),
        "data": new_identity.to_payload(),
    }


@router.get("/default-team")
def get_default_team(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    uid = _current_identity(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    user = db.execute(select(User).where(User.user_id == uid)).scalars().first()
    return {"success": True, "data": user.default_team if user else ""}


@router.post("/default-team")
def set_default_team(
    req: SetDefaultTeamRequest,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """Set the user's default team (setDefaultTeamAction equivalent)."""
    uid = _current_identity(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    user = db.execute(select(User).where(User.user_id == uid)).scalars().first()
    if not user:
        return {"success": False, "message": "用户不存在", "data": None}
    membership = find_user_team_membership(db, uid, req.teamName)
    if not membership:
        return {"success": False, "message": "无权限设置该团队为默认", "data": None}
    user.default_team = req.teamName
    db.commit()
    return {"success": True, "message": "默认团队设置成功", "data": req.teamName}


# ============================================================================
# 菜单图标目录（对齐 data-synth icon-actions getIconsAction）
# ============================================================================


@router.get("/icons")
def get_menu_icons() -> dict:
    """Available menu icon names for the system>menus editor."""
    return {"success": True, "data": MENU_ICONS}


def _admin_users() -> list[str]:
    raw = get_settings().AUTH_ADMIN_USERS
    return [u.strip() for u in raw.split(",") if u.strip()] if raw else []


__all__ = ["router"]
