"""Chat trace query API — 智能问答过程追踪只读接口（分析用，2026-10-23）。

- GET /api/v1/chat/traces                列表（分页 + session/message/kind/kb/agent 筛选）
- GET /api/v1/chat/traces/{trace_id}     详情（thinking 全文 + events + hits）

数据来源：qa.py / agents.py 问答会话在 finally 落库的 chat_trace。
Requires the x-next-identity cookie。
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.chat_trace import get_trace, list_traces
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/chat", tags=["chat-traces"])

def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    ident = decode_identity_cookie(x_next_identity or "")
    if not ident or not ident.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return ident.user_id


@router.get("/traces")
def traces_list(
    session_id: str = Query("", description="按会话筛选"),
    message_id: str = Query("", description="按 assistant 消息筛选"),
    user_id: str = Query("", description="按用户筛选（需管理员或本用户）"),
    kb_id: str = Query("", description="按知识库筛选"),
    agent_id: str = Query("", description="按智能体筛选"),
    kind: str = Query("", description="quick / agent"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    caller: str = Depends(_require_user_id),
) -> dict:
    admin_hint = _is_admin(db, caller)
    if user_id and user_id != caller and not admin_hint:
        raise HTTPException(status_code=403, detail="无权查看其他用户的追踪记录")
    scope_user = "" if admin_hint else caller
    return list_traces(
        db,
        session_id=session_id,
        message_id=message_id,
        user_id=user_id or scope_user,
        kb_id=kb_id,
        agent_id=agent_id,
        kind=kind,
        page=page,
        page_size=page_size,
    )


@router.get("/traces/{trace_id}")
def traces_detail(
    trace_id: str,
    db: Session = Depends(get_db),
    caller: str = Depends(_require_user_id),
) -> dict:
    trace = get_trace(db, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="追踪记录不存在")
    if trace["user_id"] != caller and not _is_admin(db, caller):
        raise HTTPException(status_code=403, detail="无权查看该追踪记录")
    return trace


def _is_admin(db: Session, user_id: str) -> bool:
    try:
        from api.services.scope import is_admin

        return bool(is_admin(db, user_id))
    except Exception:
        return False