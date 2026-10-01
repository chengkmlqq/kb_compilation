"""Knowledge QA API routes — SSE streaming, aligned with the platform's
ai-chat stream contract ({data: ...} lines, [DONE] terminator).

Supports two modes:
- session mode: pass session_id → the user question and the final assistant
  answer are persisted to chat_message; history is loaded from the session.
- ad-hoc mode: no session_id → single-turn RAG with caller-supplied history.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbDatasource
from api.services.chat import answer_question
from api.services.chat_sessions import (
    append_message,
    attachment_contents,
    get_context_messages,
    get_session,
)
from api.services.embedding import get_embedding_client
from api.services.identity import decode_identity_cookie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa", tags=["qa"])


class QARequest(BaseModel):
    kb_id: str = Field(..., description="知识库 ID")
    question: str = Field(..., description="用户问题")
    session_id: str | None = Field(default=None, description="会话 ID（传入则持久化+加载历史）")
    history: list[dict] = Field(default_factory=list, description="历史消息 [{role, content}]（无 session_id 时用）")
    top_k: int = Field(default=5, ge=1, le=50)
    threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    embed_query: bool = Field(default=True, description="是否做向量召回")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def qa_stream(
    req: QARequest,
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> StreamingResponse:
    """Stream RAG QA: context event -> deltas. Persists when session_id given."""

    def generator():
        kb = db.execute(select(KbDatasource).where(KbDatasource.id == req.kb_id)).scalars().first()
        if not kb:
            yield _sse({"type": "error", "message": f"知识库不存在: {req.kb_id}"})
            yield _sse({"type": "done"})
            return

        # 身份（无 session 也解析：用于个人/团队模型作用域）
        identity = decode_identity_cookie(x_next_identity or "")
        ctx_user_id = identity.user_id if identity else ""
        ctx_team_name = identity.team_name or "" if identity else ""
        ctx_is_admin = False
        if ctx_user_id:
            try:
                from api.services.models import is_admin

                ctx_is_admin = is_admin(db, ctx_user_id)
            except Exception:  # noqa: BLE001
                ctx_is_admin = False

        # 会话模式：校验 + 加载历史
        session = None
        user_id = None
        attachment_ctx = ""
        if req.session_id:
            if not identity or not identity.user_id:
                yield _sse({"type": "error", "message": "未登录"})
                yield _sse({"type": "done"})
                return
            user_id = identity.user_id
            try:
                session = get_session(db, user_id, req.session_id)
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "message": str(e)})
                yield _sse({"type": "done"})
                return
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in get_context_messages(db, req.session_id, limit=20)
            ]
            # 附件内容注入（临时文档问答）
            try:
                attachment_ctx = attachment_contents(db, req.session_id)
            except Exception:  # noqa: BLE001
                attachment_ctx = ""
        else:
            history = [
                {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                for m in req.history
                if m.get("content")
            ]

        # 持久化用户消息
        if session and user_id and req.session_id:
            try:
                append_message(db, user_id, req.session_id, "user", req.question)
            except Exception as e:  # noqa: BLE001
                logger.warning("failed to persist user message: %s", e)

        query_embedding = None
        if req.embed_query:
            try:
                client = get_embedding_client(
                    db,
                    user_id=ctx_user_id,
                    team_name=ctx_team_name,
                    is_sys_admin=ctx_is_admin,
                )
                query_embedding = client.embed_query(req.question)
            except Exception:  # noqa: BLE001
                logger.exception("query embedding failed; vector arm disabled")

        overrides = {"top_k": req.top_k, "threshold": req.threshold}

        answer_parts: list[str] = []
        refs: list[dict] = []
        try:
            for event in answer_question(
                kb_db=db,
                kb=kb,
                question=req.question,
                query_embedding=query_embedding,
                retrieval_overrides=overrides,
                history=history,
                extra_context=attachment_ctx,
                user_id=ctx_user_id,
                team_name=ctx_team_name,
                is_sys_admin=ctx_is_admin,
            ):
                if event.get("type") == "context":
                    hits = event.get("hits") or []
                    refs = [
                        {"chunk_id": h.get("chunk_id", ""), "score": h.get("score", 0)}
                        for h in hits
                    ]
                elif event.get("type") == "delta":
                    answer_parts.append(event.get("text", ""))
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            logger.exception("qa_stream failed")
            yield _sse({"type": "error", "message": str(e)})
        finally:
            # 持久化助手回答（含引用）
            if session and user_id and req.session_id:
                try:
                    append_message(db, user_id, req.session_id, "assistant", "".join(answer_parts), refs=refs)
                except Exception as e:  # noqa: BLE001
                    logger.warning("failed to persist assistant message: %s", e)
            yield _sse({"type": "done"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
