"""System message (notification) API — unread count / list / mark read.

Backed by modo_system_message (framework table, shared with the source
platform). Mirrors data-synth notification-actions:
- getMySystemMessageUnreadCountAction
- getMySystemMessagesAction
- markSystemMessageReadAction / markAllSystemMessagesReadAction
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.framework import SystemMessage, User
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
    result = db.execute(
        update(SystemMessage)
        .where(and_(SystemMessage.id == message_id, _scope(uid)))
        .values(is_read="1", read_date=datetime.now())
    )
    db.commit()
    if result.rowcount == 0:
        return {"success": False, "message": "消息不存在或无权限", "data": None}
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


# ============================================================================
# 管理端（对齐 ds notification-actions 的发送/目标用户/短信告警测试）
# ============================================================================


class SendMessageRequest(BaseModel):
    """发送系统消息（对齐 ds rawSendSystemMessageAction）。

    userIds 为空 → 发给全部启用用户（安全上限 1000，对齐 ds）。
    """
    userIds: list[str] | None = None
    title: str
    content: str
    type: str = "INFO"  # INFO/WARNING/ERROR/SUCCESS
    priority: str = "NORMAL"  # LOW/NORMAL/HIGH
    linkUrl: str | None = None


class TestAlertApiRequest(BaseModel):
    """短信告警接口测试（对齐 ds rawTestExternalAlertApiAction）。"""
    endpoint: str
    method: str = "POST"  # POST/PUT/PATCH
    id: str | None = None
    alertLevel: str | None = None
    alertTitle: str = ""
    alertContent: str = ""
    alertTime: str | None = None


@router.post("/send")
def send_message(
    req: SendMessageRequest,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}

    target_ids: list[str] = []
    if req.userIds and len(req.userIds) > 0:
        target_ids = [u for u in req.userIds if u]
    else:
        rows = db.execute(
            select(User).where(and_(User.state == "1", User.user_id.isnot(None))).limit(1000)
        ).scalars().all()
        target_ids = [u.user_id for u in rows if u.user_id]
    if not target_ids:
        return {"success": False, "message": "没有可发送的用户", "data": None}

    import uuid
    now = datetime.now()
    for uid_to in target_ids:
        db.add(SystemMessage(
            id=uuid.uuid4().hex[:32],
            user_id=uid_to,
            title=req.title,
            content=req.content,
            type=req.type,
            is_read="0",
            priority=req.priority,
            status="ACTIVE",
            sender_id=uid,
            link_url=req.linkUrl,
            create_date=now,
        ))
    db.commit()
    return {"success": True, "message": f"已发送给 {len(target_ids)} 个用户", "data": {"sent": len(target_ids)}}


@router.get("/users")
def list_notification_users(
    keyWord: str = "",
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """通知目标用户列表（对齐 ds rawGetUserListForNotificationAction：仅启用用户）。"""
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}
    conds = [User.state == "1"]
    kw = keyWord.strip()
    if kw:
        conds.append(or_(User.user_id.like(f"%{kw}%"), User.user_name.like(f"%{kw}%"), User.email.like(f"%{kw}%")))
    rows = db.execute(
        select(User).where(and_(*conds)).limit(100)
    ).scalars().all()
    items = [
        {"user_id": u.user_id, "user_name": u.user_name, "email": u.email}
        for u in rows if u.user_id
    ]
    return {"success": True, "data": {"items": items, "total": len(items)}}


@router.post("/test-alert-api")
def test_alert_api(
    req: TestAlertApiRequest,
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """向对端接口发一条测试告警（对齐 ds SmsAlertApiTester）。"""
    uid = _user_id(x_next_identity)
    if not uid:
        return {"success": False, "message": "未登录", "data": None}

    endpoint = (req.endpoint or "").strip()
    if not endpoint:
        return {"success": False, "message": "请输入对端接口地址", "data": None}
    if not endpoint.startswith(("http://", "https://")):
        return {"success": False, "message": "仅支持 http/https 接口地址", "data": None}
    method = req.method.upper()
    if method not in ("POST", "PUT", "PATCH"):
        return {"success": False, "message": "当前仅支持 POST、PUT、PATCH 请求方式", "data": None}

    payload = {
        "id": req.id,
        "alertLevel": req.alertLevel,
        "alertTitle": req.alertTitle,
        "alertContent": req.alertContent,
        "alertTime": req.alertTime,
    }
    import httpx
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.request(method, endpoint, json=payload)
        return {
            "success": True,
            "message": f"HTTP {resp.status_code}",
            "data": {"status_code": resp.status_code, "body": resp.text[:500]},
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"请求失败: {e}", "data": None}


__all__ = ["router"]
