"""Chat LLM client — OpenAI-compatible streaming chat completions.

Mirrors WeKnora's chat_completion_stream: RAG context is serialized into the
last message (search_results display block), then streamed to the model with
SSE-compatible chunk emission. Config resolution follows the platform's
runtime-config precedence (DB SYSTEM_CONFIG > env).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

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
    role: str  # system / user / assistant / tool
    content: str
    images: list[str] | None = None  # image data URLs (base64) for multimodal input
    # 工具调用（assistant 回传 LLM 的工具调用，OpenAI 格式；无工具链路时保持 None）
    tool_calls: list[dict] | None = None
    # role=tool 时关联的 assistant tool_call id（工具结果回填）
    tool_call_id: str | None = None


def load_chat_config(
    db: Session | None = None,
    *,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
    model_id: str | None = None,
) -> ChatConfig:
    """Resolve chat config with scoped model priority.

    New chain: personal default > team default > system default (scoped
    model registry), then the legacy modo_dim SYSTEM_CONFIG > env path.
    Consumers with no user context (worker/wiki build, MCP) skip personal
    and team scopes and resolve the system default first, then legacy/env.
    model_id: 显式指定模型（问答界面模型切换，2026-10-06）。
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
                model_id=model_id,
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
        tools: list[dict] | None = None,
    ) -> Iterator[dict]:
        """Yield structured events as they arrive (SSE-parsed).

        Events: {"type": "thinking", "text": ...} for reasoning_content,
        {"type": "delta", "text": ...} for the answer content, and
        {"type": "done"} at the end. tools (OpenAI function-calling payload) is
        injected into the request when given; model-emitted tool calls are
        accumulated from streaming fragments and yielded as
        {"type": "tool_call", "id", "name", "arguments"} once complete.

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
            if m.images:
                parts: list[dict] = [{"type": "text", "text": m.content}]
                for url in m.images:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                content: Any = parts
            else:
                content = m.content
            out: dict = {"role": m.role, "content": content}
            if m.tool_calls:
                out["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                out["tool_call_id"] = m.tool_call_id
            return out

        payload = {
            "model": self.cfg.model,
            "messages": [_serialize(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
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

        # 流式工具调用片段按 index 累积（OpenAI 把 id/name/arguments 分片下发）
        tool_acc: dict[int, dict] = {}

        def _flush_tool_calls() -> Iterator[dict]:
            for idx in sorted(tool_acc):
                slot = tool_acc[idx]
                yield {
                    "type": "tool_call",
                    "id": slot.get("id") or "",
                    "name": slot.get("name") or "",
                    "arguments": slot.get("arguments") or "",
                }
            tool_acc.clear()

        with httpx.Client(timeout=self.cfg.timeout) as client:
            # 2026-10-07: 上游 LLM 服务偶发 5xx/403/超时（Infer AI 瞬时故障）——
            # 在流建立阶段（尚未 yield 任何内容）自动重试，避免整轮问答失败。
            # 一旦开始迭代输出则不再重试（避免重复内容）。
            # httpx client.stream 是 contextmanager 不能手动退出后重入，
            # 故用 build_request + send(stream=True)（原生 Response，可自管关闭）。
            resp = None
            last_exc: Exception | None = None
            for attempt in range(3):  # 最多 3 次尝试（2 次重试）
                try:
                    req = client.build_request("POST", endpoint, json=payload, headers=headers)
                    resp = client.send(req, stream=True)
                    resp.raise_for_status()
                    break
                except (httpx.HTTPStatusError, httpx.TransportError, httpx.HTTPError) as exc:
                    last_exc = exc
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:  # noqa: BLE001
                            pass
                        resp = None
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    # 4xx（除 408/429）视为不可重试：鉴权/参数问题重试无意义
                    if status and 400 <= status < 500 and status not in (408, 429):
                        raise
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            if resp is None:
                raise last_exc or RuntimeError("chat stream failed")
            try:
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
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if reasoning:
                        yield {"type": "thinking", "text": reasoning}
                    if text:
                        yield {"type": "delta", "text": text}
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", 0))
                        slot = tool_acc.setdefault(idx, {})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] = (slot.get("arguments") or "") + fn["arguments"]
                    # finish_reason=tool_calls：本响应只发工具调用，无正文
                    if choice.get("finish_reason") == "tool_calls":
                        yield from _flush_tool_calls()
            finally:
                resp.close()
        # 流结束兜底：部分实现不发 finish_reason=tool_calls，已累积调用照常吐出
        yield from _flush_tool_calls()
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


def _chunk_document_images(hits: list, limit: int = 4) -> list[str]:
    """从检索命中 chunk 提取文档图片引用（kb-image://doc_id/name）并读回 data URL。

    2026-10-07 文档图片可理解改造：ingest 阶段把解析出的图片存对象存储、
    md 中留 kb-image:// 引用；问答命中带引用的 chunk 时按引用读回图片，
    随 user 消息注入视觉模型。存储不可用/引用失效时静默跳过。
    """
    import base64
    import re

    refs: list[str] = []
    for h in hits:
        content = getattr(h, "content", "") or (h.get("content") if isinstance(h, dict) else "")
        for m in re.finditer(r"kb-image://([0-9a-zA-Z_-]+)/([^\s)\"']+)", content or ""):
            refs.append(m.group(0))
        if len(refs) >= limit:
            break
    out: list[str] = []
    for ref in refs[:limit]:
        rel = ref[len("kb-image://") :]
        try:
            from api.services.storage import get_bytes, resolve_new_path

            raw = get_bytes(resolve_new_path(f"kb_documents/{rel}"))
            if raw:
                out.append("data:image/png;base64," + base64.b64encode(raw).decode("ascii"))
        except Exception:  # noqa: BLE001 - 图片读回失败不阻断问答
            continue
    return out


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
    tools: list[dict] | None = None,
    tool_executor: Callable[[dict], str] | None = None,
    max_tool_iterations: int = 3,
) -> Iterator[dict]:
    """Full RAG QA stream: retrieve -> build messages -> stream deltas.

    Yields dicts: {"type": "context", "hits": [...]} first, then
    {"type": "delta", "text": "..."} per chunk. history accepts either
    ChatMessage objects or {"role", "content"} dicts. extra_context is
    injected into the system prompt (used for session attachments).
    image_urls attaches session images to the user message (multimodal QA).
    user_id/team_name/is_sys_admin scope the resolved chat model
    (personal > team > system > legacy) when chat_cfg is not given.

    tools + tool_executor turn on the OpenAI function-calling loop (agent
    skills): the tool payload is attached to each LLM request, and when the
    model calls a tool the executor runs it (api/services/agent_skills.py)
    and its text result is fed back. tools=None keeps the plain
    single-shot stream (default, no behaviour change). Intermediate rounds'
    text is buffered (tool preamble must not leak into the answer) and only
    the final round's deltas are yielded, plus one
    {"type": "tool", "tool_calls", "results"} event per executed round.
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
    # 2026-10-07: 文档图片注入——命中 chunk 里的 kb-image:// 引用读回 data URL，
    # 与附件图片合并后随 user 消息走视觉模型（文档图片可理解/问答）
    doc_images = _chunk_document_images(hits)
    images = list(image_urls or []) + doc_images
    messages = build_messages(
        question, hits, normalized,
        extra_context=extra_context, agent_prompt=agent_prompt, image_urls=images,
    )
    client = ChatClient(chat_cfg)
    # 无工具：与接入前逐字一致的单轮流式
    if not tools:
        yield from _stream_once(client, messages)
        return
    max_rounds = max(1, int(max_tool_iterations or 1))
    for round_idx in range(max_rounds):
        tool_calls: list[dict] = []
        buffered: list[dict] = []  # 本轮流式文本（非最终轮不回传，避免前言污染答案）
        for ev in client.stream_events(messages, tools=tools):
            etype = ev.get("type")
            if etype == "tool_call":
                tool_calls.append(ev)
            elif etype in ("delta", "thinking"):
                buffered.append(ev)
        if not tool_calls or round_idx == max_rounds - 1:
            for ev in buffered:
                yield ev
            break
        # 回填工具结果：assistant 消息带 tool_calls，随后每个工具一条 role=tool
        messages.append(
            ChatMessage(
                role="assistant",
                content="".join(ev.get("text") or "" for ev in buffered),
                tool_calls=[
                    {
                        "id": tc.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.get("name") or "",
                            "arguments": tc.get("arguments") or "",
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            )
        )
        results: list[dict] = []
        for i, tc in enumerate(tool_calls):
            result = (
                tool_executor(tc)
                if tool_executor
                else f"工具不可用: 未配置执行器（{tc.get('name') or '?'}）"
            )
            results.append(
                {"id": tc.get("id") or f"call_{i}", "name": tc.get("name") or "", "result": result}
            )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=result,
                    tool_call_id=tc.get("id") or f"call_{i}",
                )
            )
        yield {"type": "tool", "tool_calls": tool_calls, "results": results}


def _stream_once(client: ChatClient, messages: list[ChatMessage]) -> Iterator[dict]:
    """单轮流式：只透传 delta / thinking（done 由 answer_question 之前的循环收尾）。"""
    for ev in client.stream_events(messages):
        if ev.get("type") in ("delta", "thinking"):
            yield ev