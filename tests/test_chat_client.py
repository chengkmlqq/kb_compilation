"""ChatClient behaviour: model-scoped custom headers and image content arrays.

The message serialization + headers are the two places where a model-scoped
custom_headers config and multimodal images actually reach the wire.
"""

from __future__ import annotations

from api.services.chat import ChatClient, ChatConfig, ChatMessage

import pytest  # noqa: F401


def _fake_httpx(monkeypatch, captured: dict):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.kw = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def close(self):
            pass

        def iter_lines(self):
            return ["data: [DONE]"]
            yield  # pragma: no cover

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
            captured["json"] = req["json"]
            captured["headers"] = req["headers"]
            return FakeResp()

        def stream(self, method, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResp()

    monkeypatch.setattr("httpx.Client", FakeClient)


def test_chat_client_sends_custom_headers(monkeypatch):
    captured: dict = {}
    _fake_httpx(monkeypatch, captured)
    client = ChatClient(
        ChatConfig(
            base_url="https://api.example.com/v1",
            api_key="sk-1",
            model="m1",
            custom_headers={"X-Api-Key": "abc", "X-Tenant": "t1"},
        )
    )
    list(client.stream_events([ChatMessage(role="user", content="hi")]))

    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-1"
    assert captured["headers"]["X-Api-Key"] == "abc"
    assert captured["headers"]["X-Tenant"] == "t1"


def test_chat_client_serializes_images_as_content_array(monkeypatch):
    captured: dict = {}
    _fake_httpx(monkeypatch, captured)
    client = ChatClient(ChatConfig(base_url="https://x/v1", api_key="", model="m"))
    list(
        client.stream_events(
            [ChatMessage(role="user", content="这是什么", images=["data:image/png;base64,AAAA"])]
        )
    )
    messages = captured["json"]["messages"]
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "这是什么"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    # plain messages stay string content
    captured2: dict = {}
    _fake_httpx(monkeypatch, captured2)
    list(client.stream_events([ChatMessage(role="user", content="hi")]))
    assert captured2["json"]["messages"][0]["content"] == "hi"


def test_chat_client_thinking_payload_propagation(monkeypatch):
    """2026-10-20 深度思考：thinking=True/False 写入请求 payload，None 不写。

    非 Agent 问答（qa.py）与 Agent 模式（agents.py）都经 answer_question
    透传 thinking 到 stream_events，此用例锁住 wire 层的三态语义。
    """
    captured: dict = {}

    def _mk_client():
        _fake_httpx(monkeypatch, captured)
        return ChatClient(ChatConfig(base_url="https://x/v1", api_key="", model="m"))

    # True -> {"type": "enabled"}
    list(_mk_client().stream_events([ChatMessage(role="user", content="hi")], thinking=True))
    assert captured["json"]["thinking"] == {"type": "enabled"}

    # False -> {"type": "disabled"}
    list(_mk_client().stream_events([ChatMessage(role="user", content="hi")], thinking=False))
    assert captured["json"]["thinking"] == {"type": "disabled"}

    # None -> 不写字段（沿用网关默认）
    list(_mk_client().stream_events([ChatMessage(role="user", content="hi")]))
    assert "thinking" not in captured["json"]


def test_chat_client_thinking_payload(monkeypatch):
    captured: dict = {}
    _fake_httpx(monkeypatch, captured)
    client = ChatClient(ChatConfig(base_url="https://x/v1", api_key="", model="m"))
    list(client.stream_events([ChatMessage(role="user", content="hi")], thinking=True, top_p=0.9))
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert captured["json"]["top_p"] == 0.9
    # False -> {"type": "disabled"}（2026-10-20 深度思考显式关闭）
    captured_false: dict = {}
    _fake_httpx(monkeypatch, captured_false)
    list(client.stream_events([ChatMessage(role="user", content="hi")], thinking=False))
    assert captured_false["json"]["thinking"] == {"type": "disabled"}
    # None thinking/top_p are omitted
    captured2: dict = {}
    _fake_httpx(monkeypatch, captured2)
    list(client.stream_events([ChatMessage(role="user", content="hi")]))
    assert "thinking" not in captured2["json"]
    assert "top_p" not in captured2["json"]