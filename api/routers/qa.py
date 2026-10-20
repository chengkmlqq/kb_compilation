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
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbDatasource
from api.services import qa_stream_store as store
from api.services.chat_sessions import (
    append_message,
    attachment_contents,
    attachment_image_data_urls,
    attachment_image_ids,
    attachment_images,
    get_context_messages,
    get_session,
    set_attachment_caption,
    update_message,
)
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/qa", tags=["qa"])


# 2026-10-15 联网搜索按钮由参数表控制显隐：页面挂载时读取此端点决定是否渲染开关。
# 走 /api/v1/qa 前缀（前端 /chat 菜单控制），普通问答用户可读；写入口在参数管理（dims）。
@router.get("/config")
def read_chat_config(db: Session = Depends(get_db)) -> dict:
    from api.services.system_config import get_platform_config_value

    raw = get_platform_config_value(db, "CHAT_WEB_SEARCH_ENABLED").strip().lower()
    return {
        "success": True,
        "data": {"web_search_enabled": raw in ("1", "true", "yes", "on")},
    }


class QARequest(BaseModel):
    kb_id: str = ""
    question: str = ""
    session_id: str | None = None
    history: list[dict] | None = None
    top_k: int | None = None
    threshold: float | None = None
    embed_query: bool = True

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("问题不能为空")
        return v
    # 2026-10-06: 问答界面模型切换（指定 chat 模型 id；不传=用默认模型）
    model_id: str | None = None
    # 2026-10-12 对齐 WeKnora 输入区联网开关：请求级开启联网搜索（注入 web_search 工具）
    web_search_enabled: bool = False
    # 2026-10-20 深度思考：请求级显式开启 thinking（reasoning_content 流式透出）。
    # None/不传 = 沿用网关默认（不发 thinking 参数），True = 显式 enabled，False = disabled。
    thinking: bool | None = None
    # 2026-10-18 对齐 WeKnora「图片仅随当轮请求注入」：本轮上传的图片附件 id。
    # None（旧客户端未传）= 回落会话级全部图片；[]= 本轮无图。
    image_attachment_ids: list[str] | None = None
    # 2026-10-20 附件问答交互改造：本轮上传的全部附件 id（图片+文档），
    # 持久化到 user 消息 attachment_ids，供前端在用户消息侧展示附件。
    # None = 旧客户端未传（回落 images 语义）；[] = 本轮无附件。
    attachment_ids: list[str] | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

def _fail(stream_id: str, message: str) -> None:
    """Terminal failure: emit error + done so tailing clients always finish."""
    store.append_event(stream_id, {"type": "error", "message": message})
    store.append_event(stream_id, {"type": "done"})
    store.mark_done(stream_id)


def visible_kbs_for_qa(
    db: Session,
    ctx_user_id: str,
    ctx_team_name: str,
    ctx_is_admin: bool,
) -> list[KbDatasource]:
    """全库问答可见范围（2026-10-19 统一口径，对齐知识库列表）。

    admin 全量（state=1）；非 admin 按 visible_clauses 并集
    （own ∪ team ∪ system），保证「列表里看得到 → 全库问答也能检到」。
    """
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    if not ctx_is_admin:
        from api.services.scope import visible_clauses

        _clauses = visible_clauses(ctx_user_id, ctx_team_name, ctx_is_admin, KbDatasource)
        if _clauses:
            stmt = stmt.where(*_clauses)
    return list(db.execute(stmt).scalars().all())


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
        # 2026-10-16 全库问答：kb_id 为空（未选择知识库）→ 检索全部可见知识库；
        # kb_id 非空则校验知识库必须真实存在。
        kb = None
        kbs: list = []
        if req.kb_id:
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

        # 2026-10-16 全库问答：未选知识库时解析可见范围（state=1）。
        # 2026-10-19 统一口径：admin 全量；非 admin 按 visible_clauses 并集
        # （own ∪ team ∪ system），与知识库列表 kb_visible_clauses 一致——
        # 此前按 team_name 精确匹配，导致自己建的 personal 库 / system 预置库
        # 在全库问答中检索不到（列表可见但检索范围不含）。
        if not req.kb_id:
            kbs = visible_kbs_for_qa(db, ctx_user_id, ctx_team_name, ctx_is_admin)

        # Session mode: validate + load history, persist user msg
        session = None
        user_id = None
        # 2026-10-14 持久化后的消息 id 回传前端：done 事件带回服务端 id，
        # 否则前端只能保留本地占位 id（local-ai-*），点赞/反馈按 id 查库会 404「消息不存在」。
        user_message_id = ""
        attachment_ctx = ""
        image_urls: list[str] = []
        # 本轮图片附件 id（对齐 WeKnora attachment_ids）：仅当轮注入；None=旧客户端
        turn_image_ids: list[str] = []
        images_scoped = req.image_attachment_ids is not None
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
            # 历史图片轮次只回带视觉描述文本（WeKnora extractImageCaptions 同款）
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in get_context_messages(
                    db, req.session_id, limit=20, with_image_captions=True
                )
            ]
            try:
                attachment_ctx = attachment_contents(db, req.session_id)
            except Exception:  # noqa: BLE001
                attachment_ctx = ""
            try:
                if images_scoped:
                    turn_image_ids = [str(i) for i in (req.image_attachment_ids or [])]
                    image_urls = attachment_image_data_urls(db, req.session_id, turn_image_ids)
                else:
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
                image_ids = (
                    turn_image_ids
                    if images_scoped
                    else attachment_image_ids(db, req.session_id)
                )
                persisted = append_message(
                    db,
                    user_id,
                    req.session_id,
                    "user",
                    req.question,
                    images=image_ids or None,
                    attachment_ids=(
                        [str(i) for i in (req.attachment_ids or [])]
                        if req.attachment_ids is not None
                        else None
                    ),
                )
                user_message_id = persisted.get("id") or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("persist user msg failed: %s", e)

        query_embedding = None
        # 纯对话模式（未选知识库）不检索，无需生成查询向量
        if req.embed_query and (kb is not None or kbs):
            try:
                from api.services.embedding import get_embedding_client

                client = get_embedding_client(
                    db,
                    kb_id=(kb.id if kb is not None else (kbs[0].id if kbs else "")),
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
        image_description = ""
        plan_images: list[str] = image_urls
        include_doc_images = True
        # 2026-10-20 增量持久化：生成过程中定期把已生成部分落库（同一消息 UPDATE），
        # 而非等 finally 一次性落库——刷新/关页后重开会话也能看到已生成的部分内容。
        # 首条 delta 时创建占位消息拿到 id，之后按时间节流 update，finally 终更。
        assistant_message_id = ""
        _last_persist_ts = 0.0

        def _persist_partial(force: bool = False) -> None:
            """把当前已生成内容写入会话消息（占位不存在则先创建）。"""
            nonlocal assistant_message_id, _last_persist_ts
            if not (session and user_id and req.session_id):
                return
            content = "".join(answer_parts)
            if not content and not thinking_parts and not refs:
                return  # 尚无任何内容，不建占位（避免空消息）
            now = time.time()
            if not force and now - _last_persist_ts < 1.0:
                return  # 节流：至少 1 秒写一次
            try:
                if assistant_message_id:
                    update_message(
                        db, user_id, req.session_id, assistant_message_id,
                        content=content, refs=refs,
                        thinking="".join(thinking_parts) if thinking_parts else None,
                    )
                else:
                    saved = append_message(
                        db, user_id, req.session_id, "assistant",
                        content,
                        refs=refs,
                        thinking="".join(thinking_parts) if thinking_parts else None,
                    )
                    assistant_message_id = saved.get("id") or ""
                    # 2026-10-20 增量落库完成首写：向前端暴露服务端消息 id，
                    # 前端刷新后续拉时若命中这条消息则直接复用（不再插入重复占位）
                    if assistant_message_id:
                        store.append_event(
                            stream_id,
                            {"type": "assistant_message", "message_id": assistant_message_id},
                        )
                _last_persist_ts = now
            except Exception as e:  # noqa: BLE001
                logger.warning("incremental persist assistant failed: %s", e)

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
            # 2026-10-18 图片问答对齐 WeKnora：模型能看图则直传（vision），不能看则用
            # 视觉模型产出图片描述注入文本（describe），两者都不行才明确拒绝（reject）。
            # OIP 协议无多模态绑定，同样走 describe/reject（此前图片被静默丢弃）。
            if image_urls:
                try:
                    from api.services.chat import load_chat_config
                    from api.services.image_qa import plan_image_input

                    chat_cfg = requested_cfg or load_chat_config(
                        db,
                        user_id=ctx_user_id,
                        team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                    )
                    plan = plan_image_input(
                        db,
                        image_urls=image_urls,
                        question=req.question,
                        chat_cfg=chat_cfg,
                        user_id=ctx_user_id,
                        team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                    )
                    if plan.mode == "reject":
                        _fail(stream_id, plan.error)
                        return
                    if plan.mode == "vision":
                        plan_images = plan.images
                    elif plan.mode == "describe":
                        # 描述回写附件，供历史轮次回带（WeKnora caption 同款）
                        plan_images = []
                        image_description = plan.description
                        include_doc_images = False
                        caption_ids = turn_image_ids
                        if not caption_ids and req.session_id:
                            # 旧客户端（会话级图片）：按上传顺序取附件 id 对齐描述
                            try:
                                caption_ids = attachment_image_ids(db, req.session_id)
                            except Exception:  # noqa: BLE001
                                caption_ids = []
                        for aid, cap in zip(caption_ids, plan.captions):
                            try:
                                set_attachment_caption(db, aid, cap)
                            except Exception:  # noqa: BLE001
                                logger.warning("persist image caption failed: %s", aid)
                except Exception:  # noqa: BLE001 — 计划解析失败不阻断问答（保持旧行为）
                    logger.exception("image input planning failed; passing images through")
                    plan_images = image_urls
            # 2026-10-12 对齐 WeKnora 输入区联网开关：请求级开启时注入 web_search 工具，
            # 装配失败不阻断问答（回落纯 RAG 单轮流式）
            ws_tools: list[dict] = []
            ws_executor = None
            if req.web_search_enabled:
                try:
                    from api.services.websearch import request_websearch_tool_context

                    ws_tools, ws_executor = request_websearch_tool_context(
                        db,
                        enabled=True,
                        caller_user_id=ctx_user_id,
                        caller_team_name=ctx_team_name,
                        is_sys_admin=ctx_is_admin,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("websearch tool resolution failed; websearch disabled for this call")
            for event in answer_question(
                kb_db=db,
                kb=kb,
                kbs=kbs or None,
                question=req.question,
                query_embedding=query_embedding,
                retrieval_overrides=overrides,
                history=history,
                extra_context=attachment_ctx,
                image_urls=plan_images,
                image_description=image_description,
                include_doc_images=include_doc_images,
                user_id=ctx_user_id,
                team_name=ctx_team_name,
                is_sys_admin=ctx_is_admin,
                chat_cfg=requested_cfg if req.model_id else None,  # 2026-10-06: 指定模型时复用解析结果
                tools=ws_tools or None,
                tool_executor=ws_executor,
                thinking=req.thinking,  # 2026-10-20 深度思考开关（None=网关默认）
                max_tool_iterations=3,
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
                    _persist_partial()  # 2026-10-20 增量落库：生成中定期保存已生成部分
                else:
                    store.append_event(stream_id, event)
        except Exception as e:  # noqa: BLE001
            logger.exception("qa generation failed")
            store.append_event(stream_id, {"type": "error", "message": str(e)})
        finally:
            # 终更 assistant 消息：增量持久化已建占位则 UPDATE 同一消息（保留中间
            # 已落库内容），否则（无内容场景）回落原 append 逻辑。
            if session and user_id and req.session_id:
                try:
                    _persist_partial(force=True)
                    if not assistant_message_id:
                        # 全程无 delta（如空回答/失败）：与旧行为一致，补一条空助手消息
                        saved = append_message(
                            db, user_id, req.session_id, "assistant",
                            "".join(answer_parts), refs=refs,
                            thinking="".join(thinking_parts) if thinking_parts else None,
                        )
                        assistant_message_id = saved.get("id") or ""
                except Exception as e:  # noqa: BLE001
                    logger.warning("persist assistant failed: %s", e)
            # 2026-10-14 done 事件带回持久化消息 id：前端据此把本地占位 id
            # （local-ai-*）替换为服务端 id，点赞/反馈接口才能按 id 命中消息。
            store.append_event(
                stream_id,
                {
                    "type": "done",
                    "message_id": assistant_message_id,
                    "user_message_id": user_message_id,
                },
            )
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
            time.sleep(0.05)

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
            time.sleep(0.05)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]