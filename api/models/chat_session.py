"""Chat session & message models (conversation persistence).

Sessions belong to a user (user_id) and optionally bind a knowledge base
(kb_id). Messages carry role/content plus the retrieval refs (chunk ids +
scores) that grounded the answer, so the frontend can render citations.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kb_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # 非空 = agent 会话（绑定智能体）；此时 kb_id 可空（由 agent 的 KB 范围决定）
    agent_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256), default="新会话")
    pinned: Mapped[int] = mapped_column(Integer, default=0)  # 1 = 置顶
    # 非空 = 由某会话分叉而来（WeKnora fork 分支树）
    parent_session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # 2026-10-12 对齐 WeKnora last_request_state：会话级输入态快照（JSON 字符串），
    # 前端发送后写入、打开会话时恢复输入区（agent/kb/模型/联网开关等）
    last_request_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=_now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), default=_now_iso)


class ChatAttachment(Base):
    __tablename__ = "chat_attachment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    file_name: Mapped[str] = mapped_column(String(256), default="")
    file_ext: Mapped[str] = mapped_column(String(16), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    # text = docreader 解析出的 markdown（注入 system 上下文）
    # image = 图片附件（原始字节存 file_data，QA 时挂 user 消息 images）
    media_type: Mapped[str] = mapped_column(String(16), default="text")
    # 解析后的 markdown 全文（docreader 产出）
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 图片原始字节（media_type=image 时）
    # 图片上限 MAX_ATTACHMENT_BYTES=20MB > MySQL BLOB 64KB → LONGBLOB
    # （2026-10-15: 真实截图 >64KB 触 1406 Data too long → 500）
    file_data: Mapped[bytes | None] = mapped_column(
        LargeBinary(length=32 * 1024 * 1024), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String(32), default=_now_iso)


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_chat_message_session_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # JSON 字符串：引用 refs [{chunk_id, score, content?}]（assistant 消息）
    refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 思考过程全文（assistant 消息，LLM reasoning_content）
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 问答反馈（2026-10-13）：like / dislike / NULL=未反馈；feedback_at 记录时间
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)
    feedback_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # JSON 字符串：该条 user 消息携带的图片附件 id 数组（对齐 WeKnora 用户消息图片展示）
    images: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=_now_iso)


def new_session_id() -> str:
    return _new_id("sess")


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB


def new_message_id() -> str:
    return _new_id("msg")


def new_attachment_id() -> str:
    return _new_id("att")
