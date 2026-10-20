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
    """无检索片段且无附件：使用通用助手人设（2026-10-15 纯对话模式改动）。

    此前人设是「知识库问答助手。若检索片段不足以回答…」；纯对话模式（未选
    知识库）下不注入检索片段，人设改为通用「智能问答助手」，信息不足时
    明确说明、不编造——服务于 RAG 零命中与不选知识库两种场景。
    """
    messages = build_messages("问题", [])
    assert messages[0].role == "system"
    assert "你是一个智能问答助手" in messages[0].content
    assert "信息不足时请明确说明" in messages[0].content


# ── 2026-10-20 对齐 WeKnora：提示词占位符 + 上下文模板 ─────────────────


def test_render_prompt_placeholders_basic() -> None:
    """{{query}}/{{contexts}}/{{language}}/时间占位符按字段替换。"""
    from api.services.chat import render_prompt_placeholders

    out = render_prompt_placeholders(
        "问：{{query}}\n材料：{{contexts}}\n语言：{{language}}\n{{current_time}} {{current_week}}",
        query="什么是数据中台",
        contexts="片段块",
        language="zh-CN",
        now=0,  # 1970-01-01 星期四（UTC 本地时区下仍为星期四/四）
    )
    assert "问：什么是数据中台" in out
    assert "材料：片段块" in out
    assert "语言：zh-CN" in out
    assert "{{current_time}}" not in out
    assert "{{current_week}}" not in out


def test_render_prompt_placeholders_keeps_unknown_tokens() -> None:
    """未识别的 {{...}} 原样保留（对齐 WeKnora 行为）。"""
    from api.services.chat import render_prompt_placeholders

    out = render_prompt_placeholders("{{unknown}} {{query}}", query="q")
    assert "{{unknown}}" in out
    assert "q" in out


def test_build_messages_context_template_renders_user_message() -> None:
    """对齐 WeKnora into_chat_message：context_template 决定 user 消息组织。"""
    messages = build_messages(
        "什么是数据中台？",
        [_hit()],
        context_template="参考材料：\n{{contexts}}\n\n问题：{{query}}",
    )
    user = messages[-1]
    assert user.role == "user"
    assert "参考材料：" in user.content
    assert "问题：什么是数据中台？" in user.content
    assert "search_results" in user.content  # 片段渲染进 user（模板声明位置）


def test_build_messages_default_prompt_renders_contexts_placeholder() -> None:
    """默认 SYSTEM_PROMPT 含 {{contexts}}：渲染后不残留占位符、片段不重复注入。"""
    from api.services.chat import SYSTEM_PROMPT

    messages = build_messages("问题", [_hit()], agent_prompt=SYSTEM_PROMPT)
    system = messages[0].content
    assert "{{contexts}}" not in system
    assert system.count("search_results") == 1  # 占位符注入一次，不重复拼尾部
    assert messages[-1].content == "问题"


def test_build_messages_custom_prompt_without_placeholder_appends_context() -> None:
    """自定义提示词不含 {{contexts}} 时保持旧行为：片段拼在 system 尾部。"""
    messages = build_messages("问题", [_hit()], agent_prompt="你是制度问答专家。")
    system = messages[0].content
    assert system.startswith("你是制度问答专家。")
    assert "search_results" in system
    assert "引用规范" in system  # 自动追加引用规范


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


def test_chat_client_404_becomes_actionable_message(monkeypatch) -> None:
    """2026-10-12: 网关用 404 表达「模型名不存在」（tokenerpgw/erp 实测
    model_not_found 回 404）。裸抛 httpx 异常会给用户一句无法定位的
    "Client error '404 Not Found' for url ..."，这里断言已转换为
    带模型名与接口地址的中文提示。"""

    import httpx

    captured: dict = {}

    class FakeResp:
        def __init__(self, *a, **kw):
            pass

        @property
        def status_code(self):
            return 404

        def close(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def build_request(self, method, url, json=None, headers=None):
            return {"method": method, "url": url, "json": json, "headers": headers}

        def send(self, req, stream=True):
            captured["url"] = req["url"]
            raise httpx.HTTPStatusError(
                "404", request=httpx.Request("POST", req["url"]), response=FakeResp()  # type: ignore[arg-type]
            )

    monkeypatch.setattr("httpx.Client", FakeClient)
    from api.services.chat import ChatClient, ChatConfig, ChatMessage

    client = ChatClient(
        ChatConfig(base_url="https://gw.example.com/erp/v1", api_key="sk-1", model="aliyun/x")
    )
    with pytest.raises(RuntimeError) as ei:
        list(client.stream_events([ChatMessage(role="user", content="hi")]))
    msg = str(ei.value)
    assert "404" in msg
    assert "aliyun/x" in msg  # 模型名出现在提示里
    assert "gw.example.com" in msg  # 接口地址出现在提示里
