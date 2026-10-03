"""Chat LLM client — OpenAI-compatible streaming chat completions.

Mirrors WeKnora's chat_completion_stream: RAG context is serialized into the
last message (search_results display block), then streamed to the model with
SSE-compatible chunk emission. Config resolution follows the platform's
runtime-config precedence (DB SYSTEM_CONFIG > env).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from api.config import get_settings
from api.models.knowledge import DocChunk
from api.services.retrieval import ChunkHit
from api.services.runtime_config import resolve_runtime_config
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0


@dataclass
class ChatConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = DEFAULT_TIMEOUT
    custom_headers: dict | None = None  # extra request headers (model-scoped)


@dataclass
class ChatMessage:
    role: str  # system / user / assistant
    content: str
    images: list[str] | None = None  # image data URLs (base64) for multimodal input


def load_chat_config(
    db: Session | None = None,
    *,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
) -> ChatConfig:
    """Resolve chat config with scoped model priority.

    New chain: personal default > team default > system default (scoped
    model registry), then the legacy modo_dim SYSTEM_CONFIG > env path.
    Consumers with no user context (worker/wiki build, MCP) skip personal
    and team scopes and resolve the system default first, then legacy/env.
    """
    settings = get_settings()

    if db is not None:
        try:
            from api.services.models import resolve_model_config

            resolved = resolve_model_config(
                db,
                "chat",
                caller_user_id=user_id or "",
                caller_team_name=team_name or "",
                is_sys_admin=is_sys_admin,
            )
            if resolved and resolved.get("base_url"):
                return ChatConfig(
                    base_url=resolved["base_url"],
                    api_key=resolved.get("api_key") or "",
                    model=resolved.get("model") or "",
                    custom_headers=resolved.get("custom_headers") or None,
                )
        except Exception:
            logger.warning("failed to resolve scoped chat model; using legacy config", exc_info=True)

    def _resolve(env_key: str, db_codes: list[str], fallback: str = "") -> str:
        if db is None:
            return fallback
        try:
            return resolve_runtime_config(db, env_key, db_codes=db_codes, fallback=fallback)["value"]
        except Exception:
            logger.warning("failed to resolve %s from DB; using env", env_key, exc_info=True)
            return fallback

    base_url = _resolve(
        "AI_CHAT_API_ENDPOINT", ["AI_CHAT_API_ENDPOINT", "DIFY_CHAT_API_ENDPOINT"], ""
    )
    api_key = _resolve("AI_CHAT_API_KEY", ["AI_CHAT_API_KEY"], "")
    model = _resolve("AI_CHAT_MODEL", ["AI_CHAT_MODEL"], "")
    return ChatConfig(base_url=base_url, api_key=api_key, model=model)


def serialize_context(hits: list[ChunkHit]) -> str:
    """Serialize retrieval hits into the model-visible context block.

    Mirrors WeKnora's search_results display block: each row carries chunk_id,
    knowledge refs and the chunk content. The model is instructed to cite
    [chunk_id] markers, which the frontend renders as public citations.
    """
    if not hits:
        return ""
    rows = []
    for hit in hits:
        rows.append(
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "kb_id": hit.kb_id,
                "content": hit.content,
                "score": round(float(hit.score), 4),
            }
        )
    block = {
        "display_type": "search_results",
        "results": rows,
    }
    return json.dumps(block, ensure_ascii=False)


SYSTEM_PROMPT = (
    "你是一个知识库问答助手。请仅依据下方提供的检索片段回答问题，"
    "不得编造片段中不存在的内容。回答时在引用处标注来源片段编号，"
    "格式为 [chunk_id]。若检索片段不足以回答，请明确说明。\n\n"
    "以下是检索到的知识片段：\n"
    "{context}"
)


def build_messages(
    question: str,
    hits: list[ChunkHit],
    history: list[ChatMessage] | None = None,
    extra_context: str = "",
    agent_prompt: str = "",
    image_urls: list[str] | None = None,
) -> list[ChatMessage]:
    """Build the message list: system (with context) + history + user question.

    extra_context (session attachments, already markdown) is appended to the
    system prompt so the model can answer from attached documents.
    agent_prompt overrides the default system prompt (agent persona).
    image_urls (session image attachments, data URLs) are attached to the
    user message as OpenAI content-array image parts (multimodal QA).
    """
    context = serialize_context(hits)
    if context or extra_context:
        parts = []
        if context:
            parts.append(f"以下为知识库检索片段：\n{context}")
        if extra_context:
            parts.append(f"以下为本次会话上传的附件文档内容：\n{extra_context}")
        base = (
            "你是一个知识库问答助手。请优先依据提供的检索片段和附件文档回答问题；"
            "若信息不足，请明确说明。"
        )
        system = (agent_prompt or base) + "\n\n" + "\n\n".join(parts)
    else:
        system = agent_prompt or "你是一个知识库问答助手。若检索片段不足以回答，请明确说明。"
    messages = [ChatMessage(role="system", content=system)]
    messages.extend(history or [])
    messages.append(
        ChatMessage(role="user", content=question, images=image_urls or None)
    )
    return messages


class ChatClient:
    """OpenAI-compatible streaming chat client."""

    def __init__(self, cfg: ChatConfig):
        self.cfg = cfg

    def stream(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float | None = None,
        thinking: bool | None = None,
    ) -> Iterator[str]:
        """Yield delta text chunks as they arrive (SSE-parsed)."""
        for delta in self.stream_events(messages, temperature, max_tokens, top_p, thinking):
            yield delta.get("text") or ""

    def stream_events(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float | None = None,
        thinking: bool | None = None,
    ) -> Iterator[dict]:
        """Yield structured events as they arrive (SSE-parsed).

        Events: {"type": "thinking", "text": ...} for reasoning_content,
        {"type": "delta", "text": ...} for the answer content, and
        {"type": "done"} at the end.

        top_p/thinking are optional; when given they are added to the
        request payload (thinking maps to {"type": "enabled"|"disabled"}).
        Messages carrying images are serialized as OpenAI content arrays
        (image_url data URLs) for multimodal (vllm) probing.
        """
        endpoint = (self.cfg.base_url or "").rstrip("/")
        if not endpoint:
            raise RuntimeError("AI_CHAT_API_ENDPOINT is not configured")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"

        def _serialize(m: ChatMessage) -> dict:
            if not m.images:
                return {"role": m.role, "content": m.content}
            parts: list[dict] = [{"type": "text", "text": m.content}]
            for url in m.images:
                parts.append({"type": "image_url", "image_url": {"url": url}})
            return {"role": m.role, "content": parts}

        payload = {
            "model": self.cfg.model,
            "messages": [_serialize(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        if self.cfg.custom_headers:
            # model-scoped custom headers win over the defaults (custom auth
            # schemes like X-Api-Key are configured per model)
            headers.update(self.cfg.custom_headers)

        with httpx.Client(timeout=self.cfg.timeout) as client:
            with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[len("data:"):].strip()
                    elif line.startswith("data: "):
                        data = line[len("data: "):].strip()
                    else:
                        data = line.strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if reasoning:
                        yield {"type": "thinking", "text": reasoning}
                    if text:
                        yield {"type": "delta", "text": text}
        yield {"type": "done"}

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float | None = None,
        thinking: bool | None = None,
    ) -> str:
        """Non-streaming convenience: join streamed deltas."""
        return "".join(self.stream(messages, temperature, max_tokens, top_p, thinking))


# ---------------------------------------------------------------------------
# Framework-callable QA entry (used by the API router)
# ---------------------------------------------------------------------------


def answer_question(
    kb_db: Session,
    kb: Any,
    question: str,
    query_embedding: list[float] | None,
    retrieval_overrides: dict | None = None,
    history: list[ChatMessage] | list[dict] | None = None,
    chat_cfg: ChatConfig | None = None,
    extra_context: str = "",
    agent_prompt: str = "",
    image_urls: list[str] | None = None,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
) -> Iterator[dict]:
    """Full RAG QA stream: retrieve -> build messages -> stream deltas.

    Yields dicts: {"type": "context", "hits": [...]} first, then
    {"type": "delta", "text": "..."} per chunk. history accepts either
    ChatMessage objects or {"role", "content"} dicts. extra_context is
    injected into the system prompt (used for session attachments).
    image_urls attaches session images to the user message (multimodal QA).
    user_id/team_name/is_sys_admin scope the resolved chat model
    (personal > team > system > legacy) when chat_cfg is not given.
    """
    from api.services.retrieval import config_from_kb, hybrid_search

    cfg = config_from_kb(kb, retrieval_overrides)
    hits = hybrid_search(kb_db, kb.id, question, query_embedding, cfg)
    if chat_cfg is None:
        chat_cfg = load_chat_config(
            kb_db, user_id=user_id or "", team_name=team_name or "", is_sys_admin=is_sys_admin
        )

    yield {"type": "context", "hits": [h.__dict__ for h in hits]}

    if history:
        normalized: list[ChatMessage] = [
            m if isinstance(m, ChatMessage) else ChatMessage(role=m["role"], content=m["content"])
            for m in history
        ]
    else:
        normalized = []
    messages = build_messages(
        question, hits, normalized,
        extra_context=extra_context, agent_prompt=agent_prompt, image_urls=image_urls,
    )
    client = ChatClient(chat_cfg)
    for ev in client.stream_events(messages):
        if ev.get("type") == "delta":
            yield {"type": "delta", "text": ev.get("text") or ""}
        elif ev.get("type") == "thinking":
            yield {"type": "thinking", "text": ev.get("text") or ""}