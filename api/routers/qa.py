"""Knowledge QA API routes — SSE streaming, aligned with the platform's
ai-chat stream contract ({data: ...} lines, [DONE] terminator)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import KbDatasource
from api.services.chat import answer_question
from api.services.embedding import get_embedding_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa", tags=["qa"])


class QARequest(BaseModel):
    kb_id: str = Field(..., description="知识库 ID")
    question: str = Field(..., description="用户问题")
    history: list[dict] = Field(default_factory=list, description="历史消息 [{role, content}]")
    top_k: int = Field(default=5, ge=1, le=50)
    threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    embed_query: bool = Field(default=True, description="是否做向量召回")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def qa_stream(req: QARequest, db: Session = Depends(get_db)) -> StreamingResponse:
    """Stream RAG QA: first a context event (retrieved hits), then deltas."""

    def generator():
        kb = db.execute(select(KbDatasource).where(KbDatasource.id == req.kb_id)).scalars().first()
        if not kb:
            yield _sse({"type": "error", "message": f"知识库不存在: {req.kb_id}"})
            yield _sse({"type": "done"})
            return

        query_embedding = None
        if req.embed_query:
            try:
                client = get_embedding_client(db)
                query_embedding = client.embed_query(req.question)
            except Exception:  # noqa: BLE001
                logger.exception("query embedding failed; vector arm disabled")

        overrides = {"top_k": req.top_k, "threshold": req.threshold}
        history = [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in req.history
            if m.get("content")
        ]

        try:
            for event in answer_question(
                kb_db=db,
                kb=kb,
                question=req.question,
                query_embedding=query_embedding,
                retrieval_overrides=overrides,
                history=history,
            ):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            logger.exception("qa_stream failed")
            yield _sse({"type": "error", "message": str(e)})
        finally:
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
