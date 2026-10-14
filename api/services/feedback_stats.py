"""问答反馈（点赞/点踩）统计服务 —— 采集用户使用情况。

数据源：chat_message.feedback（like / dislike / NULL）+ feedback_at，
关联 chat_session（user_id / title）取用户与会话上下文；
问题文本取该 assistant 消息之前最近的 user 消息。

只读查询，不修改任何数据。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from api.models.chat_session import ChatMessage, ChatSession


def _parse_iso(ts: str | None) -> datetime | None:
    """chat 的时间是 ISO 字符串（UTC），解析失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def feedback_stats(db: Session, user_id: str = "") -> dict:
    """反馈汇总：总问答(assistant)消息数、已反馈数、点赞/点踩数、点赞率。

    user_id 非 admin/ROOT 时仅统计该用户自己的反馈（对齐数据查询团队授权口径，
    预留按用户隔离；admin 看全量）。
    """
    stmt = select(ChatMessage).where(
        ChatMessage.role == "assistant",
        ChatMessage.feedback.is_not(None),
    )
    if user_id and user_id not in ("admin", "ROOT", ""):
        stmt = stmt.join(ChatSession, ChatSession.id == ChatMessage.session_id).where(
            ChatSession.user_id == user_id
        )
    rows = db.execute(stmt).scalars().all()

    total_messages = len(rows)
    like_count = sum(1 for m in rows if m.feedback == "like")
    dislike_count = sum(1 for m in rows if m.feedback == "dislike")
    now = datetime.now(timezone.utc)
    today_count = sum(
        1 for m in rows if _within_days(_parse_iso(m.feedback_at), now, 1)
    )
    week_count = sum(1 for m in rows if _within_days(_parse_iso(m.feedback_at), now, 7))

    return {
        "success": True,
        "data": {
            "total_feedback": total_messages,
            "like_count": like_count,
            "dislike_count": dislike_count,
            "like_rate": round(like_count / total_messages, 4) if total_messages else 0,
            "today_count": today_count,
            "week_count": week_count,
            "scoped": bool(user_id and user_id not in ("admin", "ROOT", "")),
        },
    }


def _within_days(ts: datetime | None, now: datetime, days: int) -> bool:
    if not ts:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() <= days * 86400


def list_feedback(
    db: Session,
    page: int,
    page_size: int,
    feedback_type: str | None = None,
    keyword: str = "",
    user_id: str = "",
) -> dict:
    """反馈明细分页：回答内容 + 问题 + 用户 + 会话标题 + 反馈类型/时间。

    keyword 模糊匹配 问题/回答 文本；feedback_type 取值 like / dislike / None=全部。
    """
    stmt = (
        select(ChatMessage, ChatSession)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.role == "assistant",
            ChatMessage.feedback.is_not(None),
        )
    )
    if feedback_type in ("like", "dislike"):
        stmt = stmt.where(ChatMessage.feedback == feedback_type)
    if user_id and user_id not in ("admin", "ROOT", ""):
        stmt = stmt.where(ChatSession.user_id == user_id)
    if keyword:
        like = f"%{keyword}%"
        # 匹配回答文本 / 会话标题 / 用户；
        # 以及「该 assistant 消息之前最近的 user 消息(问题)」含关键字（精确对应，非整会话）
        UserMsg = aliased(ChatMessage)
        prev_user_seq = (
            select(func.max(UserMsg.seq))
            .where(
                UserMsg.session_id == ChatMessage.session_id,
                UserMsg.role == "user",
                UserMsg.seq < ChatMessage.seq,
            )
            .correlate(ChatMessage)
            .scalar_subquery()
        )
        question_match = (
            select(UserMsg.id)
            .where(
                UserMsg.session_id == ChatMessage.session_id,
                UserMsg.role == "user",
                UserMsg.seq == prev_user_seq,
                UserMsg.content.like(like),
            )
            .correlate(ChatMessage, UserMsg)
            .exists()
        )
        stmt = stmt.where(
            ChatMessage.content.like(like)
            | ChatSession.title.like(like)
            | ChatSession.user_id.like(like)
            | question_match
        )

    # 窄列计数（消息 content 是 Text，避免全列载入）
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar() or 0

    rows = (
        db.execute(
            stmt.order_by(ChatMessage.feedback_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .all()
    )

    items: list[dict] = []
    for msg, sess in rows:
        question = _prev_user_question(db, msg)
        items.append(
            {
                "id": msg.id,
                "session_id": msg.session_id,
                "session_title": sess.title or "",
                "user_id": sess.user_id or "",
                "feedback": msg.feedback,
                "feedback_at": msg.feedback_at,
                "question": question,
                "answer": msg.content,
            }
        )

    return {
        "success": True,
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


def _prev_user_question(db: Session, msg: ChatMessage) -> str:
    """该 assistant 消息之前最近的 user 消息内容（问题文本）。"""
    row = db.execute(
        select(ChatMessage.content)
        .where(
            ChatMessage.session_id == msg.session_id,
            ChatMessage.role == "user",
            ChatMessage.seq < msg.seq,
        )
        .order_by(ChatMessage.seq.desc())
        .limit(1)
    ).scalar()
    return row or ""
