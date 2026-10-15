"""Chat session services — conversation CRUD + message persistence + LLM
enhancements (title generation, recommended questions, follow-up suggestions).

Sessions/messages live on the framework store (Base). The QA stream itself
stays in api.services.chat; this module owns the conversation lifecycle that
wraps it: create session -> ask (persists user+assistant) -> list/load ->
title/suggestions via ChatClient.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from api.models.chat_session import (
    ChatAttachment,
    ChatMessage,
    ChatSession,
    new_attachment_id,
    new_message_id,
    new_session_id,
)
from api.models.knowledge import KbDatasource

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_SESSION = 500


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _session_dict(s: ChatSession) -> dict:
    state: dict | None = None
    if s.last_request_state:
        try:
            state = json.loads(s.last_request_state)
        except (TypeError, json.JSONDecodeError):
            state = None
    return {
        "id": s.id,
        "kb_id": s.kb_id,
        "agent_id": s.agent_id,
        "title": s.title,
        "pinned": bool(s.pinned),
        # 2026-10-12 对齐 WeKnora last_request_state：会话输入态快照（前端恢复用）
        "last_request_state": state,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def create_session(
    db: Session,
    user_id: str,
    kb_id: str | None,
    title: str | None = None,
    last_request_state: dict | None = None,
    agent_id: str | None = None,
) -> dict:
    session = ChatSession(
        id=new_session_id(),
        user_id=user_id,
        kb_id=kb_id or None,
        agent_id=agent_id or None,
        title=(title or "").strip() or "新会话",
        last_request_state=json.dumps(last_request_state, ensure_ascii=False) if last_request_state else None,
    )
    db.add(session)
    db.commit()
    return _session_dict(session)


def list_sessions(db: Session, user_id: str, page: int = 1, page_size: int = 50) -> dict:
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.pinned.desc(), ChatSession.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.execute(stmt).scalars().all()
    total = db.execute(
        select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)
    ).scalar() or 0
    return {"items": [_session_dict(s) for s in rows], "total": total}


def get_session(db: Session, user_id: str, session_id: str) -> ChatSession:
    s = db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    ).scalars().first()
    if not s:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return s


def update_session(
    db: Session,
    user_id: str,
    session_id: str,
    title: str | None = None,
    kb_id: str | None = None,
    pinned: bool | None = None,
    last_request_state: dict | None = None,
) -> dict:
    s = get_session(db, user_id, session_id)
    if title is not None:
        s.title = title.strip() or "新会话"
    if kb_id is not None:
        s.kb_id = kb_id or None
    if pinned is not None:
        s.pinned = 1 if pinned else 0
    if last_request_state is not None:
        # 2026-10-12 会话输入态快照（对齐 WeKnora last_request_state）
        s.last_request_state = json.dumps(last_request_state, ensure_ascii=False)
    s.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    db.commit()
    return _session_dict(s)


def delete_session(db: Session, user_id: str, session_id: str) -> None:
    s = get_session(db, user_id, session_id)
    db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    db.delete(s)
    db.commit()


def batch_delete_sessions(db: Session, user_id: str, session_ids: list[str]) -> int:
    if not session_ids:
        return 0
    db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
    result = db.execute(
        delete(ChatSession).where(
            ChatSession.id.in_(session_ids), ChatSession.user_id == user_id
        )
    )
    db.commit()
    return result.rowcount or 0


def clear_session_messages(db: Session, user_id: str, session_id: str) -> None:
    get_session(db, user_id, session_id)
    db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    s = db.execute(select(ChatSession).where(ChatSession.id == session_id)).scalars().first()
    if s:
        s.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
        db.commit()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def _message_dict(db: Session, m: ChatMessage) -> dict:
    refs = []
    if m.refs:
        try:
            refs = json.loads(m.refs)
        except (TypeError, json.JSONDecodeError):
            refs = []
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "refs": refs,
        "thinking": m.thinking or None,
        "feedback": m.feedback or None,
        "feedback_at": m.feedback_at or None,
        "images": _message_images(db, m),
        "created_at": m.created_at,
    }


def _message_images(db: Session, m: ChatMessage) -> list[dict]:
    """该条消息携带的图片附件（对齐 WeKnora 用户消息缩略图展示）。

    小图（<=300KB）内联 data URL 供直接渲染；大图只给元信息（文件名/大小）。
    """
    if not m.images:
        return []
    try:
        ids = json.loads(m.images) or []
    except (TypeError, json.JSONDecodeError):
        return []
    if not ids:
        return []
    from sqlalchemy import select
    import base64

    rows = (
        db.execute(select(ChatAttachment).where(ChatAttachment.id.in_(ids)))
        .scalars()
        .all()
    )
    by_id = {a.id: a for a in rows}

    out = []
    for aid in ids:
        att = by_id.get(aid)
        if not att or att.media_type != "image":
            continue
        item: dict = {"id": att.id, "file_name": att.file_name or "图片", "file_size": att.file_size or 0}
        if att.file_data and (att.file_size or 0) <= 300 * 1024:
            item["url"] = "data:image/jpeg;base64," + base64.b64encode(att.file_data).decode()
        out.append(item)
    return out


def load_messages(db: Session, user_id: str, session_id: str) -> list[dict]:
    get_session(db, user_id, session_id)
    rows = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.asc())
        )
        .scalars()
        .all()
    )
    return [_message_dict(db, m) for m in rows]


def delete_message(db: Session, user_id: str, session_id: str, message_id: str) -> None:
    get_session(db, user_id, session_id)
    m = db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id, ChatMessage.session_id == session_id
        )
    ).scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
    db.delete(m)
    db.commit()


def set_message_feedback(
    db: Session, user_id: str, session_id: str, message_id: str, feedback_type: str | None
) -> dict:
    """设置/清除单条消息的问答反馈（like / dislike，None=清除）。

    同会话同消息只保留一条反馈；切换或清除直接覆盖。返回更新后的消息字典。
    """
    get_session(db, user_id, session_id)
    m = db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id, ChatMessage.session_id == session_id
        )
    ).scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
    if m.role != "assistant":
        raise HTTPException(status_code=400, detail="仅可对 AI 回复进行反馈")
    if feedback_type in ("like", "dislike"):
        m.feedback = feedback_type
        m.feedback_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        m.feedback = None
        m.feedback_at = None
    db.commit()
    db.refresh(m)
    return _message_dict(db, m)


def append_message(
    db: Session,
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    refs: list[dict] | None = None,
    thinking: str | None = None,
    images: list[str] | None = None,
) -> dict:
    get_session(db, user_id, session_id)
    max_seq = db.execute(
        select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)
    ).scalar() or 0
    msg = ChatMessage(
        id=new_message_id(),
        session_id=session_id,
        seq=int(max_seq) + 1,
        role=role,
        content=content or "",
        refs=json.dumps(refs or [], ensure_ascii=False) if refs else None,
        thinking=thinking or None,
        images=json.dumps(images or [], ensure_ascii=False) if images else None,
    )
    db.add(msg)
    s = db.execute(select(ChatSession).where(ChatSession.id == session_id)).scalars().first()
    if s:
        s.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
        s.title = s.title if s.title and s.title != "新会话" else (content[:30] or "新会话")
    db.commit()
    return _message_dict(db, msg)


def get_context_messages(db: Session, session_id: str, limit: int = 20) -> list[dict]:
    """最近 N 条消息作为多轮对话上下文（用户+助手交替，按时间序）。"""
    rows = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_message_dict(db, m) for m in reversed(rows)]


# ---------------------------------------------------------------------------
# LLM 增强：标题 / 推荐问题 / 追问建议
# ---------------------------------------------------------------------------


def _llm_client(
    db: Session,
    *,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
):
    from api.services.chat import ChatClient, load_chat_config

    cfg = load_chat_config(
        db,
        user_id=user_id or "",
        team_name=team_name or "",
        is_sys_admin=is_sys_admin,
    )
    if not cfg.base_url:
        raise HTTPException(status_code=400, detail="未配置问答模型（AI_CHAT_API_ENDPOINT）")
    return ChatClient(cfg)


def _llm_chat(
    db: Session,
    prompt: str,
    max_tokens: int,
    temperature: float = 0.7,
    retries: int = 3,
    min_len: int = 1,
    *,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
) -> str:
    """ChatClient.chat with empty/short-response retry.

    inferai occasionally returns a 200 stream with zero content chunks or a
    truncated echo; retries make the LLM-backed helpers (title / suggestions)
    reliable without changing the shared ChatClient.
    """
    from api.services.chat import ChatMessage

    client = _llm_client(db, user_id=user_id, team_name=team_name, is_sys_admin=is_sys_admin)
    last = ""
    for attempt in range(retries):
        try:
            last = client.chat(
                [
                    ChatMessage(
                        role="system",
                        content="你是知识库平台的助手。严格只输出最终结果本身，不要输出任何操作指令、任务描述或说明。",
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            ).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("llm_chat attempt %d failed: %s", attempt + 1, e)
            last = ""
        if len(last) >= min_len:
            return last
    return last


def generate_title(
    db: Session, user_id: str, session_id: str, *, model_ctx: dict | None = None
) -> str:
    """用 LLM 从首条用户消息生成会话标题（≤20 字）。model_ctx 携带
    {user_id, team_name, is_admin} 以解析个人/团队/系统模型。"""
    get_session(db, user_id, session_id)
    msgs = load_messages(db, user_id, session_id)
    first_user = next((m for m in msgs if m["role"] == "user"), None)
    if not first_user:
        return "新会话"
    prompt = (
        "为以下对话生成一个简短的中文会话标题，不超过 15 个字，不要引号，不要标点结尾，直接输出标题。\n"
        f"用户消息：{first_user['content'][:100]}"
    )
    ctx = model_ctx or {}
    title = _llm_chat(
        db,
        prompt,
        max_tokens=128,
        temperature=0.3,
        min_len=3,
        user_id=ctx.get("user_id", ""),
        team_name=ctx.get("team_name", ""),
        is_sys_admin=ctx.get("is_admin", False),
    )
    title = "".join(title.split())[:20]
    # 防 prompt 泄漏：LLM 偶发把任务指令当标题输出（如「我们被要求…」「不超过…」），
    # 命中指令特征即回退用户消息截断
    _NOISE = ("被要求", "不超过", "直接输出", "不要引号", "不要标点", "每行一个", "字数", "生成简短")
    if any(n in title for n in _NOISE):
        title = ""
    if not title:
        title = (first_user["content"] or "").strip()[:20] or "新会话"
    update_session(db, user_id, session_id, title=title)
    return title


def recommended_questions(
    db: Session, kb_id: str | None, count: int = 3, *, model_ctx: dict | None = None
) -> list[str]:
    """新会话推荐问题：优先从知识库 wiki 页面标题/内容生成，无库则通用。"""
    kb_titles: list[str] = []
    if kb_id:
        kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
        if kb:
            try:
                from sqlalchemy import text as sa_text

                rows = db.execute(
                    sa_text(
                        "SELECT title FROM wiki_page WHERE kb_id = :kb_id AND state = '1' "
                        "ORDER BY page_type, created_at LIMIT 8"
                    ),
                    {"kb_id": kb_id},
                ).all()
                kb_titles = [r[0] for r in rows if r[0]]
            except Exception:  # noqa: BLE001
                kb_titles = []
    if kb_titles:
        prompt = (
            "基于以下知识库的文档主题，生成 3 个用户可能关心的中文问题，每行一个，"
            "不要编号，直接输出问题。\n主题：" + "、".join(kb_titles[:8])
        )
    else:
        prompt = "给出 3 个通用的知识库问答开场问题，每行一个，不要编号，直接输出问题。"
    ctx = model_ctx or {}
    text = _llm_chat(
        db,
        prompt,
        max_tokens=200,
        user_id=ctx.get("user_id", ""),
        team_name=ctx.get("team_name", ""),
        is_sys_admin=ctx.get("is_admin", False),
    )
    lines = [l.strip(" -·•") for l in text.splitlines() if l.strip()]
    # 防 prompt 泄漏：LLM 偶发把任务指令行也输出（如「我们被要求…」「每行一个…」），剔除
    _NOISE = ("被要求", "每行一个", "不要编号", "直接输出", "确保问题", "开场问题", "输出问题", "适合知识库问答")
    clean = [l for l in lines if not any(n in l for n in _NOISE)]
    return clean[:count] or ["这个知识库有哪些文档？", "文档的主要内容是什么？"]


def follow_up_suggestions(
    db: Session,
    user_id: str,
    session_id: str,
    last_answer: str = "",
    count: int = 3,
    *,
    model_ctx: dict | None = None,
) -> list[str]:
    """根据最近一条回答生成追问建议（FollowUp questions）。"""
    get_session(db, user_id, session_id)
    if not last_answer:
        return []
    prompt = (
        "根据以下 AI 回答内容，生成 3 个用户可能会追问的问题，每行一个，不要编号，"
        "直接输出问题。严禁输出任务指令、说明或「回答内容」原文。回答内容：\n"
        + last_answer[:500]
    )
    ctx = model_ctx or {}
    text = _llm_chat(
        db,
        prompt,
        max_tokens=200,
        user_id=ctx.get("user_id", ""),
        team_name=ctx.get("team_name", ""),
        is_sys_admin=ctx.get("is_admin", False),
    )
    lines = [l.strip(" -·•") for l in text.splitlines() if l.strip()]
    # 防 prompt 泄漏：剔除指令特征行；混合行（prompt+真问题拼接）按「用户可能追问」切分取真问题
    _NOISE = ("我们需要", "我们被要求", "要求：", "每行一个", "不要编号", "直接输出",
              "回答内容", "基于这个简短的回答", "输出问题", "可能会追问的问题", "可能追问的问题", "生成 3 个")
    clean: list[str] = []
    for l in lines:
        if any(n in l for n in _NOISE):
            if "用户可能追问" in l:
                tail = l.split("用户可能追问", 1)[1].lstrip("：: ")
                for part in re.split(r"[？?。；;\n]", tail):
                    part = part.strip().strip("，, ")
                    if part and len(part) > 1:
                        clean.append(part if part.endswith("？") else part + "？")
            continue
        clean.append(l)
    return clean[:count]


# ---------------------------------------------------------------------------
# 附件（临时文档问答 / 图片问答）
# ---------------------------------------------------------------------------

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def is_image_ext(ext: str) -> bool:
    return (ext or "").lower() in IMAGE_EXTS


def _attachment_dict(a: ChatAttachment) -> dict:
    return {
        "id": a.id,
        "session_id": a.session_id,
        "file_name": a.file_name,
        "file_ext": a.file_ext,
        "file_size": a.file_size,
        "media_type": a.media_type or "text",
        "created_at": a.created_at,
    }


def create_attachment(
    db: Session,
    user_id: str,
    session_id: str,
    file_name: str,
    file_ext: str,
    file_size: int,
    content: str,
    media_type: str = "text",
    file_data: bytes | None = None,
) -> dict:
    get_session(db, user_id, session_id)
    att = ChatAttachment(
        id=new_attachment_id(),
        session_id=session_id,
        user_id=user_id,
        file_name=file_name,
        file_ext=file_ext,
        file_size=file_size,
        media_type=media_type,
        content=content,
        file_data=file_data,
    )
    db.add(att)
    db.commit()
    return _attachment_dict(att)


def list_attachments(db: Session, user_id: str, session_id: str) -> list[dict]:
    get_session(db, user_id, session_id)
    rows = (
        db.execute(
            select(ChatAttachment)
            .where(ChatAttachment.session_id == session_id)
            .order_by(ChatAttachment.created_at.asc())
        )
        .scalars()
        .all()
    )
    return [_attachment_dict(a) for a in rows]


def delete_attachment(db: Session, user_id: str, session_id: str, attachment_id: str) -> None:
    get_session(db, user_id, session_id)
    a = db.execute(
        select(ChatAttachment).where(
            ChatAttachment.id == attachment_id, ChatAttachment.session_id == session_id
        )
    ).scalars().first()
    if not a:
        raise HTTPException(status_code=404, detail=f"附件不存在: {attachment_id}")
    db.delete(a)
    db.commit()


def attachment_contents(db: Session, session_id: str, max_chars: int = 6000) -> str:
    """汇总会话全部附件的 markdown 文本（LLM 上下文注入）。"""
    rows = (
        db.execute(
            select(ChatAttachment)
            .where(ChatAttachment.session_id == session_id)
            .order_by(ChatAttachment.created_at.asc())
        )
        .scalars()
        .all()
    )
    parts = []
    budget = max_chars
    for a in rows:
        if a.media_type == "image":
            continue  # 图片不注入文本上下文，走 attachment_images
        content = (a.content or "").strip()
        if not content:
            continue
        snippet = content[:budget]
        parts.append(f"【附件:{a.file_name}】\n{snippet}")
        budget -= len(snippet)
        if budget <= 0:
            break
    return "\n\n".join(parts)


def attachment_image_ids(db: Session, session_id: str) -> list[str]:
    """会话全部图片附件 id（按上传顺序），用于 user 消息 images 持久化。"""
    rows = (
        db.execute(
            select(ChatAttachment.id)
            .where(
                ChatAttachment.session_id == session_id,
                ChatAttachment.media_type == "image",
            )
            .order_by(ChatAttachment.created_at.asc())
        )
        .scalars()
        .all()
    )
    return [str(r) for r in rows]


def attachment_images(db: Session, session_id: str) -> list[str]:
    """图片附件转 data URL 列表（QA 时挂 user 消息 images，按上传顺序）。

    与 WeKnora 「图片仅随当轮请求注入」的差异：kb 附件是会话级，图片
    每轮随当前 user 消息重发，删除附件后不再携带。历史消息不含图片。
    """
    rows = (
        db.execute(
            select(ChatAttachment)
            .where(
                ChatAttachment.session_id == session_id,
                ChatAttachment.media_type == "image",
            )
            .order_by(ChatAttachment.created_at.asc())
        )
        .scalars()
        .all()
    )
    out = []
    for a in rows:
        if not a.file_data:
            continue
        mime = IMAGE_MIME.get((a.file_ext or "").lower(), "image/png")
        import base64

        b64 = base64.b64encode(a.file_data).decode("ascii")
        out.append(f"data:{mime};base64,{b64}")
    return out


# ---------------------------------------------------------------------------
# 消息搜索（跨会话）
# ---------------------------------------------------------------------------


def search_messages(db: Session, user_id: str, keyword: str, limit: int = 20) -> list[dict]:
    """跨会话关键词搜索消息（keyword 子串匹配，SQLite/MySQL/PG 通用）。"""
    from sqlalchemy import or_

    if not keyword or not keyword.strip():
        return []
    kw = keyword.strip()
    rows = (
        db.execute(
            select(ChatMessage)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(
                ChatSession.user_id == user_id,
                or_(
                    ChatMessage.content.ilike(f"%{kw}%"),
                    ChatSession.title.ilike(f"%{kw}%"),
                ),
            )
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    out = []
    for m in rows:
        item = _message_dict(db, m)
        item["session_id"] = m.session_id
        item["session_title"] = ""
        # 附带会话标题
        s = db.execute(select(ChatSession).where(ChatSession.id == m.session_id)).scalars().first()
        if s:
            item["session_title"] = s.title
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Agent 会话
# ---------------------------------------------------------------------------


def create_agent_session(
    db: Session,
    user_id: str,
    agent_id: str,
    kb_id: str | None = None,
    title: str | None = None,
    last_request_state: dict | None = None,
) -> dict:
    session = ChatSession(
        id=new_session_id(),
        user_id=user_id,
        kb_id=kb_id or None,
        agent_id=agent_id,
        title=(title or "").strip() or "新会话",
        last_request_state=json.dumps(last_request_state, ensure_ascii=False) if last_request_state else None,
    )
    db.add(session)
    db.commit()
    return _session_dict(session)


def fork_session(
    db: Session,
    user_id: str,
    session_id: str,
    message_id: str | None = None,
    title: str | None = None,
) -> dict:
    """Fork a session at a message boundary (WeKnora-style branching).

    Copies the parent session and keeps messages up to `message_id`
    (inclusive); the new session records parent_session_id so the UI can
    render a branch tree. Default fork point = last message.
    """
    src = get_session(db, user_id, session_id)
    msgs = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq)
        )
        .scalars()
        .all()
    )
    cut_seq = None
    if message_id:
        hit = next((m for m in msgs if m.id == message_id), None)
        if not hit:
            raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
        cut_seq = hit.seq
    new_id = new_session_id()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    db.add(
        ChatSession(
            id=new_id,
            user_id=user_id,
            kb_id=src.kb_id,
            agent_id=src.agent_id,
            title=(title or src.title) if msgs else (title or "新会话"),
            pinned=0,
            parent_session_id=session_id,
            created_at=now,
            updated_at=now,
        )
    )
    copied = 0
    for m in msgs:
        if cut_seq is not None and m.seq > cut_seq:
            break
        db.add(
            ChatMessage(
                id=new_message_id(),
                session_id=new_id,
                seq=m.seq,
                role=m.role,
                content=m.content,
                refs=m.refs,
                thinking=m.thinking,
                created_at=m.created_at,
            )
        )
        copied += 1
    db.commit()
    return {
        "id": new_id,
        "title": (title or src.title) if msgs else (title or "新会话"),
        "kb_id": src.kb_id,
        "agent_id": src.agent_id,
        "parent_session_id": session_id,
        "message_count": copied,
        "created_at": now,
        "updated_at": now,
    }


def rewind_session(db: Session, user_id: str, session_id: str, message_id: str) -> dict:
    """Truncate a session at `message_id`: messages after it are removed.

    The anchor message itself is kept (WeKnora rewindToMessage semantics) —
    the client then sends a new question from that point.
    """
    s = get_session(db, user_id, session_id)
    msgs = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq)
        )
        .scalars()
        .all()
    )
    hit = next((m for m in msgs if m.id == message_id), None)
    if not hit:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
    removed = (
        db.execute(
            delete(ChatMessage).where(
                ChatMessage.session_id == session_id, ChatMessage.seq > hit.seq
            )
        ).rowcount or 0
    )
    s.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    db.commit()
    return {"session_id": session_id, "removed": removed, "remaining": hit.seq + 1}
