"""问答反馈（点赞/点踩）统计 API —— 供「问答反馈」页面采集用户使用情况。

端点：
  GET /feedback/stats  反馈汇总（总数/点赞/点踩/点赞率/今日/近7天）
  GET /feedback/list   反馈明细分页（回答/问题/用户/会话/类型/时间）

需要登录（x-next-identity cookie）；未登录 401。admin/ROOT 看全量，
其他用户只统计自己的反馈（对齐数据查询团队授权口径）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.feedback_stats import feedback_stats, list_feedback
from api.services.identity import Identity, decode_identity_cookie

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _require_identity(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> Identity:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity


@router.get("/stats")
def stats(
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    return feedback_stats(db, idn.user_id or "")


@router.get("/list")
def list_feedback_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    feedback_type: Optional[str] = Query(None, description="like / dislike / 空=全部"),
    keyword: str = Query("", description="匹配问题/回答/会话标题/用户"),
    db: Session = Depends(get_db),
    idn: Identity = Depends(_require_identity),
) -> dict:
    return list_feedback(
        db,
        page,
        page_size,
        feedback_type=feedback_type,
        keyword=keyword,
        user_id=idn.user_id or "",
    )