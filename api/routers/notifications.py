"""System message (notification) API — unread count / list / mark read.

Backed by modo_system_message (framework table, shared with the source
platform). Mirrors data-synth notification-actions:
- getMySystemMessageUnreadCountAction
- getMySystemMessagesAction
- markSystemMessageReadAction / markAllSystemMessagesReadAction
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.framework import SystemMessage
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/system/notifications", tags=["notifications"])

# MySQL 容器时区为 UTC，业务展示用 CST（+8h）
_CST = timezone(timedelta(hours=8))


def _fmt(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _user_id(x_next_identity: str | None) -> str | None:
    identity = decode_identity_cookie(x_next_identity or "")
    return identity.user_id if identity else None


def _scope(uid: str):
    """My-message scope: own messages or messages targeting all teams/users
    (source builds a similar where: userId = me OR linkUrl/sender for global)."""
    return or_(
        SystemMessage.user_id == uid,
        SystemMessage.user_id == "ALL",
        SystemMessage.user_id == "",
    )


@router.get("/unread-count")
def unread_count(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    cnt = db.execute(
        select(func.count())
        .select_from(SystemMessage)
        .where(and_(SystemMessage.status == "ACTIVE", SystemMessage.is_read == "0", _scope(uid)))
    ).scalar() or 0
    return {"success": True, "data": {"unread_count": int(cnt)}}


class MessageListParams(BaseModel):
    tab: str = "unread"  # unread | all
    page: int = 1
    page_size: int = 10


@router.get("")
def list_messages(
    tab: str = "unread",
    page: int = 1,
    page_size: int = 10,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    conds = [SystemMessage.status == "ACTIVE", _scope(uid)]
    if tab == "unread":
        conds.append(SystemMessage.is_read == "0")
    total = db.execute(
        select(func.count()).select_from(SystemMessage).where(and_(*conds))
    ).scalar() or 0
    rows = db.execute(
        select(SystemMessage)
        .where(and_(*conds))
        .order_by(desc(SystemMessage.create_date))
        .offset((max(page, 1) - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    items = [
        {
            "id": m.id,
            "title": m.title,
            "content": m.content,
            "type": m.type,
            "is_read": m.is_read,
            "priority": m.priority,
            "link_url": m.link_url,
            "sender_id": m.sender_id,
            "create_date": _fmt(m.create_date),
        }
        for m in rows
    ]
    return {"success": True, "data": {"items": items, "total": int(total)}}


class ReadRequest(BaseModel):
    id: str


@router.post("/{message_id}/read")
def mark_read(
    message_id: str,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    db.execute(
        update(SystemMessage)
        .where(and_(SystemMessage.id == message_id, _scope(uid)))
        .values(is_read="1", read_date=datetime.now())
    )
    db.commit()
    return {"success": True, "message": "已标记已读"}


@router.post("/read-all")
def mark_all_read(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    db.execute(
        update(SystemMessage)
        .where(and_(SystemMessage.is_read == "0", _scope(uid)))
        .values(is_read="1", read_date=datetime.now())
    )
    db.commit()
    return {"success": True, "message": "全部已读"}


__all__ = ["router"]
