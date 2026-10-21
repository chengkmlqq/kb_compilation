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

import json

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from api.config import get_settings
from api.db import get_sessionmaker, reset_request_db, set_request_db
from api.models.framework import Menu
from api.services.identity import check_path_permission, decode_identity_cookie
from api.services.system_admin import (
    is_platform_admin,
    my_menus,
)

WHITELIST_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/v1/auth/",
    "/api/v1/me",
    "/api/v1/open/",
)
MY_MENUS_PATH = "/api/v1/system/my-menus"


def normalize_content_type(request: Request, call_next):
    """把逗号拼接的重复 content-type 归一化（'application/json, application/json' → 'application/json'）。

    根因（2026-10-11「新建目录 422」）：前端 request() 的默认小写 content-type 与
    调用方显式的 Content-Type 两个大小写不同的键同时存在时，新版 Chromium 合并成
    单个逗号值；starlette 不识别 → JSON body 按字符串解析 → 422
    model_attributes_type。前端已去重；此中间件兼容存量客户端/旧缓存浏览器，
    使其立即恢复可用。合法单值 content-type 不含逗号，取第一段即可。
    """
    raw = request.scope.get("headers", [])
    new_headers: list[tuple[bytes, bytes]] = []
    changed = False
    for key, value in raw:
        if key.lower() == b"content-type":
            text = value.decode("latin-1")
            if "," in text:
                first = text.split(",", 1)[0].strip()
                new_headers.append((key, first.encode("latin-1")))
                changed = True
                continue
        new_headers.append((key, value))
    if changed:
        request.scope["headers"] = new_headers
    return call_next(request)

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
    ("/api/v1/feedback", "/feedback-stats"),
    ("/api/v1/usage", "/usage"),  # 2026-10-16 LLM 使用看板
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

    2026-10-21 性能修复：原实现对 my_menus 返回的每个菜单单独
    `select(Menu)` + `menu_api_perms`（每请求 N+1 ~ 2N 次查询，
    叠加远程库 RTT 是列表接口慢的主因之一）；改为一次 `IN (...)` 批量
    取回全部候选菜单，路由匹配与 api_perms 解析在内存完成。
    """
    menu_data = my_menus(db, identity_user_id, is_admin=False)
    menu_ids = [x["menu_id"] for x in menu_data.get("items", [])]
    if not menu_ids:
        return True
    # 一次批量取回候选菜单行（原实现逐 mid 单查）
    rows = (
        db.execute(select(Menu).where(Menu.menu_id.in_(menu_ids)))
        .scalars()
        .all()
    )
    restricted = False
    covered = False
    for mrow in rows:
        if (mrow.route or "").rstrip("/") != page_route:
            continue
        perms = _menu_ext_perms(mrow)
        if not perms:
            continue
        restricted = True
        if any(_api_perm_match(path, method, p) for p in perms):
            covered = True
    if restricted and not covered:
        return False
    return True


def _menu_ext_perms(mrow: Menu) -> list[dict]:
    """解析菜单 menu_ext_conf 的 api_perms（内存版，不查库）。

    与 services.system_admin.menu_api_perms 的 JSON 结构语义一致；
    middleware 用它消除逐菜单 N+1（menu_api_perms 每次再查一次 Menu）。
    """
    if not mrow or not mrow.menu_ext_conf:
        return []
    try:
        conf = json.loads(mrow.menu_ext_conf)
        return conf.get("api_perms") or []
    except (ValueError, TypeError):
        return []


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
    # 2026-10-21 连接复用：中间件 session 写入 _REQUEST_DB contextvar，
    # 端点 get_db 优先复用同一连接。远程库（10.1.206.135）连接建立 ~100ms，
    # 此前中间件与端点各建一个 session = 每请求两次连接成本，列表接口显著变慢。
    # 生命周期：放行后由端点 get_db 负责 close（BaseHTTPMiddleware 的 finally
    # 早于端点执行，不能在此 close）；仅提前返回（403）与异常路径自行 close。
    set_request_db(db)
    try:
        settings = get_settings()
        admin_users = [u.strip() for u in settings.AUTH_ADMIN_USERS.split(",") if u.strip()]
        if is_platform_admin(db, identity.user_id, admin_users):
            return await call_next(request)
        allowed, reason = check_path_permission(db, identity.user_id, page_route)
        if not allowed:
            reset_request_db()
            db.close()
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": "访问被拒绝", "reason": reason},
            )
        if not _menu_api_guard(db, identity.user_id, path, request.method.upper(), page_route):
            reset_request_db()
            db.close()
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "访问被拒绝",
                    "reason": "该 API 未被菜单服务授权",
                },
            )
    except Exception:
        # 异常路径：端点不会执行，自行归还连接并恢复 contextvar
        reset_request_db()
        db.close()
        raise
    # 放行：db 交端点 get_db 复用（其 finally db.close() 归还连接）。
    # 端点若未依赖 get_db（无 DB 路由），此处 finally 兜底归还；
    # Session.close() 幂等，端点已 close 再 close 无害。
    try:
        return await call_next(request)
    finally:
        db.close()
        reset_request_db()


__all__ = ["rbac_guard", "is_whitelisted", "resolve_page_route"]