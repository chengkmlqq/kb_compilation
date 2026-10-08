"""System management API routes — users / roles / teams / menus / logs.

Read queries + role/menu mutations: role CRUD, role-menu assignment
(menu authorization), role-user assignment, menu CRUD, the current
user's navigation menus (my-menus, backing the frontend Sider), the
user's own teams / team switching (aligned with data-synth
team-actions), and the menu-icon catalog (aligned with icon-actions).
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
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
    assign_user_roles,
    create_dim,
    create_team,
    create_user,
    delete_dim,
    delete_menu,
    delete_role,
    delete_team,
    delete_user,
    get_menu_apis,
    get_role_menus,
    get_role_users,
    is_platform_admin,
    list_dims,
    list_dim_groups,
    list_menus,
    list_operation_logs,
    list_roles,
    list_teams,
    list_users,
    my_menus,
    reset_user_pwd,
    save_menu,
    update_dim,
    save_menu_apis,
    save_role,
    save_role_menus,
    save_role_users,
    save_team_members,
    team_members,
    update_team,
    update_user,
    user_role_ids,
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
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_users(db, page, page_size, keyword)}


@router.get("/roles")
def get_roles(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
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


# ---------------------------------------------------------------------------
# dims — modo_dim 通用参数 CRUD（对齐 data-synth system/dims 参数管理页）
# ---------------------------------------------------------------------------


@router.get("/dims")
def get_dims(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    dim_code: str = "",
    dim_group: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_dims(db, page, page_size, dim_code, dim_group)}


@router.get("/dims/groups")
def get_dim_groups(db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_dim_groups(db)}


@router.post("/dims")
def create_dim_item(payload: dict, db: Session = Depends(get_db)) -> dict:
    try:
        return {"success": True, "data": create_dim(db, payload)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/dims/{dim_id}")
def update_dim_item(dim_id: str, payload: dict, db: Session = Depends(get_db)) -> dict:
    try:
        return {"success": True, "data": update_dim(db, dim_id, payload)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/dims/{dim_id}")
def remove_dim_item(dim_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return {"success": True, "data": delete_dim(db, dim_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
def get_teams(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
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
    page: int = Query(1, ge=1),
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


# ============================================================================
# 用户 CRUD + 角色配置 + 重置密码（对齐 data-synth UserManagerNeo 交互面）
# ============================================================================


class UserCreateRequest(BaseModel):
    userId: str
    userName: str = ""
    pwd: str = ""
    email: str = ""
    phone: str = ""
    defaultTeam: str = ""
    state: str = "1"
    roleIds: list[str] = []


class UserUpdateRequest(BaseModel):
    userName: str = ""
    email: str = ""
    phone: str = ""
    defaultTeam: str = ""
    state: str = "1"


class ResetPwdRequest(BaseModel):
    pwd: str = ""


class UserRolesRequest(BaseModel):
    roleIds: list[str] = []


class TeamCreateRequest(BaseModel):
    teamName: str
    label: str = ""
    descr: str = ""
    parentTeamName: str = ""
    state: str = "1"


class TeamUpdateRequest(BaseModel):
    label: str = ""
    descr: str = ""
    parentTeamName: str = ""
    state: str = "1"


class TeamMembersRequest(BaseModel):
    userIds: list[str] = []


class MenuApisRequest(BaseModel):
    apis: list[dict] = []


@router.post("/users")
def post_user(req: UserCreateRequest, db: Session = Depends(get_db)) -> dict:
    """新建用户（对齐 modoUser create）。"""
    return create_user(db, req.model_dump())


@router.put("/users/{user_id}")
def put_user(user_id: str, req: UserUpdateRequest, db: Session = Depends(get_db)) -> dict:
    return update_user(db, user_id, req.model_dump())


@router.post("/users/{user_id}/pwd")
def post_user_pwd(user_id: str, req: ResetPwdRequest, db: Session = Depends(get_db)) -> dict:
    """重置用户密码（对齐 ds 用户管理「更多」下拉重置密码）。"""
    return reset_user_pwd(db, user_id, req.pwd)


@router.delete("/users/{user_id}")
def remove_user(user_id: str, db: Session = Depends(get_db)) -> dict:
    return delete_user(db, user_id)


@router.get("/users/{user_id}/roles")
def get_user_roles(user_id: str, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": {"roleIds": user_role_ids(db, user_id)}}


@router.put("/users/{user_id}/roles")
def put_user_roles(user_id: str, req: UserRolesRequest, db: Session = Depends(get_db)) -> dict:
    """保存用户角色关系（对齐 userRoleRela.saveRoleRelaByUserId）。"""
    return assign_user_roles(db, user_id, req.roleIds)


# ============================================================================
# 团队 CRUD + 成员维护（对齐 data-synth TeamManagerZj 交互面）
# ============================================================================


@router.post("/teams")
def post_team(req: TeamCreateRequest, db: Session = Depends(get_db)) -> dict:
    return create_team(db, req.model_dump())


@router.put("/teams/{team_name}")
def put_team(team_name: str, req: TeamUpdateRequest, db: Session = Depends(get_db)) -> dict:
    return update_team(db, team_name, req.model_dump())


@router.delete("/teams/{team_name}")
def remove_team(team_name: str, db: Session = Depends(get_db)) -> dict:
    return delete_team(db, team_name)


@router.get("/teams/{team_name}/members")
def get_team_members(team_name: str, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": team_members(db, team_name)}


@router.put("/teams/{team_name}/members")
def put_team_members(
    team_name: str, req: TeamMembersRequest, db: Session = Depends(get_db)
) -> dict:
    return save_team_members(db, team_name, req.userIds)


# ============================================================================
# 菜单 API 权限（对齐 data-synth 菜单服务授权；中间件按此过滤）
# ============================================================================


@router.get("/menus/{menu_id}/apis")
def get_menu_apis_route(menu_id: str, db: Session = Depends(get_db)) -> dict:
    return get_menu_apis(db, menu_id)


@router.put("/menus/{menu_id}/apis")
def put_menu_apis(menu_id: str, req: MenuApisRequest, db: Session = Depends(get_db)) -> dict:
    return save_menu_apis(db, menu_id, req.apis)


__all__ = ["router"]
