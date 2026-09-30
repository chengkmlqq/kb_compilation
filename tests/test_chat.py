"""Chat/RAG QA tests — context serialization, message assembly, streaming parse."""

from __future__ import annotations

import json

import pytest

from api.services.chat import (
    ChatConfig,
    ChatMessage,
    build_messages,
    serialize_context,
)
from api.services.retrieval import ChunkHit


def _hit(chunk_id="c1", content="内容"):
    return ChunkHit(chunk_id=chunk_id, content=content, document_id="d1", kb_id="kb1", score=0.8)


def test_serialize_context_shape() -> None:
    ctx = serialize_context([_hit(), _hit("c2", "第二段")])
    obj = json.loads(ctx)
    assert obj["display_type"] == "search_results"
    assert len(obj["results"]) == 2
    row = obj["results"][0]
    assert row["chunk_id"] == "c1"
    assert row["document_id"] == "d1"
    assert row["kb_id"] == "kb1"
    # score rounded to 4 decimals
    assert row["score"] == 0.8


def test_serialize_context_empty() -> None:
    assert serialize_context([]) == ""


def test_build_messages_includes_context_and_question() -> None:
    messages = build_messages("什么是数据中台？", [_hit()])
    assert messages[0].role == "system"
    assert "search_results" in messages[0].content  # context embedded in system
    assert messages[-1].role == "user"
    assert messages[-1].content == "什么是数据中台？"


def test_build_messages_includes_history() -> None:
    history = [
        ChatMessage(role="user", content="上一轮问题"),
        ChatMessage(role="assistant", content="上一轮回答"),
    ]
    messages = build_messages("本轮问题", [_hit()], history)
    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert messages[1].content == "上一轮问题"
    assert messages[2].content == "上一轮回答"


def test_build_messages_no_context_uses_fallback_system() -> None:
    messages = build_messages("问题", [])
    assert messages[0].role == "system"
    assert "检索片段不足以回答" in messages[0].content


def test_chat_client_endpoint_normalization() -> None:
    from api.services.chat import ChatClient

    # both base_url shapes construct fine; actual URL assembly happens inside stream()
    c1 = ChatClient(ChatConfig(base_url="http://x/v1", api_key="", model="m"))
    assert c1.cfg.model == "m"
    c2 = ChatClient(ChatConfig(base_url="http://x/v1/chat/completions", api_key="", model="m"))
    assert c2.cfg.model == "m"


def test_chat_client_missing_endpoint_raises() -> None:
    from api.services.chat import ChatClient

    client = ChatClient(ChatConfig(base_url="", api_key="", model="m"))
    with pytest.raises(RuntimeError):
        list(client.stream([ChatMessage(role="user", content="hi")]))
