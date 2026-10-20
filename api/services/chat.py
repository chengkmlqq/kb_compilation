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

# ---- 2026-10-20 对齐 WeKnora agent 循环加固（internal/agent/const.go + engine.go）----
# WeKnora: maxEmptyResponseRetries=2 / maxRepeatedResponseRounds=2 /
# MaxToolOutputChars / MaxContextTokens。kb 无对应 AgentConfig 字段，用常量兜底，
# 语义与 WeKnora 一致：空内容重试、卡死检测、工具结果截断、上下文预算压缩。
MAX_EMPTY_RETRIES = 2            # 空回答重试上限（对齐 maxEmptyResponseRetries）
MAX_REPEATED_TOOL_ROUNDS = 2     # 连续相同工具调用轮次上限（对齐卡死检测）
MAX_TOOL_OUTPUT_CHARS = 12000    # 单个工具结果截断上限（对齐 MaxToolOutputChars）
MAX_CONTEXT_TOKENS = 60000       # 工具循环上下文预算（粗估 token，超限压缩早期 tool 消息）
_EMPTY_NUDGE = "请直接给出你的完整回答（纯文本），不要调用工具。"


@dataclass
class ChatConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = DEFAULT_TIMEOUT
    custom_headers: dict | None = None  # extra request headers (model-scoped)
    # 接口协议：openai（OpenAI 兼容 /v1，流式）| oip（KServe OIP /v2 非流式）
    interface_type: str = "openai"
    # 模型登记表里的视觉能力标记（None=未知/未登记，不做门禁）
    supports_vision: bool | None = None
    # 单次回答最大输出 token（None = 链路默认 2048；模型注册表可配置，思考型模型建议调大）
    max_tokens: int | None = None


# 非视觉模型的图片描述注入前缀（WeKnora into_chat_message.go 同款文案）
IMAGE_CONTENT_PREFIX = "\n\n[用户上传图片内容]\n"


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
                    interface_type=(resolved.get("interface_type") or "openai"),
                    supports_vision=resolved.get("supports_vision"),
                    max_tokens=resolved.get("max_tokens"),
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
    # 2026-10-18: env 路径回落的模型不声明视觉能力 → 明确 supports_vision=False，
    # 图片问答走 describe/vision 回退（此前 None=不设门禁直传, 非视觉模型静默丢图）
    return ChatConfig(base_url=base_url, api_key=api_key, model=model, supports_vision=False)


def serialize_context(hits: list[ChunkHit]) -> str:
    """Serialize retrieval hits into the model-visible context block.

    Mirrors WeKnora's <chunk chunk_id knowledge_id knowledge_title> rows:
    each row carries chunk_id, knowledge refs, the READABLE document title
    and the chunk content. The model is instructed to cite
    <kb doc="文件名" chunk_id=".." kb_id=".."/> markers (WeKnora's citation
    tag), which the frontend renders as document-name citation pills
    instead of bare UUIDs.
    """
    if not hits:
        return ""
    rows = []
    for hit in hits:
        rows.append(
            {
                "chunk_id": hit.chunk_id,
                "document_name": hit.document_title or hit.document_id,
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


# 引用规范 —— 对齐 WeKnora：模型输出 <kb doc=... chunk_id=.../> 标签，
# 前端 MarkdownViewer 把它渲染成「显示文档名」的引用胶囊（可点击看片段内容）。
# 注意：不要让模型输出裸 chunk_id（32 位乱码）——用户看到的是一串 ID，不是链接。
CITATION_INSTRUCTIONS = (
    "引用规范（必须遵守）：在引用某片段内容的位置，输出引用标签：\n"
    '<kb doc="片段的 document_name" chunk_id="片段的 chunk_id" kb_id="片段的 kb_id" />\n'
    "其中 doc 必须照抄检索结果里的 document_name（文档文件名），"
    "chunk_id/kb_id 照抄对应字段。标签前后不加多余括号或编号；"
    "正文里不要输出裸的 chunk_id / document_id（纯 32 位乱码用户看不懂）。\n"
    "如果使用了联网搜索结果（web_search 工具返回的网页），引用网页时输出：\n"
    '<web url="网页完整URL" title="网页标题" />\n'
    "url 必须照抄搜索结果里的 URL，title 用网页标题（无标题可省略）。"
)


SYSTEM_PROMPT = (
    "你是一个知识库问答助手。请仅依据下方提供的检索片段回答问题，"
    "不得编造片段中不存在的内容。\n"
    + CITATION_INSTRUCTIONS
    + "\n\n以下是检索到的知识片段：\n{context}"
)


def build_messages(
    question: str,
    hits: list[ChunkHit],
    history: list[ChatMessage] | None = None,
    extra_context: str = "",
    agent_prompt: str = "",
    image_urls: list[str] | None = None,
    image_description: str = "",
) -> list[ChatMessage]:
    """Build the message list: system (with context) + history + user question.

    extra_context (session attachments, already markdown) is appended to the
    system prompt so the model can answer from attached documents.
    agent_prompt overrides the default system prompt (agent persona).
    image_urls (session image attachments, data URLs) are attached to the
    user message as OpenAI content-array image parts (multimodal QA).
    image_description: 非视觉模型路径的图像文本描述，以「[用户上传图片内容]」拼进
    user 消息（对齐 WeKnora into_chat_message.go）。
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
            "若信息不足，请明确说明。\n"
            + CITATION_INSTRUCTIONS
        )
        # agent 人设也要带引用规范，否则 agent 路径的引用仍是裸 id
        system = (
            (base if not agent_prompt else agent_prompt + "\n\n" + CITATION_INSTRUCTIONS)
            + "\n\n"
            + "\n\n".join(parts)
        )
    else:
        # 2026-10-15 纯对话模式（未选知识库 / 检索零命中）：不注入检索片段，
        # 用通用助手人设自由回答；信息不足时明确说明，避免编造。
        system = agent_prompt or "你是一个智能问答助手。请直接、准确地回答用户的问题；信息不足时请明确说明，不要编造。"
    messages = [ChatMessage(role="system", content=system)]
    messages.extend(history or [])
    user_content = question
    if image_description:
        # 图片文本描述：模型看不了图时（非视觉模型 / OIP 协议）注入（WeKnora 同款）
        user_content = question + IMAGE_CONTENT_PREFIX + image_description
    messages.append(
        ChatMessage(role="user", content=user_content, images=image_urls or None)
    )
    return messages


def _usage_from_openai(o: dict) -> dict[str, int]:
    """从 usage 对象提取 input/output/cache（deepseek 与 OpenAI 两种字段兼容）。"""
    read = int(o.get("prompt_cache_hit_tokens") or 0)
    details = o.get("prompt_tokens_details") or {}
    if not read and isinstance(details, dict):
        read = int(details.get("cached_tokens") or 0)
    return {
        "input": int(o.get("prompt_tokens") or 0),
        "output": int(o.get("completion_tokens") or 0),
        "read": read,
        "creation": int((details or {}).get("cache_creation_tokens") or 0)
        if isinstance(details, dict)
        else 0,
    }


def _record_chat_usage(
    usage_ctx: dict,
    model: str,
    usage: dict[str, int],
    t0: float,
    ttft_ms: float | None,
    err: Exception | None,
    retries: int,
) -> None:
    """写一条 chat 来源的 LLM 使用记录（best-effort，绝不抛出）。"""
    try:
        from api.services.llm_usage import classify_error, record_llm_usage

        msg = str(err)[:300] if err else ""
        record_llm_usage(
            trace_id=str(usage_ctx.get("trace_id") or ""),
            source="chat",
            kb_id=str(usage_ctx.get("kb_id") or ""),
            kb_name=str(usage_ctx.get("kb_name") or ""),
            doc_id=str(usage_ctx.get("doc_id") or ""),
            doc_name=str(usage_ctx.get("doc_name") or ""),
            model=model,
            operation_type=str(usage_ctx.get("operation") or "qa"),
            input_tokens=usage.get("input", 0),
            output_tokens=usage.get("output", 0),
            cache_read_tokens=usage.get("read", 0),
            cache_creation_tokens=usage.get("creation", 0),
            ttft_ms=ttft_ms,
            total_ms=int((time.time() - t0) * 1000),
            status="success" if err is None else classify_error(msg),
            error_msg=msg,
            retries=retries,
        )
    except Exception:  # noqa: BLE001 — 埋点失败不影响问答
        logger.debug("record chat usage failed", exc_info=True)


class ChatClient:
    """OpenAI-compatible streaming chat client."""

    def __init__(self, cfg: ChatConfig):
        self.cfg = cfg

    def stream(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        thinking: bool | None = None,
        usage_ctx: dict | None = None,
    ) -> Iterator[str]:
        """Yield delta text chunks as they arrive (SSE-parsed)."""
        for delta in self.stream_events(messages, temperature, max_tokens, top_p, thinking, usage_ctx):
            yield delta.get("text") or ""

    def stream_events(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        thinking: bool | None = None,
        usage_ctx: dict | None = None,
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

        usage_ctx（2026-10-16 LLM 使用看板埋点）: 提供时开启
        stream_options.include_usage 并测量 TTFT/总耗时，结束写一条
        kb_llm_usage 记录（source=chat）。字段：kb_id/kb_name/doc_id/
        doc_name/operation。不提供则与接入前逐字一致（无埋点无副作用）。
        """
        # 接口协议 = oip：KServe Open Inference Protocol（/v2 非流式）
        oip_taken = (self.cfg.interface_type or "openai").lower() == "oip"
        # max_tokens: None = 未显式指定 → 模型注册表配置（cfg.max_tokens）或链路默认 2048
        if max_tokens is None:
            max_tokens = self.cfg.max_tokens or 2048
        t0 = time.time()
        usage_acc: dict[str, int] = {"input": 0, "output": 0, "read": 0, "creation": 0}
        # 埋点统计（_stream_openai_stream 内原地更新，finally 读取）
        _stats: dict = {"t0": t0, "ttft": None, "retries": 0}
        err: Exception | None = None
        try:
            if oip_taken:
                yield from self._stream_oip(messages, temperature, max_tokens, top_p, usage_ctx)
                return
            yield from self._stream_openai_stream(
                messages, temperature, max_tokens, top_p, thinking, tools,
                usage_ctx, usage_acc, _stats,
            )
        except Exception as exc:  # noqa: BLE001
            err = exc
            raise
        finally:
            if usage_ctx is not None and not oip_taken:
                _record_chat_usage(
                    usage_ctx, self.cfg.model, usage_acc,
                    t0, _stats.get("ttft"), err, _stats.get("retries", 0),
                )

    def _stream_openai_stream(
        self,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        top_p: float | None,
        thinking: bool | None,
        tools: list[dict] | None,
        usage_ctx: dict | None,
        usage_acc: dict[str, int],
        _stats: dict,
    ) -> Iterator[dict]:

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
        if usage_ctx is not None:
            # 2026-10-16 LLM 使用看板：请求 usage 计数（多数 OpenAI 兼容网关支持）
            payload["stream_options"] = {"include_usage": True}
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
                _stats["retries"] = attempt
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
                    # 4xx 视为不可重试（401 鉴权失败/参数问题重试无意义）；
                    # 例外：403/408/429 —— 部分服务(Infer AI)用 403 表达限流，
                    # 实测高峰期会瞬时 403 后自行恢复，故纳入重试。
                    if status and 400 <= status < 500 and status not in (403, 408, 429):
                        # 2026-10-12: 上游网关用 404 表达「模型名不存在」
                        # （tokenerpgw/erp 网关实测：model_not_found 回 404）。
                        # 裸抛 httpx 异常只会给用户一句 "Client error '404 Not
                        # Found' for url ..."，无从判断是地址错还是模型名错，
                        # 这里改成可据模型名自查的中文提示。
                        if status == 404:
                            raise RuntimeError(
                                f"模型不可用：网关返回 404（模型不存在或地址错误）。"
                                f"当前模型名「{self.cfg.model}」，接口地址「{endpoint}」。"
                                f"请在「模型配置」中核对该模型名是否为网关实际支持的取值。"
                            ) from exc
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
                    # 2026-10-16 LLM 使用看板：TTFT = 首个可见 token（thinking/正文）时间
                    if (reasoning or text) and _stats.get("ttft") is None:
                        _stats["ttft"] = round((time.time() - _stats["t0"]) * 1000, 2)
                    if reasoning:
                        yield {"type": "thinking", "text": reasoning}
                    if text:
                        yield {"type": "delta", "text": text}
                    # 流式 usage（include_usage 末帧下发）：deepseek 系缓存字段兼容
                    u = obj.get("usage") or {}
                    if u:
                        got = _usage_from_openai(u)
                        for _k in usage_acc:
                            usage_acc[_k] += got.get(_k, 0)
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

    def _stream_oip(
        self,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        top_p: float | None,
        usage_ctx: dict | None = None,
    ) -> Iterator[dict]:
        """KServe Open Inference Protocol (non-streaming).

        OIP has no SSE; the whole answer arrives in one
        /v2/models/{model}/infer response, so it is emitted as a single
        delta event to keep the upstream event contract unchanged.
        Thinking / tool calls / multimodal have no portable OIP binding.
        OIP 无 usage 字段：提供 usage_ctx 时只记耗时/状态，token 记 0。
        """
        from api.services.oip import decode_bytes_cell, infer, scalar_input, text_input

        t0 = time.time()
        err: Exception | None = None
        try:
            roles = {"system": "System", "user": "User", "assistant": "Assistant", "tool": "Tool"}
            parts = [f"{roles.get(m.role, m.role)}: {m.content}" for m in messages if (m.content or "").strip()]
            prompt = "\n\n".join(parts)
            if not prompt.strip():
                raise RuntimeError("OIP infer skipped: empty prompt")

            inputs = [
                text_input("text", [prompt]),
                scalar_input("stream", "BOOL", False),
            ]
            if max_tokens:
                inputs.append(scalar_input("max_tokens", "INT64", int(max_tokens)))
            if temperature is not None:
                inputs.append(scalar_input("temperature", "FP32", float(temperature)))
            if top_p is not None:
                inputs.append(scalar_input("top_p", "FP32", float(top_p)))

            blob = infer(
                self.cfg.base_url,
                self.cfg.model,
                inputs,
                [{"name": "text"}],
                api_key=self.cfg.api_key,
                custom_headers=self.cfg.custom_headers,
                timeout=self.cfg.timeout,
            )
            cells = blob.get("text") or []
            text = decode_bytes_cell(cells[0]) if cells else ""
            if text:
                yield {"type": "delta", "text": text}
            yield {"type": "done"}
        except Exception as exc:  # noqa: BLE001
            err = exc
            raise
        finally:
            if usage_ctx is not None:
                _record_chat_usage(
                    usage_ctx, self.cfg.model,
                    {"input": 0, "output": 0, "read": 0, "creation": 0},
                    t0, None, err, 0,
                )

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
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
    kb: Any | None,
    question: str,
    query_embedding: list[float] | None,
    retrieval_overrides: dict | None = None,
    kbs: list[Any] | None = None,
    history: list[ChatMessage] | list[dict] | None = None,
    chat_cfg: ChatConfig | None = None,
    extra_context: str = "",
    agent_prompt: str = "",
    image_urls: list[str] | None = None,
    image_description: str = "",
    include_doc_images: bool = True,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
    tools: list[dict] | None = None,
    tool_executor: Callable[[dict], str] | None = None,
    thinking: bool | None = None,
    max_tool_iterations: int = 3,
) -> Iterator[dict]:
    """Full RAG QA stream: retrieve -> build messages -> stream deltas.

    kb=None 时为纯对话模式（前端未选择知识库）：跳过检索，hits 为空，
    直接以通用助手人设走 LLM 流式回答（2026-10-15）。

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

    # 2026-10-15 纯对话模式：kb=None（前端未选知识库）→ 跳过检索，
    # hits 为空直接走 LLM 对话（build_messages 空上下文即自由回答）。
    if chat_cfg is None:
        chat_cfg = load_chat_config(
            kb_db, user_id=user_id or "", team_name=team_name or "", is_sys_admin=is_sys_admin
        )
    if kb is None and kbs:
        # 2026-10-16 全库问答：不选知识库=检索全部可见知识库，按分数合并取 top
        yield {
            "type": "stage",
            "stage": "retrieval",
            "status": "running",
            "title": f"正在检索全部知识库（{len(kbs)} 个）…",
        }
        merged: list = []
        for k in kbs:
            cfg = config_from_kb(k, retrieval_overrides)
            merged.extend(hybrid_search(kb_db, k.id, question, query_embedding, cfg))
        merged.sort(key=lambda h: h.score or 0.0, reverse=True)
        top_n = 8
        if retrieval_overrides and retrieval_overrides.get("top_k") is not None:
            try:
                top_n = int(retrieval_overrides["top_k"])
            except (TypeError, ValueError):
                pass
        hits = merged[:top_n]
        yield {
            "type": "stage",
            "stage": "retrieval",
            "status": "done",
            "title": "检索完成",
            "summary": f"全库 {len(kbs)} 个知识库，命中 {len(hits)} 篇相关片段",
            "hits_count": len(hits),
        }
        yield {"type": "context", "hits": [h.__dict__ for h in hits]}
    elif kb is None:
        hits: list = []
        yield {
            "type": "stage",
            "stage": "retrieval",
            "status": "done",
            "title": "未选择知识库，直接问答",
            "summary": "纯对话模式（环境中暂无可用知识库）",
            "hits_count": 0,
        }
        yield {"type": "context", "hits": []}
    else:
        yield {"type": "stage", "stage": "retrieval", "status": "running", "title": "正在检索知识库…"}
        cfg = config_from_kb(kb, retrieval_overrides)
        hits = hybrid_search(kb_db, kb.id, question, query_embedding, cfg)
        yield {
            "type": "stage",
            "stage": "retrieval",
            "status": "done",
            "title": "检索完成",
            "summary": f"命中 {len(hits)} 篇相关片段",
            "hits_count": len(hits),
        }
        yield {"type": "context", "hits": [h.__dict__ for h in hits]}

    # 2026-10-16 LLM 使用看板埋点上下文：kb/文档取自本次问答命中
    _first = next(
        (
            h
            for h in hits
            if getattr(h, "document_id", None) or getattr(h, "document_title", None)
        ),
        None,
    )
    usage_ctx = {
        "kb_id": getattr(kb, "id", "") or "",
        "kb_name": getattr(kb, "name", "") or ("全部知识库" if kbs else ""),
        "doc_id": getattr(_first, "document_id", "") if _first is not None else "",
        "doc_name": getattr(_first, "document_title", "") if _first is not None else "",
        "operation": "agent" if agent_prompt else "qa",
    }

    if history:
        normalized: list[ChatMessage] = [
            m if isinstance(m, ChatMessage) else ChatMessage(role=m["role"], content=m["content"])
            for m in history
        ]
    else:
        normalized = []
    # 2026-10-07: 文档图片注入——命中 chunk 里的 kb-image:// 引用读回 data URL，
    # 与附件图片合并后随 user 消息走视觉模型（文档图片可理解/问答）
    # include_doc_images=False：本轮走「非视觉模型 + 图片描述」路径，命中片段里的
    # 文档图片同样不能直传（否则非视觉模型收到 image_url part 直接报错）。
    doc_images = _chunk_document_images(hits) if include_doc_images else []
    images = list(image_urls or []) + doc_images
    messages = build_messages(
        question, hits, normalized,
        extra_context=extra_context, agent_prompt=agent_prompt, image_urls=images,
        image_description=image_description,
    )
    client = ChatClient(chat_cfg)
    # 无工具：与接入前逐字一致的单轮流式
    if not tools:
        yield {"type": "stage", "stage": "generation", "status": "running", "title": "正在生成回答…"}
        yield from _stream_once(client, messages, usage_ctx, thinking=thinking)
        # 2026-10-12: 此前只发 running 从不发 done，前端步骤树的
        # 「正在生成回答…」永远转 loading（收到回复后仍显示）。结束补发 done。
        yield {"type": "stage", "stage": "generation", "status": "done", "title": "回答完成"}
        return
    max_rounds = max(1, int(max_tool_iterations or 1))
    # 2026-10-20 对齐 WeKnora 循环加固：
    # - empty_retries：空回答重试计数
    # - last_tool_sig / same_tool_rounds：卡死检测（连续相同工具调用）
    empty_retries = 0
    last_tool_sig = ""
    same_tool_rounds = 0
    for round_idx in range(max_rounds):
        tool_calls: list[dict] = []
        buffered: list[dict] = []  # 本轮流式文本（非最终轮不回传，避免前言污染答案）
        yield {
            "type": "stage",
            "stage": "generation",
            "status": "running",
            "title": f"正在生成回答…（第 {round_idx + 1} 轮）" if round_idx > 0 else "正在生成回答…",
        }
        for ev in client.stream_events(messages, tools=tools, usage_ctx=usage_ctx, thinking=thinking):
            etype = ev.get("type")
            if etype == "tool_call":
                tool_calls.append(ev)
            elif etype in ("delta", "thinking"):
                buffered.append(ev)
        if not tool_calls:
            # 2026-10-20 空内容重试（对齐 WeKnora maxEmptyResponseRetries）：
            # LLM 自然 stop 但未产出任何正文（thinking-only 循环/空响应）——
            # 追加 nudge 消息重试，最多 MAX_EMPTY_RETRIES 次，耗尽后走兜底文案。
            has_answer_text = any(
                ev.get("type") == "delta" and (ev.get("text") or "").strip()
                for ev in buffered
            )
            if not has_answer_text and empty_retries < MAX_EMPTY_RETRIES:
                empty_retries += 1
                logger.warning(
                    "agent round %d: empty answer, nudging LLM (retry %d/%d)",
                    round_idx + 1, empty_retries, MAX_EMPTY_RETRIES,
                )
                messages.append(ChatMessage(role="user", content=_EMPTY_NUDGE))
                continue
            for ev in buffered:
                yield ev
            break
        # 卡死检测（对齐 WeKnora maxRepeatedResponseRounds）：连续多轮发起
        # 完全相同的工具调用（同一工具+同参数）视为卡死，提前结束循环，
        # 避免空转消耗完 max_rounds 且用户久久等不到结果。
        tool_sig = json.dumps(
            [
                {
                    "name": tc.get("name") or "",
                    "arguments": (tc.get("arguments") or "")[:200],
                }
                for tc in tool_calls
            ],
            ensure_ascii=False,
        )
        if tool_sig and tool_sig == last_tool_sig:
            same_tool_rounds += 1
        else:
            same_tool_rounds = 0
        last_tool_sig = tool_sig
        if same_tool_rounds >= MAX_REPEATED_TOOL_ROUNDS:
            logger.warning(
                "agent round %d: stuck loop (same tool calls %d consecutive rounds), stopping",
                round_idx + 1, same_tool_rounds,
            )
            for ev in buffered:
                yield ev
            break
        # 动作开始：LLM 决定调用工具 → 即时透出（前端步骤树显示「正在调用」）
        yield {
            "type": "tool_call",
            "tool_calls": [
                {"id": tc.get("id") or f"call_{i}", "name": tc.get("name") or "", "arguments": tc.get("arguments") or ""}
                for i, tc in enumerate(tool_calls)
            ],
        }
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
        durations: list[float] = []
        for i, tc in enumerate(tool_calls):
            _t0 = time.time()
            result = (
                tool_executor(tc)
                if tool_executor
                else f"工具不可用: 未配置执行器（{tc.get('name') or '?'}）"
            )
            # 2026-10-20 工具输出钳制（对齐 WeKnora MaxToolOutputChars）：
            # 超长工具结果截断，防爆上下文/响应体积；截断标记保留在原位。
            if result and len(result) > MAX_TOOL_OUTPUT_CHARS:
                result = (
                    result[:MAX_TOOL_OUTPUT_CHARS]
                    + f"\n…[工具输出已截断：原 {len(result)} 字符，仅保留前 {MAX_TOOL_OUTPUT_CHARS} 字符]"
                )
            durations.append(round(time.time() - _t0, 2))
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
        yield {"type": "tool", "tool_calls": tool_calls, "results": results, "durations": durations}
        # 2026-10-20 上下文窗口管理（对齐 WeKnora manageContextWindow）：多轮工具
        # 循环把工具结果全量累积进 messages，长链路可能撑爆模型上下文。每轮结束
        # 粗估总 token，超过 MAX_CONTEXT_TOKENS 时压缩最早的 tool 消息（截短内容
        # 到尾部摘要），保留最近轮次完整结果。压缩失败/估算不可用时跳过（尽力而为）。
        try:
            from api.services.chunking import approx_token_count

            est_tokens = sum(approx_token_count(m.content or "") for m in messages)
            if est_tokens > MAX_CONTEXT_TOKENS:
                logger.warning(
                    "agent context window: est=%d > %d, compressing oldest tool messages",
                    est_tokens, MAX_CONTEXT_TOKENS,
                )
                _compressed = 0
                for _m in messages:
                    if _m.role != "tool":
                        continue
                    if approx_token_count(_m.content or "") <= 400:
                        continue
                    _keep = (_m.content or "")[:2000]
                    _tail = (_m.content or "")[-200:]
                    _m.content = _keep + "\n…[工具结果已压缩以控制上下文]…" + _tail
                    _compressed += 1
                    est_tokens = sum(approx_token_count(m.content or "") for m in messages)
                    if est_tokens <= MAX_CONTEXT_TOKENS:
                        break
                logger.warning("agent context window: compressed %d tool messages", _compressed)
        except Exception:  # noqa: BLE001 — 上下文压缩尽力而为，失败不阻断
            logger.debug("agent context compression skipped", exc_info=True)
    # 2026-10-12: 工具循环结束（无更多工具调用或达最大轮次）补发 done，
    # 否则前端步骤树「正在生成回答…」永远转 loading。
    yield {"type": "stage", "stage": "generation", "status": "done", "title": "回答完成"}


def _stream_once(client: ChatClient, messages: list[ChatMessage], usage_ctx: dict | None = None, thinking: bool | None = None) -> Iterator[dict]:
    """单轮流式：只透传 delta / thinking（done 由 answer_question 之前的循环收尾）。"""
    for ev in client.stream_events(messages, usage_ctx=usage_ctx, thinking=thinking):
        if ev.get("type") in ("delta", "thinking"):
            yield ev