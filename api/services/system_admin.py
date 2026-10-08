"""System management service — users, roles, teams, menus, operation logs.

Read queries back the frontend "系统管理" pages. Role/menu mutations
(save/delete + role-menu / role-user assignments) were migrated from the
source platform's role-actions.ts / menu-actions.ts server actions so the
platform can control page access via role-menu assignment (RBAC).

All functions take an explicit framework-store session.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_encrypt
from api.models.framework import (
    Dim,
    Menu,
    OperLog,
    RoleMenuRela,
    Team,
    TeamMember,
    User,
    UserRole,
    UserRoleRela,
)
from api.services.identity import collect_user_role_ids

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


# ============================================================================
# Role CRUD (saveRoleAction / deleteRoleAction)
# ============================================================================


def save_role(db: Session, payload: dict) -> dict:
    """Create or update a role. `is_edit=true` updates the existing role_id.

    Mirrors the source role form: role_id (immutable when editing),
    role_name, role_type (plat-mgr | team-role), role_descr, state (1/0).
    """
    role_id = (payload.get("role_id") or "").strip()
    role_name = (payload.get("role_name") or "").strip()
    role_type = (payload.get("role_type") or "team-role").strip()
    role_descr = payload.get("role_descr") or ""
    state = str(payload.get("state") or "1")
    is_edit = bool(payload.get("is_edit"))

    if not role_id or not role_name:
        return {"success": False, "message": "角色编码与角色名称必填"}
    if role_type not in ("plat-mgr", "team-role"):
        return {"success": False, "message": "非法角色类型"}

    existing = db.execute(select(UserRole).where(UserRole.role_id == role_id)).scalars().first()
    if is_edit:
        if not existing:
            return {"success": False, "message": f"角色不存在: {role_id}"}
        existing.role_name = role_name
        existing.role_type = role_type
        existing.role_descr = role_descr
        existing.state = state
        db.commit()
        return {"success": True, "message": "更新成功"}
    if existing:
        return {"success": False, "message": f"角色编码已存在: {role_id}"}
    db.add(
        UserRole(
            role_id=role_id,
            role_name=role_name,
            role_type=role_type,
            role_descr=role_descr,
            state=state,
        )
    )
    db.commit()
    return {"success": True, "message": "创建成功"}


def delete_role(db: Session, role_id: str) -> dict:
    """Delete a role. Refuses when users are still bound; cascades role-menu links."""
    role = db.execute(select(UserRole).where(UserRole.role_id == role_id)).scalars().first()
    if not role:
        return {"success": False, "message": f"角色不存在: {role_id}"}
    bound = db.execute(
        select(UserRoleRela.rela_id).where(UserRoleRela.role_id == role_id).limit(1)
    ).scalars().first()
    if bound:
        return {"success": False, "message": "该角色仍绑定用户，请先解除用户配置"}
    db.execute(delete(RoleMenuRela).where(RoleMenuRela.role_id == role_id))
    db.delete(role)
    db.commit()
    return {"success": True, "message": "删除成功"}


# ============================================================================
# Role -> Menu assignment (getRoleMenusAction / saveRoleMenusAction)
# ============================================================================


def get_role_menus(db: Session, role_id: str) -> dict:
    rows = db.execute(
        select(RoleMenuRela.menu_id).where(RoleMenuRela.role_id == role_id)
    ).scalars().all()
    return {"menuIds": list(rows)}


def save_role_menus(db: Session, role_id: str, menu_ids: list[str]) -> dict:
    """Replace the role's menu assignment (delete old links, insert new ones)."""
    role = db.execute(select(UserRole).where(UserRole.role_id == role_id)).scalars().first()
    if not role:
        return {"success": False, "message": f"角色不存在: {role_id}"}
    valid = []
    for menu_id in menu_ids or []:
        exists = db.execute(
            select(Menu.menu_id).where(Menu.menu_id == menu_id).limit(1)
        ).scalars().first()
        if exists:
            valid.append(menu_id)
    db.execute(delete(RoleMenuRela).where(RoleMenuRela.role_id == role_id))
    for menu_id in valid:
        db.add(RoleMenuRela(rela_id=uuid.uuid4().hex, role_id=role_id, menu_id=menu_id))
    db.commit()
    return {"success": True, "message": f"已分配 {len(valid)} 个菜单"}


# ============================================================================
# Role -> User assignment (getRoleUsersAction / saveRoleUsersAction)
# ============================================================================


def get_role_users(db: Session, role_id: str) -> dict:
    rows = db.execute(
        select(UserRoleRela.user_id).where(UserRoleRela.role_id == role_id)
    ).scalars().all()
    return {"userIds": list(rows)}


def save_role_users(db: Session, role_id: str, user_ids: list[str]) -> dict:
    """Replace the role's user assignment (delete old links, insert new ones)."""
    role = db.execute(select(UserRole).where(UserRole.role_id == role_id)).scalars().first()
    if not role:
        return {"success": False, "message": f"角色不存在: {role_id}"}
    valid = []
    for user_id in user_ids or []:
        exists = db.execute(
            select(User.user_id).where(User.user_id == user_id).limit(1)
        ).scalars().first()
        if exists:
            valid.append(user_id)
    db.execute(delete(UserRoleRela).where(UserRoleRela.role_id == role_id))
    for user_id in valid:
        db.add(UserRoleRela(rela_id=uuid.uuid4().hex, role_id=role_id, user_id=user_id))
    db.commit()
    return {"success": True, "message": f"已配置 {len(valid)} 个用户"}


# ============================================================================
# Menu CRUD (saveMenuAction / deleteMenuAction)
# ============================================================================

_MENU_ROUTE_FIELDS = ("open_type", "link_type", "fetch_mode", "route", "route_param", "url")


def _menu_subtree_ids(db: Session, root_id: str) -> set[str]:
    """收集 root_id 自身 + 全部后代菜单 id（BFS，避免深递归）。"""
    children: dict[str, list[str]] = {}
    for mid, pid in db.execute(select(Menu.menu_id, Menu.parent_id)).all():
        children.setdefault(pid or "", []).append(mid)
    out: set[str] = {root_id}
    queue = [root_id]
    while queue:
        cur = queue.pop()
        for cid in children.get(cur, []):
            if cid not in out:
                out.add(cid)
                queue.append(cid)
    return out


def _check_menu_parent(db: Session, menu_id: str | None, parent_id: str | None) -> str | None:
    """校验父模块：非空时父级必须存在，且不得把菜单挂到自己的后代下（成环）。"""
    if not parent_id:
        return None
    if menu_id and parent_id == menu_id:
        return "父模块不能是自身"
    exists = db.execute(
        select(Menu.menu_id).where(Menu.menu_id == parent_id).limit(1)
    ).scalars().first()
    if not exists:
        return f"父模块不存在: {parent_id}"
    if menu_id and parent_id in _menu_subtree_ids(db, menu_id):
        return "父模块不能是自己的子菜单（会形成环状菜单树）"
    return None


def save_menu(db: Session, payload: dict) -> dict:
    """Create or update a menu. `is_edit=true` updates menu_id.

    The source menu form submits basic fields plus an ext-conf JSON
    ({openType, linkType, fetchMode, route, routeParam, url}); we build
    menu_ext_conf the same way when a dict is passed.
    """
    menu_name = (payload.get("menu_name") or "").strip()
    menu_label = (payload.get("menu_label") or "").strip()
    if not menu_name or not menu_label:
        return {"success": False, "message": "模块编码与模块中文名必填"}

    parent_id = (payload.get("parent_id") or "").strip() or None
    try:
        sort_num = int(payload.get("sort_num") or 1)
    except (TypeError, ValueError):
        sort_num = 1
    state = str(payload.get("state") or "1")
    menu_type = str(payload.get("menu_type") or "frame")
    route = (payload.get("route") or "").strip() or None
    menu_icon = payload.get("menu_icon") or None
    menu_descr = payload.get("menu_descr") or None

    ext_conf = payload.get("menu_ext_conf")
    if isinstance(ext_conf, dict):
        ext_conf = (
            json.dumps({k: v for k, v in ext_conf.items() if k in _MENU_ROUTE_FIELDS}, ensure_ascii=False)
            if ext_conf
            else None
        )
    elif isinstance(ext_conf, str):
        ext_conf = ext_conf or None

    if bool(payload.get("is_edit")):
        menu_id = payload.get("menu_id")
        menu = db.execute(select(Menu).where(Menu.menu_id == menu_id)).scalars().first()
        if not menu:
            return {"success": False, "message": f"菜单不存在: {menu_id}"}
        # 防成环：父模块不得是自身或其后代（多层菜单骨架必需的保护）
        cycle_err = _check_menu_parent(db, str(menu_id), parent_id)
        if cycle_err:
            return {"success": False, "message": cycle_err}
        menu.menu_name = menu_name
        menu.menu_label = menu_label
        menu.parent_id = parent_id
        menu.sort_num = sort_num
        menu.state = state
        menu.menu_type = menu_type
        menu.route = route
        menu.menu_icon = menu_icon
        menu.menu_descr = menu_descr
        if ext_conf is not None:
            menu.menu_ext_conf = ext_conf
        db.commit()
        return {"success": True, "message": "更新成功"}
    # 新建：父模块必须存在（创建时尚无自身 id，无需成环判断）
    parent_err = _check_menu_parent(db, None, parent_id)
    if parent_err:
        return {"success": False, "message": parent_err}
    new_id = uuid.uuid4().hex
    db.add(
        Menu(
            menu_id=new_id,
            menu_name=menu_name,
            menu_label=menu_label,
            menu_descr=menu_descr,
            menu_ext_conf=ext_conf,
            menu_icon=menu_icon,
            menu_type=menu_type,
            route=route,
            parent_id=parent_id,
            sort_num=sort_num,
            state=state,
        )
    )
    db.commit()
    return {"success": True, "message": "创建成功", "data": {"menu_id": new_id}}


def delete_menu(db: Session, menu_id: str) -> dict:
    """Delete a menu. Refuses when child menus exist or a role still references it."""
    menu = db.execute(select(Menu).where(Menu.menu_id == menu_id)).scalars().first()
    if not menu:
        return {"success": False, "message": f"菜单不存在: {menu_id}"}
    child = db.execute(
        select(Menu.menu_id).where(Menu.parent_id == menu_id).limit(1)
    ).scalars().first()
    if child:
        return {"success": False, "message": "存在子菜单，请先删除子菜单"}
    ref = db.execute(
        select(RoleMenuRela.rela_id).where(RoleMenuRela.menu_id == menu_id).limit(1)
    ).scalars().first()
    if ref:
        return {"success": False, "message": "该菜单已被角色引用，请先解除授权"}
    db.delete(menu)
    db.commit()
    return {"success": True, "message": "删除成功"}


# ============================================================================
# Current-user navigation menus (getNavigationMenusAction equivalent)
# ============================================================================


def my_menus(db: Session, user_id: str, is_admin: bool = False) -> dict:
    """Menus visible to the current user (state='1', sorted by sort_num).

    Platform admins (plat-mgr role or AUTH_ADMIN_USERS) get the full tree;
    other users get only menus linked to their roles, PLUS all ancestor menus
    (aligned with data-synth getNavigationMenusAction expandedIds logic), so
    the frontend can always build a complete tree from the flat list.
    """
    all_rows = db.execute(
        select(Menu).where(Menu.state == "1").order_by(Menu.sort_num, Menu.create_date)
    ).scalars().all()

    if is_admin:
        rows = all_rows
    else:
        role_ids = collect_user_role_ids(db, user_id)
        if not role_ids:
            return {"items": [], "total": 0}
        linked = db.execute(
            select(RoleMenuRela.menu_id).where(RoleMenuRela.role_id.in_(role_ids))
        ).scalars().all()
        if not linked:
            return {"items": [], "total": 0}
        # 从角色关联的 menu_id 向上补全所有祖先（对齐 source expandedIds）
        menu_map = {m.menu_id: m for m in all_rows}
        expanded = set(linked)
        for mid in linked:
            cur = menu_map.get(mid)
            while cur and cur.parent_id:
                if cur.parent_id in expanded:
                    break
                expanded.add(cur.parent_id)
                cur = menu_map.get(cur.parent_id)
        rows = [m for m in all_rows if m.menu_id in expanded]

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


def is_platform_admin(db: Session, user_id: str, admin_users: list[str] | None = None) -> bool:
    """True when the user is an explicit platform admin.

    Mirrors the source proxy's ADMIN_USERS bypass (env AUTH_ADMIN_USERS),
    NOT the role_type column — the shared seed data marks several non-admin
    roles with role_type='plat-mgr', so a role-type check would silently
    bypass the guard for ordinary users. `db` is kept for call-site symmetry.
    """
    return bool(admin_users and user_id in admin_users)


# ============================================================================
# User CRUD（对齐 data-synth modoUser create / update / deleteRealAll /
# userRoleRela 角色关系）——系统管理页「用户」全量 CRUD
# ============================================================================


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_user(db: Session, payload: dict) -> dict:
    """新建用户。pwd 明文入参，落库 AES 加密（登录校验同款）。"""
    user_id = (payload.get("userId") or "").strip()
    if not user_id:
        return {"success": False, "message": "用户ID不能为空", "data": None}
    exists = db.execute(select(User).where(User.user_id == user_id)).scalars().first()
    if exists:
        return {"success": False, "message": f"用户 {user_id} 已存在", "data": None}
    pwd = payload.get("pwd") or ""
    if not pwd:
        return {"success": False, "message": "密码不能为空", "data": None}
    user = User(
        id=uuid.uuid4().hex[:32],
        user_id=user_id,
        user_name=payload.get("userName") or "",
        user_pwd=aes_encrypt(pwd),
        email=payload.get("email") or "",
        phone=payload.get("phone") or "",
        create_dt=_now(),
        state=payload.get("state") or "1",
        default_team=payload.get("defaultTeam") or "",
    )
    db.add(user)
    db.flush()
    role_ids = payload.get("roleIds") or []
    if role_ids:
        _assign_user_roles(db, user_id, role_ids)
    db.commit()
    return {"success": True, "message": "用户已创建", "data": {"user_id": user_id}}


def update_user(db: Session, user_id: str, payload: dict) -> dict:
    user = db.execute(select(User).where(User.user_id == user_id)).scalars().first()
    if not user:
        return {"success": False, "message": "用户不存在", "data": None}
    user.user_name = payload.get("userName", user.user_name) or ""
    user.email = payload.get("email", user.email) or ""
    user.phone = payload.get("phone", user.phone) or ""
    user.default_team = payload.get("defaultTeam", user.default_team) or ""
    user.state = payload.get("state", user.state) or "1"
    db.commit()
    return {"success": True, "message": "用户已更新", "data": {"user_id": user_id}}


def delete_user(db: Session, user_id: str) -> dict:
    user = db.execute(select(User).where(User.user_id == user_id)).scalars().first()
    if not user:
        return {"success": False, "message": "用户不存在", "data": None}
    # 连带清理：角色关系 + 团队成员关系（对齐 ds deleteRealAll 语义）
    db.execute(delete(UserRoleRela).where(UserRoleRela.user_id == user_id))
    db.execute(delete(TeamMember).where(TeamMember.user_id == user_id))
    db.delete(user)
    db.commit()
    return {"success": True, "message": "用户已删除", "data": {"user_id": user_id}}


def reset_user_pwd(db: Session, user_id: str, pwd: str) -> dict:
    user = db.execute(select(User).where(User.user_id == user_id)).scalars().first()
    if not user:
        return {"success": False, "message": "用户不存在", "data": None}
    if not pwd:
        return {"success": False, "message": "新密码不能为空", "data": None}
    user.user_pwd = aes_encrypt(pwd)
    db.commit()
    return {"success": True, "message": "密码已重置", "data": {"user_id": user_id}}


def user_role_ids(db: Session, user_id: str) -> list[str]:
    return list(
        db.execute(select(UserRoleRela.role_id).where(UserRoleRela.user_id == user_id))
        .scalars()
        .all()
    )


def _assign_user_roles(db: Session, user_id: str, role_ids: list[str]) -> None:
    db.execute(delete(UserRoleRela).where(UserRoleRela.user_id == user_id))
    for rid in role_ids:
        db.add(
            UserRoleRela(
                rela_id=uuid.uuid4().hex[:32],
                role_id=rid,
                user_id=user_id,
            )
        )


def assign_user_roles(db: Session, user_id: str, role_ids: list[str]) -> dict:
    user = db.execute(select(User).where(User.user_id == user_id)).scalars().first()
    if not user:
        return {"success": False, "message": "用户不存在", "data": None}
    valid = set(
        db.execute(select(UserRole.role_id)).scalars().all()
    )
    bad = [r for r in role_ids if r not in valid]
    if bad:
        return {"success": False, "message": f"角色不存在: {bad}", "data": None}
    _assign_user_roles(db, user_id, role_ids)
    db.commit()
    return {"success": True, "message": "角色分配成功", "data": {"user_id": user_id}}


# ============================================================================
# Team CRUD（对齐 data-synth 团队管理 create / edit / delete + 成员维护）
# ============================================================================


def create_team(db: Session, payload: dict) -> dict:
    team_name = (payload.get("teamName") or "").strip()
    if not team_name:
        return {"success": False, "message": "团队编码不能为空", "data": None}
    exists = db.execute(select(Team).where(Team.team_name == team_name)).scalars().first()
    if exists:
        return {"success": False, "message": f"团队 {team_name} 已存在", "data": None}
    parent = payload.get("parentTeamName") or ""
    parent_team = None
    if parent:
        parent_team = db.execute(select(Team).where(Team.team_name == parent)).scalars().first()
        if not parent_team:
            return {"success": False, "message": f"父团队 {parent} 不存在", "data": None}
    team = Team(
        team_id=uuid.uuid4().hex[:32],
        team_name=team_name,
        label=payload.get("label") or "",
        descr=payload.get("descr") or "",
        parent_team_name=parent or "",
        parent_team_id=parent_team.team_id if parent_team else None,
        state=payload.get("state") or "1",
        create_dt=_now(),
    )
    db.add(team)
    db.commit()
    return {"success": True, "message": "团队已创建", "data": {"team_name": team_name}}


def update_team(db: Session, team_name: str, payload: dict) -> dict:
    team = db.execute(select(Team).where(Team.team_name == team_name)).scalars().first()
    if not team:
        return {"success": False, "message": "团队不存在", "data": None}
    parent = payload.get("parentTeamName")
    if parent is not None:
        if parent and parent != team_name:
            parent_team = db.execute(select(Team).where(Team.team_name == parent)).scalars().first()
            if not parent_team:
                return {"success": False, "message": f"父团队 {parent} 不存在", "data": None}
            team.parent_team_name = parent
            team.parent_team_id = parent_team.team_id
        else:
            team.parent_team_name = parent or ""
            team.parent_team_id = None
    if "label" in payload:
        team.label = payload["label"] or ""
    if "descr" in payload:
        team.descr = payload["descr"] or ""
    if "state" in payload:
        team.state = payload["state"] or "1"
    db.commit()
    return {"success": True, "message": "团队已更新", "data": {"team_name": team_name}}


def delete_team(db: Session, team_name: str) -> dict:
    team = db.execute(select(Team).where(Team.team_name == team_name)).scalars().first()
    if not team:
        return {"success": False, "message": "团队不存在", "data": None}
    if team_name == "ROOT":
        return {"success": False, "message": "根团队 ROOT 不允许删除", "data": None}
    # 连带清理：团队成员关系 + 用户默认团队引用
    db.execute(delete(TeamMember).where(TeamMember.team_name == team_name))
    users = db.execute(select(User).where(User.default_team == team_name)).scalars().all()
    for u in users:
        u.default_team = ""
    # 子团队父级引用清空
    children = db.execute(select(Team).where(Team.parent_team_name == team_name)).scalars().all()
    for c in children:
        c.parent_team_name = ""
        c.parent_team_id = None
    db.delete(team)
    db.commit()
    return {"success": True, "message": "团队已删除", "data": {"team_name": team_name}}


def team_members(db: Session, team_name: str) -> dict:
    """团队成员（含用户基本信息），对齐 ds 团队 Tabs 成员面板。"""
    rows = db.execute(
        select(TeamMember, User)
        .join(User, User.user_id == TeamMember.user_id, isouter=True)
        .where(TeamMember.team_name == team_name)
    ).all()
    items = [
        {
            "user_id": tm.user_id,
            "user_name": u.user_name if u else "",
            "email": u.email if u else "",
            "phone": u.phone if u else "",
            "state": u.state if u else "",
        }
        for tm, u in rows
    ]
    return {"items": items, "total": len(items)}


def save_team_members(db: Session, team_name: str, user_ids: list[str]) -> dict:
    team = db.execute(select(Team).where(Team.team_name == team_name)).scalars().first()
    if not team:
        return {"success": False, "message": "团队不存在", "data": None}
    valid = set(db.execute(select(User.user_id)).scalars().all())
    bad = [u for u in user_ids if u not in valid]
    if bad:
        return {"success": False, "message": f"用户不存在: {bad}", "data": None}
    db.execute(delete(TeamMember).where(TeamMember.team_name == team_name))
    for uid in user_ids:
        db.add(
            TeamMember(
                member_id=uuid.uuid4().hex[:32],
                team_name=team_name,
                user_id=uid,
                create_dt=_now(),
            )
        )
    db.commit()
    return {"success": True, "message": "团队成员已保存", "data": {"team_name": team_name}}


# ============================================================================
# 菜单 API 权限（对齐 data-synth 菜单服务授权；存 menu_ext_conf JSON）
# ============================================================================


def get_menu_apis(db: Session, menu_id: str) -> dict:
    menu = db.execute(select(Menu).where(Menu.menu_id == menu_id)).scalars().first()
    if not menu:
        return {"success": False, "message": "菜单不存在", "data": None}
    apis: list[dict] = []
    if menu.menu_ext_conf:
        try:
            conf = json.loads(menu.menu_ext_conf)
            apis = conf.get("api_perms") or []
        except (ValueError, TypeError):
            apis = []
    return {"success": True, "data": {"menu_id": menu_id, "apis": apis}}


def save_menu_apis(db: Session, menu_id: str, apis: list[dict]) -> dict:
    menu = db.execute(select(Menu).where(Menu.menu_id == menu_id)).scalars().first()
    if not menu:
        return {"success": False, "message": "菜单不存在", "data": None}
    cleaned: list[dict] = []
    for a in apis or []:
        path = (a.get("path") or "").strip()
        method = (a.get("method") or "GET").strip().upper()
        if path:
            cleaned.append({"path": path, "method": method})
    conf: dict = {}
    if menu.menu_ext_conf:
        try:
            conf = json.loads(menu.menu_ext_conf) or {}
        except (ValueError, TypeError):
            conf = {}
    conf["api_perms"] = cleaned
    menu.menu_ext_conf = json.dumps(conf, ensure_ascii=False)
    db.commit()
    return {"success": True, "message": "API 权限已保存", "data": {"menu_id": menu_id}}


def menu_api_perms(db: Session, menu_id: str) -> list[dict]:
    """Read-only helper for the middleware RBAC guard: api_perms of a menu."""
    menu = db.execute(select(Menu).where(Menu.menu_id == menu_id)).scalars().first()
    if not menu or not menu.menu_ext_conf:
        return []
    try:
        conf = json.loads(menu.menu_ext_conf)
        return conf.get("api_perms") or []
    except (ValueError, TypeError):
        return []


__all__ = [
    "assign_user_roles",
    "create_team",
    "create_user",
    "delete_menu",
    "delete_role",
    "delete_team",
    "delete_user",
    "get_menu_apis",
    "get_role_menus",
    "get_role_users",
    "is_platform_admin",
    "list_menus",
    "list_operation_logs",
    "list_roles",
    "list_teams",
    "list_users",
    "menu_api_perms",
    "my_menus",
    "reset_user_pwd",
    "save_menu",
    "save_menu_apis",
    "save_role",
    "save_role_menus",
    "save_role_users",
    "save_team_members",
    "team_members",
    "update_team",
    "update_user",
    "user_role_ids",
    "user_roles",
    # ---- dims (modo_dim 通用参数 CRUD, 对齐 data-synth system/dims) ----
    "list_dims",
    "list_dim_groups",
    "create_dim",
    "update_dim",
    "delete_dim",
]


# ============================================================================
# dims — modo_dim 通用参数 CRUD（对齐 data-synth system/dims 参数管理页）
# ============================================================================


def _dim_to_dict(d: Dim) -> dict:
    return {
        "id": d.id,
        "dim_code": d.dim_code,
        "dim_group": d.dim_group,
        "dim_value": d.dim_value,
        "dim_desc": d.dim_desc,
        "parent_dim_code": d.parent_dim_code,
        "seq": d.seq,
        "state": d.state,
    }


def list_dims(
    db: Session,
    page: int = 1,
    page_size: int = 10,
    dim_code: str = "",
    dim_group: str = "",
) -> dict:
    stmt = select(Dim)
    if dim_code:
        stmt = stmt.where(Dim.dim_code.ilike(f"%{dim_code.strip()}%"))
    if dim_group:
        stmt = stmt.where(Dim.dim_group.ilike(f"%{dim_group.strip()}%"))
    rows_all = db.execute(
        stmt.order_by(Dim.seq, Dim.dim_code)
    ).scalars().all()
    total = len(rows_all)
    rows = [
        _dim_to_dict(d)
        for d in rows_all[(page - 1) * page_size : page * page_size]
    ]
    return _paginate(rows, total, page, page_size)


def list_dim_groups(db: Session) -> dict:
    rows = db.execute(select(Dim.dim_group).distinct()).scalars().all()
    items = sorted({g for g in rows if g})
    return {"items": items, "total": len(items)}


def create_dim(db: Session, payload: dict) -> dict:
    dim_code = str(payload.get("dim_code") or "").strip()
    if not dim_code:
        raise ValueError("dim_code is required")
    dup = db.execute(
        select(Dim).where(Dim.dim_code == dim_code)
    ).scalars().first()
    if dup:
        raise ValueError(f"dim_code already exists: {dim_code}")
    dim = Dim(
        id=uuid.uuid4().hex,
        dim_code=dim_code,
        dim_group=payload.get("dim_group") or None,
        dim_value=payload.get("dim_value") or None,
        dim_desc=payload.get("dim_desc") or None,
        parent_dim_code=payload.get("parent_dim_code") or None,
        seq=int(payload["seq"]) if payload.get("seq") is not None else 0,
        state=str(payload.get("state") or "1"),
    )
    db.add(dim)
    db.commit()
    db.refresh(dim)
    return {"id": dim.id, "created": True}


def update_dim(db: Session, dim_id: str, payload: dict) -> dict:
    dim = db.execute(
        select(Dim).where(Dim.id == dim_id)
    ).scalars().first()
    if not dim:
        raise ValueError(f"dim not found: {dim_id}")
    dim_code = str(payload.get("dim_code") or "").strip()
    if dim_code and dim_code != dim.dim_code:
        dup = db.execute(
            select(Dim).where(Dim.dim_code == dim_code, Dim.id != dim_id)
        ).scalars().first()
        if dup:
            raise ValueError(f"dim_code already exists: {dim_code}")
    for field, attr in (
        ("dim_code", "dim_code"),
        ("dim_group", "dim_group"),
        ("dim_value", "dim_value"),
        ("dim_desc", "dim_desc"),
        ("parent_dim_code", "parent_dim_code"),
        ("state", "state"),
    ):
        if field in payload:
            setattr(dim, attr, payload[field] or None)
    if "seq" in payload and payload["seq"] is not None:
        dim.seq = int(payload["seq"])
    db.commit()
    return {"id": dim.id, "updated": True}


def delete_dim(db: Session, dim_id: str) -> dict:
    dim = db.execute(
        select(Dim).where(Dim.id == dim_id)
    ).scalars().first()
    if not dim:
        raise ValueError(f"dim not found: {dim_id}")
    db.delete(dim)
    db.commit()
    return {"id": dim_id, "deleted": True}
