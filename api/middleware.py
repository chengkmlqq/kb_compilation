"""RBAC path-permission guard for /api/v1 routes (source proxy.ts equivalent).

Ported behaviour from src/proxy.ts + role-actions.ts rawCheckPathPermission:
- Whitelist: /health, /api/v1/auth/*, /api/v1/me, /api/v1/open/* and
  /api/v1/system/my-menus (every logged-in user must be able to fetch their
  own navigation menus for the Sider).
- Identity is decoded from the shared `x-next-identity` cookie.
- Admin bypass: AUTH_ADMIN_USERS env list or a plat-mgr role (the source
  proxy's ADMIN_USERS bypass).
- Controlled API paths are mapped to their frontend page route and checked
  with check_path_permission (menu -> roles -> role_menu_rela). Requests with
  no identity, and paths that map to no menu (uncontrolled), pass through —
  full login enforcement is a later auth-hardening item, so this guard only
  restricts *known, logged-in* users on *controlled* pages.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from api.config import get_settings
from api.db import get_sessionmaker
from api.models.framework import Menu
from api.services.identity import check_path_permission, decode_identity_cookie
from api.services.system_admin import (
    is_platform_admin,
    menu_api_perms,
    my_menus,
)

WHITELIST_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/v1/auth/",
    "/api/v1/me",
    "/api/v1/open/",
)
MY_MENUS_PATH = "/api/v1/system/my-menus"

# API path prefix -> frontend page route (modo_menu.route). Longest first.
API_ROUTE_MAP: tuple[tuple[str, str], ...] = (
    ("/api/v1/kbs", "/kbs"),
    ("/api/v1/weknora", "/kbs"),  # WeKnora-compatible KB endpoints share the KB page control
    ("/api/v1/qa", "/chat"),
    ("/api/v1/sessions", "/chat"),
    ("/api/v1/agents", "/agents"),
    ("/api/v1/jobs", "/jobs"),
    ("/api/v1/cron", "/cron"),
    ("/api/v1/workers", "/workers"),
    ("/api/v1/files", "/files"),
    ("/api/v1/models", "/models"),
    ("/api/v1/system", "/system"),
    ("/api/v1/mcps", "/system"),
    ("/api/v1/skills", "/system"),
    ("/api/v1/datasources", "/datasources"),
    ("/api/v1/datagrid", "/datagrid"),
)


def resolve_page_route(path: str) -> str | None:
    """Map an API path to its frontend page route, or None when uncontrolled."""
    for prefix, route in API_ROUTE_MAP:
        if path == prefix or path.startswith(f"{prefix}/"):
            return route
    return None


def is_whitelisted(path: str) -> bool:
    if path == MY_MENUS_PATH:
        return True
    return any(path.startswith(p) for p in WHITELIST_PREFIXES)


def _api_perm_match(path: str, method: str, perm: dict) -> bool:
    """API 权限条目匹配：path 前缀匹配 + 方法相等（'*' 通配任意方法）。"""
    p = (perm.get("path") or "").rstrip("/")
    m = (perm.get("method") or "*").strip().upper()
    if not p:
        return False
    if path == p or path.startswith(f"{p}/"):
        return m == "*" or m == method
    return False


def _menu_api_guard(db, identity_user_id: str, path: str, method: str, page_route: str) -> bool:
    """菜单 API 授权过滤（对齐 data-synth 菜单服务授权）。

    仅当用户可访问的菜单中、与当前页面 route 匹配的菜单配置了
    api_perms 时才收紧：请求的 API 必须命中其中一条，否则拒绝。
    未配置 api_perms 的菜单保持原行为（不影响现有部署）。
    """
    menu_data = my_menus(db, identity_user_id, is_admin=False)
    menu_ids = [x["menu_id"] for x in menu_data.get("items", [])]
    restricted = False
    covered = False
    for mid in menu_ids:
        mrow = db.execute(select(Menu).where(Menu.menu_id == mid)).scalars().first()
        if not mrow:
            continue
        if (mrow.route or "").rstrip("/") != page_route:
            continue
        perms = menu_api_perms(db, mid)
        if not perms:
            continue
        restricted = True
        if any(_api_perm_match(path, method, p) for p in perms):
            covered = True
    if restricted and not covered:
        return False
    return True


async def rbac_guard(request: Request, call_next):
    """FastAPI middleware entry — registered in api/main.py."""
    path = request.url.path

    if is_whitelisted(path):
        return await call_next(request)

    page_route = resolve_page_route(path)
    if page_route is None:
        return await call_next(request)

    identity = decode_identity_cookie(request.cookies.get("x-next-identity") or "")
    if not identity or not identity.user_id:
        # No identity on a controlled path: fail-closed (login enforcement).
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "未登录", "reason": "需要登录后访问"},
        )

    db = get_sessionmaker()()
    try:
        settings = get_settings()
        admin_users = [u.strip() for u in settings.AUTH_ADMIN_USERS.split(",") if u.strip()]
        if is_platform_admin(db, identity.user_id, admin_users):
            return await call_next(request)
        allowed, reason = check_path_permission(db, identity.user_id, page_route)
        if not allowed:
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": "访问被拒绝", "reason": reason},
            )
        if not _menu_api_guard(db, identity.user_id, path, request.method.upper(), page_route):
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "访问被拒绝",
                    "reason": "该 API 未被菜单服务授权",
                },
            )
    finally:
        db.close()
    return await call_next(request)


__all__ = ["rbac_guard", "is_whitelisted", "resolve_page_route"]