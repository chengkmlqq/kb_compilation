"""问答反馈（点赞/点踩）统计服务 —— 采集用户使用情况。

数据源：chat_message.feedback（like / dislike / NULL）+ feedback_at，
关联 chat_session（user_id / title）取用户与会话上下文；
问题文本取该 assistant 消息之前最近的 user 消息。

只读查询，不修改任何数据。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select
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

    2026-10-21 性能修复：总数/赞踩改 SQL 聚合（原实现把全部反馈行全列物化到
    Python 再计数，chat_message 累积后每次首页加载都拖全表）；今日/本周回看
    窗口仍需 Python 计算，只窄列取 feedback_at（不触碰 content 大列）。
    """
    conds: list = [
        ChatMessage.role == "assistant",
        ChatMessage.feedback.is_not(None),
    ]
    if user_id and user_id not in ("admin", "ROOT", ""):
        # 按用户隔离：session_id ∈ 该用户会话（子查询，避免 join 条件展开）
        conds.append(
            ChatMessage.session_id.in_(
                select(ChatSession.id).where(ChatSession.user_id == user_id)
            )
        )

    total_messages, like_count, dislike_count = (
        db.execute(
            select(
                func.count(ChatMessage.id),
                func.coalesce(
                    func.sum(case((ChatMessage.feedback == "like", 1), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((ChatMessage.feedback == "dislike", 1), else_=0)), 0
                ),
            ).where(*conds)
        ).one()
    )
    total_messages = int(total_messages or 0)
    like_count = int(like_count or 0)
    dislike_count = int(dislike_count or 0)

    now = datetime.now(timezone.utc)
    # 今日/本周：窄列取 feedback_at（避免全列物化 content 正文）
    ats = (
        db.execute(
            select(ChatMessage.feedback_at).where(*conds)
        )
        .scalars()
        .all()
    )
    today_count = sum(1 for ts in ats if _within_days(_parse_iso(ts), now, 1))
    week_count = sum(1 for ts in ats if _within_days(_parse_iso(ts), now, 7))

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

    2026-10-21 性能修复：每行的「前驱 user 问题」原实现逐行查一次
    （N+1，一页 20 行 = 21 条查询），改为相关子查询并入主 select 一次取回。
    """
    UserMsg = aliased(ChatMessage)
    # 该 assistant 消息之前最近的 user 消息内容（问题文本）——相关子查询
    prev_question = (
        select(UserMsg.content)
        .where(
            UserMsg.session_id == ChatMessage.session_id,
            UserMsg.role == "user",
            UserMsg.seq < ChatMessage.seq,
        )
        .order_by(UserMsg.seq.desc())
        .limit(1)
        .correlate(ChatMessage)
        .scalar_subquery()
    )
    stmt = (
        select(ChatMessage, ChatSession, prev_question)
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
    for msg, sess, question in rows:
        items.append(
            {
                "id": msg.id,
                "session_id": msg.session_id,
                "session_title": sess.title or "",
                "user_id": sess.user_id or "",
                "feedback": msg.feedback,
                "feedback_at": msg.feedback_at,
                "question": question or "",
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
