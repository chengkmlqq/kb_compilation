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


def test_chat_client_thinking_payload(monkeypatch):
    captured: dict = {}
    _fake_httpx(monkeypatch, captured)
    client = ChatClient(ChatConfig(base_url="https://x/v1", api_key="", model="m"))
    list(client.stream_events([ChatMessage(role="user", content="hi")], thinking=True, top_p=0.9))
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert captured["json"]["top_p"] == 0.9
    # None thinking/top_p are omitted
    captured2: dict = {}
    _fake_httpx(monkeypatch, captured2)
    list(client.stream_events([ChatMessage(role="user", content="hi")]))
    assert "thinking" not in captured2["json"]
    assert "top_p" not in captured2["json"]