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
    # 图片原始字节（media_type=image 时；对象存储不可用时回落）
    # 图片上限 MAX_ATTACHMENT_BYTES=20MB > MySQL BLOB 64KB → LONGBLOB
    # （2026-10-15: 真实截图 >64KB 触 1406 Data too long → 500）
    file_data: Mapped[bytes | None] = mapped_column(
        LargeBinary(length=32 * 1024 * 1024), nullable=True
    )
    # 图片对象存储路径（minio://bucket/chat_images/<session>/<att>.<ext>，对齐
    # WeKnora 图片落存储；为空表示字节存在 file_data 列）
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 图片视觉描述（非视觉模型的文本回退；对齐 WeKnora message.Images[].Caption，
    # 历史轮次按此回带「用户上传图片内容」）
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # JSON 字符串：该条 user 消息携带的全部附件 id 数组（图片 + 文档），
    # 供前端在用户消息侧展示附件入口（2026-10-20 附件问答交互改造）
    attachment_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=_now_iso)


def new_session_id() -> str:
    return _new_id("sess")


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB


def new_message_id() -> str:
    return _new_id("msg")


def new_attachment_id() -> str:
    return _new_id("att")


class ChatTrace(Base):
    """智能问答过程追踪（2026-10-23 分析需求）：按 assistant 消息一条。

    记录 quick/agent 两条问答链路的思考过程全文（thinking）、时序事件明细
    （events：stage/context/tool_call/tool_result/done，含相对耗时毫秒）、
    检索命中明细（hits）、回合汇总（summary），供后续分析知识库知识质量与
    模型能力影响定位。无正文/空响应的尝试同样记录（失败分析）。
    """

    __tablename__ = "chat_trace"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    # assistant 消息 id（无正文/无引用未落消息时为 ""）
    message_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    team_name: Mapped[str | None] = mapped_column(String(64))
    kb_id: Mapped[str | None] = mapped_column(String(64), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="quick")  # quick / agent
    question: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(128))
    # reasoning_content 全文（chat_message.thinking 的补充：agent 链路此前不落）
    thinking: Mapped[str | None] = mapped_column(Text)
    # JSON 数组：时序事件 [{t, ms, ...}]（stage/context/thinking/tool_call/tool_result/done）
    events: Mapped[str | None] = mapped_column(Text)
    # JSON 数组：检索命中明细 [{kb_id, chunk_id, document_id, score, content 截断}]
    hits: Mapped[str | None] = mapped_column(Text)
    # JSON：汇总 {rounds, tool_calls, retrieval_hits, dur_ms, error, ...}
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=_now_iso)


def new_trace_id() -> str:
    return _new_id("trace")
