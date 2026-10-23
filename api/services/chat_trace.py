"""智能问答过程追踪（chat_trace）——思考过程 / 工具调用链 / 检索命中的收集与落库。

2026-10-23 分析需求：记录 quick 与 agent 问答的完整过程，供后续分析知识库
知识质量与模型能力影响。事件源 = chat.py answer_question 的 SSE 事件流：
- stage（retrieval/generation running|done）→ 阶段时序
- context（hits）→ 检索命中明细（content 截断）
- thinking（text 流）→ reasoning_content 全文聚合
- tool_call（tool_calls[]）→ 模型请求的工具（name/arguments 截断）
- tool（results[] 含耗时）→ 工具执行结果（result 截断 + success/error）
- delta / done / error

落库位置：qa.py / agents.py 会话模式下 finally 调用 save_trace（与 chat_message
持久化同守卫；无正文/空响应的尝试也记录，供失败分析）。
"""

from __future__ import annotations

import json
import time

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.models.chat_session import ChatTrace, new_trace_id

# 截断阈值（与 chat.py MAX_TOOL_OUTPUT_CHARS=6000? 就近对齐，控制入库体积）
MAX_ARG_CHARS = 2000   # 工具参数
MAX_RESULT_CHARS = 3000  # 工具结果
MAX_HIT_CONTENT_CHARS = 2000  # 命中片段
MAX_THINK_CHARS = 60000  # 思考全文上限（超出截断，防单条过大）


def _truncate(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + f"…[截断 {len(s)} 字符]"


class TraceCollector:
    """收集一次问答回合（一个 assistant 消息）的过程事件，链路内随手喂。"""

    def __init__(
        self,
        *,
        session_id: str,
        user_id: str,
        team_name: str = "",
        kb_id: str = "",
        agent_id: str = "",
        kind: str = "quick",
        question: str = "",
        model: str = "",
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.team_name = team_name
        self.kb_id = kb_id
        self.agent_id = agent_id
        self.kind = kind
        self.question = question
        self.model = model
        self._t0 = time.monotonic()
        self.events: list[dict] = []
        self._thinking_parts: list[str] = []
        self.hits: list[dict] = []
        self._pending_tool_calls: dict[str, dict] = {}  # id -> call 事件
        self._round = 0  # 工具调用轮次（tool_call 事件数）
        self._tool_calls_n = 0
        self._delta_chars = 0
        self.error: str | None = None
        self.done = False

    @property
    def thinking(self) -> str:
        return "".join(self._thinking_parts)[:MAX_THINK_CHARS]

    def _ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def on_event(self, ev: dict) -> None:
        """喂 answer_question 产出的每个事件。"""
        t = ev.get("type")
        try:
            if t == "stage":
                self.events.append(
                    {"t": "stage", "stage": ev.get("stage"), "status": ev.get("status"), "ms": self._ms()}
                )
            elif t == "context":
                hits = ev.get("hits") or []
                for h in hits:
                    self.hits.append(
                        {
                            "kb_id": h.get("kb_id", ""),
                            "chunk_id": h.get("chunk_id", ""),
                            "document_id": h.get("document_id", ""),
                            "file_name": h.get("meta", {}).get("file_name", "") if isinstance(h.get("meta"), dict) else "",
                            "score": round(float(h.get("score") or 0), 4),
                            "content": _truncate(h.get("content", ""), MAX_HIT_CONTENT_CHARS),
                        }
                    )
                self.events.append({"t": "context", "n": len(hits), "ms": self._ms()})
            elif t == "thinking":
                text = str(ev.get("text") or "")
                if text:
                    self._thinking_parts.append(text)
                self.events.append({"t": "thinking", "n": len(text), "ms": self._ms()})
            elif t == "tool_call":
                calls = ev.get("tool_calls") or []
                self._round += 1
                self._tool_calls_n += len(calls)
                for tc in calls:
                    cid = str(tc.get("id") or f"call_{self._tool_calls_n}")
                    entry = {
                        "t": "tool_call",
                        "round": self._round,
                        "ms": self._ms(),
                        "id": cid,
                        "name": tc.get("name", ""),
                        "arguments": _truncate(tc.get("arguments", ""), MAX_ARG_CHARS),
                    }
                    self.events.append(entry)
                    self._pending_tool_calls[cid] = entry
            elif t == "tool":
                results = ev.get("results") or []
                durations = ev.get("durations") or []
                for i, res in enumerate(results):
                    cid = str(res.get("id") or "")
                    call = self._pending_tool_calls.pop(cid, None) or {}
                    dur = float(durations[i]) if i < len(durations) else 0.0
                    self.events.append(
                        {
                            "t": "tool_result",
                            "round": call.get("round", self._round),
                            "ms": self._ms(),
                            "id": cid,
                            "name": res.get("name") or call.get("name", ""),
                            "success": bool(res.get("success", True)),
                            "error": str(res.get("error") or "")[:500] or None,
                            "elapsed_ms": int(dur * 1000),
                            "result": _truncate(res.get("result", ""), MAX_RESULT_CHARS),
                        }
                    )
            elif t == "delta":
                self._delta_chars += len(str(ev.get("text") or ""))
            elif t == "error":
                self.error = str(ev.get("message") or ev.get("error") or "")[:500] or self.error
                self.events.append({"t": "error", "ms": self._ms(), "message": self.error})
            elif t == "done":
                self.done = True
                self.events.append({"t": "done", "ms": self._ms()})
            # 其余事件（assistant_message 等）不记录，避免噪声
        except Exception:  # noqa: BLE001 — 追踪不得影响问答主链路
            pass

    def summary(self) -> dict:
        rounds = max(self._round, 1 if self.done else 0)
        return {
            "rounds": rounds,
            "tool_calls": self._tool_calls_n,
            "retrieval_hits": len(self.hits),
            "thinking_chars": len(self.thinking),
            "answer_chars": self._delta_chars,
            "dur_ms": self._ms(),
            "error": self.error,
        }


def save_trace(db: Session, col: TraceCollector, message_id: str = "") -> str:
    """落库一条 chat_trace；失败不抛错（记录是尽力而为，问答主链路优先）。"""
    try:
        if not col.session_id or not col.user_id:
            return ""
        summary = col.summary()
        trace = ChatTrace(
            id=new_trace_id(),
            session_id=col.session_id,
            message_id=message_id or "",
            user_id=col.user_id,
            team_name=col.team_name or "",
            kb_id=col.kb_id or "",
            agent_id=col.agent_id or "",
            kind=col.kind,
            question=str(col.question or "")[:2000],
            model=col.model or "",
            thinking=col.thinking or None,
            events=json.dumps(col.events, ensure_ascii=False) if col.events else None,
            hits=json.dumps(col.hits, ensure_ascii=False) if col.hits else None,
            summary=json.dumps(summary, ensure_ascii=False),
        )
        db.add(trace)
        db.commit()
        return trace.id
    except Exception:  # noqa: BLE001
        db.rollback()
        return ""


def _trace_dict(t: ChatTrace, full: bool = False) -> dict:
    def _loads(v):
        if not v:
            return None
        try:
            return json.loads(v)
        except Exception:
            return v

    d = {
        "id": t.id,
        "session_id": t.session_id,
        "message_id": t.message_id,
        "user_id": t.user_id,
        "team_name": t.team_name,
        "kb_id": t.kb_id,
        "agent_id": t.agent_id,
        "kind": t.kind,
        "question": t.question,
        "model": t.model,
        "summary": _loads(t.summary),
        "created_at": t.created_at,
    }
    if full:
        d["thinking"] = t.thinking
        d["events"] = _loads(t.events)
        d["hits"] = _loads(t.hits)
    return d


def list_traces(
    db: Session,
    *,
    session_id: str = "",
    message_id: str = "",
    user_id: str = "",
    kb_id: str = "",
    agent_id: str = "",
    kind: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(ChatTrace)
    if session_id:
        stmt = stmt.where(ChatTrace.session_id == session_id)
    if message_id:
        stmt = stmt.where(ChatTrace.message_id == message_id)
    if user_id:
        stmt = stmt.where(ChatTrace.user_id == user_id)
    if kb_id:
        stmt = stmt.where(ChatTrace.kb_id == kb_id)
    if agent_id:
        stmt = stmt.where(ChatTrace.agent_id == agent_id)
    if kind:
        stmt = stmt.where(ChatTrace.kind == kind)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.execute(
        stmt.order_by(desc(ChatTrace.created_at), desc(ChatTrace.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_trace_dict(r) for r in rows]}


def get_trace(db: Session, trace_id: str) -> dict | None:
    t = db.get(ChatTrace, trace_id)
    return _trace_dict(t, full=True) if t else None