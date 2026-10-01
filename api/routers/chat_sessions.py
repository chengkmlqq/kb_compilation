"""Chat session API routes — conversation persistence + LLM enhancements.

Backs the frontend "智能问答" conversation UI:
- POST   /api/v1/sessions                 create
- GET    /api/v1/sessions                 list (pinned first)
- GET    /api/v1/sessions/{id}            detail
- PUT    /api/v1/sessions/{id}            rename / rebind kb / pin
- DELETE /api/v1/sessions/{id}            delete (+messages)
- POST   /api/v1/sessions/batch-delete    batch delete
- DELETE /api/v1/sessions/{id}/messages   clear messages
- POST   /api/v1/sessions/{id}/generate-title    LLM title
- POST   /api/v1/sessions/recommendations        new-session recommended questions
- POST   /api/v1/sessions/follow-up              follow-up suggestions
- GET    /api/v1/sessions/{id}/messages          load history
- DELETE /api/v1/sessions/{id}/messages/{mid}    delete one message
Requires the x-next-identity cookie (user ownership).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.chat_session import MAX_ATTACHMENT_BYTES
from api.services.chat_sessions import (
    batch_delete_sessions,
    clear_session_messages,
    create_agent_session,
    create_attachment,
    create_session,
    delete_attachment,
    delete_message,
    delete_session,
    follow_up_suggestions,
    generate_title,
    get_session,
    list_attachments,
    list_sessions,
    load_messages,
    recommended_questions,
    search_messages,
    update_session,
)
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/sessions", tags=["chat-sessions"])


def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    """FastAPI dependency: resolve the current user id from the auth cookie."""
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _model_ctx(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """模型作用域上下文：{user_id, team_name, is_admin}，供 LLM 增强解析
    个人/团队/系统级模型。"""
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    from api.services.models import caller_context

    return caller_context(db, identity.user_id)


class CreateSessionRequest(BaseModel):
    kb_id: str | None = None
    title: str | None = None
    agent_id: str | None = None  # 非空 = agent 会话


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    kb_id: str | None = None
    pinned: bool | None = None


class BatchDeleteRequest(BaseModel):
    session_ids: list[str] = []


class RecommendRequest(BaseModel):
    kb_id: str | None = None
    count: int = 3


class FollowUpRequest(BaseModel):
    session_id: str
    last_answer: str = ""
    count: int = 3


@router.post("")
def api_create_session(
    req: CreateSessionRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    if req.agent_id:
        return {"success": True, "data": create_agent_session(db, user_id, req.agent_id, req.kb_id, req.title)}
    return {"success": True, "data": create_session(db, user_id, req.kb_id, req.title)}


@router.get("")
def api_list_sessions(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    return {"success": True, "data": list_sessions(db, user_id, page, page_size)}


@router.get("/{session_id}")
def api_get_session(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    s = get_session(db, user_id, session_id)
    return {
        "success": True,
        "data": {
            "id": s.id,
            "kb_id": s.kb_id,
            "title": s.title,
            "pinned": bool(s.pinned),
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        },
    }


@router.put("/{session_id}")
def api_update_session(
    session_id: str,
    req: UpdateSessionRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    return {
        "success": True,
        "data": update_session(db, user_id, session_id, title=req.title, kb_id=req.kb_id, pinned=req.pinned),
    }


@router.delete("/{session_id}")
def api_delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    delete_session(db, user_id, session_id)
    return {"success": True, "data": {"deleted": session_id}}


@router.post("/batch-delete")
def api_batch_delete(
    req: BatchDeleteRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    n = batch_delete_sessions(db, user_id, req.session_ids)
    return {"success": True, "data": {"deleted": n}}


@router.delete("/{session_id}/messages")
def api_clear_messages(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    clear_session_messages(db, user_id, session_id)
    return {"success": True, "data": {"cleared": session_id}}


@router.post("/{session_id}/generate-title")
def api_generate_title(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
    model_ctx: dict = Depends(_model_ctx),
) -> dict:
    title = generate_title(db, user_id, session_id, model_ctx=model_ctx)
    return {"success": True, "data": {"title": title}}


@router.post("/recommendations")
def api_recommendations(
    req: RecommendRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
    model_ctx: dict = Depends(_model_ctx),
) -> dict:
    questions = recommended_questions(db, req.kb_id, req.count, model_ctx=model_ctx)
    return {"success": True, "data": {"questions": questions}}


@router.post("/follow-up")
def api_follow_up(
    req: FollowUpRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
    model_ctx: dict = Depends(_model_ctx),
) -> dict:
    questions = follow_up_suggestions(
        db, user_id, req.session_id, req.last_answer, req.count, model_ctx=model_ctx
    )
    return {"success": True, "data": {"questions": questions}}


@router.get("/{session_id}/messages")
def api_load_messages(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    return {"success": True, "data": {"items": load_messages(db, user_id, session_id)}}


@router.delete("/{session_id}/messages/{message_id}")
def api_delete_message(
    session_id: str,
    message_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    delete_message(db, user_id, session_id, message_id)
    return {"success": True, "data": {"deleted": message_id}}


# ---------------------------------------------------------------------------
# 消息搜索
# ---------------------------------------------------------------------------


class SearchMessagesRequest(BaseModel):
    keyword: str = ""
    limit: int = 20


@router.post("/search")
def api_search_messages(
    req: SearchMessagesRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    hits = search_messages(db, user_id, req.keyword, req.limit)
    return {"success": True, "data": {"items": hits}}


# ---------------------------------------------------------------------------
# 附件（临时文档问答）
# ---------------------------------------------------------------------------


@router.get("/{session_id}/attachments")
def api_list_attachments(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    return {"success": True, "data": {"items": list_attachments(db, user_id, session_id)}}


@router.post("/{session_id}/attachments")
async def api_upload_attachment(
    session_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
    file: UploadFile = File(...),
) -> dict:
    """上传临时文档附件：docreader 解析为 markdown 后入库，问答时自动注入。"""
    from fastapi import HTTPException as _HTTPException

    from api.models.chat_session import MAX_ATTACHMENT_BYTES
    from api.services.chat_sessions import create_attachment
    from api.services.ingest import parse_document

    data = await file.read()
    if not data:
        raise _HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise _HTTPException(status_code=413, detail=f"附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB")
    file_name = (file.filename or "untitled").strip()
    ext = Path(file_name).suffix.lower()
    try:
        markdown = parse_document(file_name, ext, data)
    except Exception as e:  # noqa: BLE001
        raise _HTTPException(status_code=400, detail=f"文档解析失败: {e}") from e
    att = create_attachment(
        db, user_id, session_id, file_name, ext, len(data), markdown or ""
    )
    return {"success": True, "data": att}


@router.delete("/{session_id}/attachments/{attachment_id}")
def api_delete_attachment(
    session_id: str,
    attachment_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(_require_user_id),
) -> dict:
    delete_attachment(db, user_id, session_id, attachment_id)
    return {"success": True, "data": {"deleted": attachment_id}}


__all__ = ["router"]