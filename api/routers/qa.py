"""RAG QA streaming endpoint — /api/v1/qa/stream (+ resume via /qa/stream/{id}).

Generation decoupled from the HTTP response: a background thread performs
retrieval + LLM streaming and appends every event to a Redis buffer
(api.services.qa_stream_store). The SSE response tails that buffer, so the
browser navigating away does not cancel generation; reconnecting with the
stream id resumes from the caller's offset.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Iterator

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbDatasource
from api.services import qa_stream_store as store
from api.services.chat_sessions import (
    append_message,
    attachment_contents,
    attachment_images,
    get_context_messages,
    get_session,
)
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/qa", tags=["qa"])


class QARequest(BaseModel):
    kb_id: str = ""
    question: str = ""
    session_id: str | None = None
    history: list[dict] | None = None
    top_k: int | None = None
    threshold: float | None = None
    embed_query: bool = True
    # 2026-10-06: 问答界面模型切换（指定 chat 模型 id；不传=用默认模型）
    model_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

def _fail(stream_id: str, message: str) -> None:
    """Terminal failure: emit error + done so tailing clients always finish."""
    store.append_event(stream_id, {"type": "error", "message": message})
    store.append_event(stream_id, {"type": "done"})
    store.mark_done(stream_id)




def _run_generation(
    req: QARequest,
    x_next_identity: str,
    stream_id: str,
) -> None:
    """Background thread: full RAG QA, appending events to the Redis buffer.

    Runs detached from the HTTP response lifecycle so navigation away does not
    cancel it. Uses its OWN db session via get_sessionmaker (never the
    request's, which dies with the request scope).
    """
    import logging

    from api.db import get_sessionmaker
    from api.services.chat import answer_question
    from api.services.models import is_admin

    logger = logging.getLogger(__name__)
    db = get_sessionmaker()()
    try:
        kb = db.execute(
            select(KbDatasource).where(KbDatasource.id == req.kb_id)
        ).scalars().first()
        if not kb:
            _fail(stream_id, f"知识库不存在: {req.kb_id}")
            return

        identity = decode_identity_cookie(x_next_identity or "")
        ctx_user_id = identity.user_id if identity else ""
        ctx_team_name = identity.team_name or "" if identity else ""
        ctx_is_admin = False
        if ctx_user_id:
            try:
                ctx_is_admin = is_admin(db, ctx_user_id)
            except Exception:  # noqa: BLE001
                ctx_is_admin = False

        # Session mode: validate + load history, persist user msg
        session = None
        user_id = None
        attachment_ctx = ""
        image_urls: list[str] = []
        history: list[dict] = []
        if req.session_id:
            if not identity or not identity.user_id:
                _fail(stream_id, "未登录")
                return
            user_id = identity.user_id
            try:
                session = get_session(db, user_id, req.session_id)
            except Exception as e:  # noqa: BLE001
                _fail(stream_id, str(e))
                return
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in get_context_messages(db, req.session_id, limit=20)
            ]
            try:
                attachment_ctx = attachment_contents(db, req.session_id)
            except Exception:  # noqa: BLE001
                attachment_ctx = ""
            try:
                image_urls = attachment_images(db, req.session_id)
            except Exception:  # noqa: BLE001
                image_urls = []
        else:
            history = [
                {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                for m in (req.history or [])
                if m.get("content")
            ]

        if session and user_id and req.session_id:
            try:
                append_message(db, user_id, req.session_id, "user", req.question)
            except Exception as e:  # noqa: BLE001
                logger.warning("persist user msg failed: %s", e)

        query_embedding = None
        if req.embed_query:
            try:
                from api.services.embedding import get_embedding_client

                client = get_embedding_client(
                    db,
                    user_id=ctx_user_id,
                    team_name=ctx_team_name,
                    is_sys_admin=ctx_is_admin,
                )
                query_embedding = client.embed_query(req.question)
            except Exception:  # noqa: BLE001
                pass

        overrides: dict = {}
        if req.top_k is not None:
            overrides["top_k"] = req.top_k
        if req.threshold is not None:
            overrides["threshold"] = req.threshold

        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        refs: list[dict] = []
        try:
            requested_cfg = None
            if req.model_id:
                # 2026-10-06: 模型切换——指定模型则显式解析（文本/图片均生效）
                try:
                    from api.services.chat import load_chat_config

                    requested_cfg = load_chat_config(
                        db,
                        user_id=ctx_user_id,
                        team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                        model_id=req.model_id,
                    )
                except Exception:  # noqa: BLE001
                    requested_cfg = None
            if image_urls:
                # 图片问答要求对话模型支持视觉（supports_vision）
                try:
                    from api.models.model import KbModel
                    from api.services.chat import load_chat_config

                    chat_cfg = requested_cfg or load_chat_config(
                        db,
                        user_id=ctx_user_id,
                        team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                    )
                    mrow = db.execute(
                        select(KbModel).where(KbModel.name == chat_cfg.model)
                    ).scalars().first()
                    if mrow and not mrow.supports_vision:
                        _fail(stream_id, "当前对话模型不支持图片输入，请在模型配置中选用支持视觉的对话模型")
                        return
                except Exception:  # noqa: BLE001
                    pass
            for event in answer_question(
                kb_db=db,
                kb=kb,
                question=req.question,
                query_embedding=query_embedding,
                retrieval_overrides=overrides,
                history=history,
                extra_context=attachment_ctx,
                image_urls=image_urls,
                user_id=ctx_user_id,
                team_name=ctx_team_name,
                is_sys_admin=ctx_is_admin,
                chat_cfg=requested_cfg if req.model_id else None,  # 2026-10-06: 指定模型时复用解析结果
            ):
                if event.get("type") == "context":
                    hits = event.get("hits") or []
                    refs = [
                        {
                            "chunk_id": h.get("chunk_id", ""),
                            "content": h.get("content", ""),
                            "document_id": h.get("document_id", ""),
                            "kb_id": h.get("kb_id", ""),
                            "score": h.get("score", 0),
                            "meta": h.get("meta") or {},
                        }
                        for h in hits
                    ]
                    store.append_event(stream_id, {"type": "context", "hits": refs})
                elif event.get("type") == "thinking":
                    thinking_parts.append(event.get("text", ""))
                    store.append_event(stream_id, {"type": "thinking", "text": event.get("text", "")})
                elif event.get("type") == "delta":
                    answer_parts.append(event.get("text", ""))
                    store.append_event(stream_id, {"type": "delta", "text": event.get("text", "")})
                else:
                    store.append_event(stream_id, event)
        except Exception as e:  # noqa: BLE001
            logger.exception("qa generation failed")
            store.append_event(stream_id, {"type": "error", "message": str(e)})
        finally:
            # persist assistant message (content + refs + thinking)
            if session and user_id and req.session_id:
                try:
                    payload = {"content": "".join(answer_parts), "refs": refs}
                    if thinking_parts:
                        payload["thinking"] = "".join(thinking_parts)
                    append_message(db, user_id, req.session_id, "assistant", **payload)
                except Exception as e:  # noqa: BLE001
                    logger.warning("persist assistant failed: %s", e)
            store.append_event(stream_id, {"type": "done"})
            store.mark_done(stream_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("qa generation crashed")
        try:
            _fail(stream_id, str(e))
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


@router.post("/stream")
def qa_stream(
    req: QARequest,
    db: Session = Depends(get_db),
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> StreamingResponse:
    """Start a QA stream: spawn background generation, tail Redis buffer via SSE."""
    stream_id = store.new_stream_id()
    identity = decode_identity_cookie(x_next_identity or "")
    store.start_stream(
        stream_id,
        {"user_id": identity.user_id if identity else ""},
    )
    # meta event occupies index 0 so resume (after=N) skips it naturally
    store.append_event(stream_id, {"type": "stream_meta", "stream_id": stream_id})

    # spawn background thread; it owns its own DB session
    t = threading.Thread(
        target=_run_generation,
        args=(req, x_next_identity or "", stream_id),
        daemon=True,
    )
    t.start()

    def generator() -> Iterator[str]:
        offset = 0
        while True:
            events = store.read_events(stream_id, after=offset)
            for ev in events:
                yield _sse(ev)
            offset += len(events)
            if store.is_done(stream_id):
                tail = store.read_events(stream_id, after=offset)
                for ev in tail:
                    yield _sse(ev)
                return
            time.sleep(0.15)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Stream-Id": stream_id,
        },
    )


@router.get("/stream/{stream_id}")
def resume_stream(
    stream_id: str,
    after: int = 0,
) -> StreamingResponse:
    """Resume a QA stream from an offset — replay buffered events via SSE.

    Used by the frontend when returning to a page mid-answer: the background
    generation kept running (Redis buffer), so we just continue streaming
    from `after` (number of events already delivered).
    """
    # Unknown / expired stream: fail fast instead of tailing forever.
    if not store.get_meta(stream_id) and store.stream_length(stream_id) == 0:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="流不存在或已过期")

    def generator() -> Iterator[str]:
        offset = int(after)
        idle_polls = 0
        while True:
            events = store.read_events(stream_id, after=offset)
            for ev in events:
                yield _sse(ev)
            offset += len(events)
            if events:
                idle_polls = 0
            else:
                idle_polls += 1
            if store.is_done(stream_id):
                tail = store.read_events(stream_id, after=offset)
                for ev in tail:
                    yield _sse(ev)
                return
            # A done stream with no new events drains fully; a stream that
            # produced nothing for a long time is treated as finished so the
            # client never hangs on a dead buffer.
            if idle_polls > 2000:  # ~5 min with no event at all
                yield _sse({"type": "error", "message": "流已失活"})
                return
            time.sleep(0.15)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]