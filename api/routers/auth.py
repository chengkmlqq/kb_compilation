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
    data: Identity | None = None
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
            data=identity,
            identity_cookie=encode_identity_cookie(identity),
        )

    return LoginResponse(success=False, message="用户未启用或用户名、密码不正确")


def get_identity_from_cookie(token: str | None, db: Session) -> Identity | None:
    """Decode `x-next-identity` cookie into Identity (shared with frontend)."""
    if not token:
        return None
    from api.services.identity import decode_identity_cookie

    return decode_identity_cookie(token)


__all__ = ["router", "LoginRequest", "LoginResponse", "get_identity_from_cookie"]