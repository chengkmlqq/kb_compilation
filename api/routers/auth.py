"""Login / logout API — ported from the source platform's login-actions.

Note: the frontend still uses server actions; this API exists so the extracted
backend can serve non-Next clients (Open API / MCP) and later the refactored
frontend. Password comparison uses the shared AES-encrypted `user_pwd` column.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.config import get_settings
from api.lib.crypto import aes_encrypt
from api.models.framework import OperLog, User
from api.services.identity import (
    Identity,
    encode_identity_cookie,
    resolve_active_user_identity,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    userId: str = Field(..., description="登录名")
    pwd: str = Field(..., description="明文密码（服务端 AES 加密后比对）")


class LoginResponse(BaseModel):
    success: bool
    message: str
    # camelCase identity payload for the frontend (mirrors the TS Identity shape)
    data: dict | None = None
    identity_cookie: str | None = None


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    raw_user_id = req.userId.strip()
    raw_pwd = req.pwd
    if not raw_user_id or not raw_pwd:
        return LoginResponse(success=False, message="用户名或密码不能为空")

    # Same comparison as TS: DB stores AES-encrypted password.
    enc_pwd = aes_encrypt(raw_pwd)
    user = db.execute(select(User).where(User.user_id == raw_user_id)).scalars().first()
    if not user:
        return LoginResponse(success=False, message="用户不存在")
    if user.state == "1" and user.user_pwd == enc_pwd:
        identity = resolve_active_user_identity(db, raw_user_id, user.user_name)
        if not identity:
            return LoginResponse(success=False, message="用户未启用或团队信息异常")
        # Log login (mirrors saveLogAction).
        db.add(
            OperLog(
                id=__import__("uuid").uuid4().hex,
                oper_type="LOGIN",
                oper_content=f"{identity.user_name}登录的系统",
                oper_url="/login",
                user_id=identity.user_id,
                user_name=identity.user_name,
                team_name=identity.team_name,
            )
        )
        db.commit()
        return LoginResponse(
            success=True,
            message="登录成功",
            data=identity.to_payload(),
            identity_cookie=encode_identity_cookie(identity),
        )

    return LoginResponse(success=False, message="用户未启用或用户名、密码不正确")


def get_identity_from_cookie(token: str | None, db: Session) -> Identity | None:
    """Decode `x-next-identity` cookie into Identity (shared with frontend)."""
    if not token:
        return None
    from api.services.identity import decode_identity_cookie

    return decode_identity_cookie(token)


class JwtExchangeRequest(BaseModel):
    token: str = Field(..., description="宿主系统签发的 HS256 JWT")


class JwtExchangeResponse(BaseModel):
    success: bool
    message: str
    data: dict | None = None
    identity_cookie: str | None = None


@router.post("/jwt-exchange", response_model=JwtExchangeResponse)
def jwt_exchange(req: JwtExchangeRequest, db: Session = Depends(get_db)) -> JwtExchangeResponse:
    """URL 带 ?jwt= 换签：验签宿主 JWT → 查库解析真实身份 → 签发 x-next-identity cookie。

    挂载场景（iframe / 无框架）免二次登录的正规通道：JWT 只在这里消费一次，
    换签成现有身份 cookie 后，所有接口走既有认证链路（middleware RBAC + 17 个
    router 的 _require_*），写操作按真实 user_id 正常授权，不做匿名放行。

    安全约束：
    - HS256 共享密钥（JWT_SECRET），未配置时端点直接拒绝；
    - exp 必须存在且未过期（JWT_MAX_AGE 兜底上限，防长期 token 流入日志）；
    - 可选 iss 白名单（JWT_ISSUER）校验签发方；
    - 换签后只认 resolve_active_user_identity 查库结果：用户不存在/停用 → 拒绝。
    """
    # 2026-10-29：验签复用 services.identity.verify_bearer_jwt（与中间件
    # Bearer 直调同一规则），按 reason 映射差异化响应文案（保持既有语义）。
    from api.services.identity import verify_bearer_jwt

    identity, reason = verify_bearer_jwt(req.token, db)
    if reason == "no_secret":
        return JwtExchangeResponse(success=False, message="服务端未启用 JWT 认证")
    if reason == "expired":
        return JwtExchangeResponse(success=False, message="登录凭证已过期，请重新进入")
    if reason == "invalid":
        return JwtExchangeResponse(success=False, message="登录凭证无效")
    if reason == "issuer":
        return JwtExchangeResponse(success=False, message="登录凭证签发方不匹配")
    if reason == "no_user_id":
        return JwtExchangeResponse(success=False, message="登录凭证缺少用户标识")
    if reason == "no_user":
        return JwtExchangeResponse(success=False, message="用户不存在或未启用")
    # reason == "" → identity 有效
    assert identity is not None
    return JwtExchangeResponse(
        success=True,
        message="登录成功",
        data=identity.to_payload(),
        identity_cookie=encode_identity_cookie(identity),
    )


@router.get("/auth-mode")
def auth_mode() -> dict:
    """Runtime auth configuration for the login page.

    Mirrors data-synth /api/open/auth-mode: lets the frontend decide between
    local login and SSO. Default ssoEnabled=false → plain local login; set
    SSO_* env vars to enable later without touching the frontend.
    """
    settings = get_settings()
    sso_enabled = bool(getattr(settings, "SSO_ENABLED", "") or "")
    return {
        "success": True,
        "data": {
            "authMode": "sso" if sso_enabled else "local",
            "ssoEnabled": sso_enabled,
            "appLogo": "",
            "defaultRedirectPath": "/kbs",
        },
    }


__all__ = ["router", "LoginRequest", "LoginResponse", "get_identity_from_cookie"]