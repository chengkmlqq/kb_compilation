"""API tests for the model debug endpoint (multipart, per-type live probes).

POST /api/v1/models/{id}/debug now takes multipart form fields (input /
options / documents / file) and returns the WeKnora-parity envelope:
ok / elapsed_ms / request preview / raw_response / observations / error.
"""

from __future__ import annotations

import json
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.framework import TeamMember, User
from api.models.model import KbModel
from api.services.models import create_model

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.create_all(engine)
    db = Session()
    db.add_all(
        [
            User(id="u1", user_id="alice", user_name="Alice", state="1", default_team="T1"),
            TeamMember(member_id="m1", team_name="T1", user_id="alice", role_name="", state="1"),
        ]
    )
    db.commit()
    for t in ["chat", "embedding", "rerank", "vllm", "asr"]:
        create_model(
            db,
            caller_user_id="alice",
            caller_team_name="T1",
            is_sys_admin=False,
            payload={
                "scope": "personal",
                "name": f"m-{t}",
                "type": t,
                "provider": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test123",
                "dimension": 768 if t == "embedding" else None,
                "custom_headers": {"X-Api-Key": "hunter2"} if t == "chat" else None,
            },
        )
    db.commit()

    # fail-closed RBAC middleware: needs a middleware-decodable identity cookie
    # plus a sessionmaker/settings that resolve against the in-memory DB.
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    monkeypatch.setattr(
        "api.middleware.get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1")
    )
    app.dependency_overrides[get_db] = lambda: db
    try:
        from api.services.identity import Identity, encode_identity_cookie

        c = TestClient(app)
        c.cookies.set(
            "x-next-identity",
            encode_identity_cookie(
                Identity(user_id="alice", user_name="Alice", team_name="T1")
            ),
        )
        yield c
    finally:
        app.dependency_overrides.clear()
        db.query(KbModel).delete()
        db.query(TeamMember).delete()
        db.query(User).delete()
        db.commit()
        db.close()


def _model_id(db: Session, name: str) -> str:
    m = db.query(KbModel).filter_by(name=name).first()
    assert m is not None
    return m.id


# ---------------------------------------------------------------------------
# chat / vllm — ChatClient fakes
# ---------------------------------------------------------------------------


class FakeChatClient:
    def __init__(self, cfg):
        self.cfg = cfg
        FakeChatClient.last_cfg = cfg
        FakeChatClient.last_call = None

    def stream_events(
        self, messages, temperature=0.7, max_tokens=2048, top_p=None, thinking=None
    ):
        FakeChatClient.last_call = (messages, temperature, max_tokens, top_p, thinking)
        yield {"type": "thinking", "text": "思考中"}
        yield {"type": "delta", "text": "你好"}
        yield {"type": "done"}


def test_debug_chat_streams_with_options(client, monkeypatch):
    db = next(iter(app.dependency_overrides.values()))()
    monkeypatch.setattr("api.services.chat.ChatClient", FakeChatClient)
    r = client.post(
        f"/api/v1/models/{_model_id(db, 'm-chat')}/debug",
        data={
            "input": "你好",
            "options": json.dumps(
                {
                    "system_prompt": "你是测试助手",
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "max_tokens": 512,
                    "thinking": True,
                }
            ),
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["raw_response"]["content"] == "你好"
    assert data["raw_response"]["reasoning_content"] == "思考中"
    assert data["observations"]["stream"] is True
    assert data["observations"]["requested_thinking"] is True
    assert data["observations"]["reasoning_returned"] is True
    assert data["observations"]["reasoning_characters"] == 3
    assert data["observations"]["answer_characters"] == 2
    assert data["elapsed_ms"] >= 0
    # request preview carries no secret
    assert "api_key" not in json.dumps(data["request"])

    cfg = FakeChatClient.last_cfg
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "m-chat"
    assert cfg.custom_headers == {"X-Api-Key": "hunter2"}
    # request preview lists header NAMES only, never the secret value
    assert data["request"]["custom_header_names"] == ["X-Api-Key"]
    assert "hunter2" not in json.dumps(data)
    messages, temperature, max_tokens, top_p, thinking = FakeChatClient.last_call
    assert messages[0].role == "system"
    assert messages[0].content == "你是测试助手"
    assert messages[1].role == "user"
    assert messages[1].content == "你好"
    assert temperature == 0.3
    assert max_tokens == 512
    assert top_p == 0.9
    assert thinking is True


def test_debug_vllm_requires_image_and_passes_data_url(client, monkeypatch):
    db = next(iter(app.dependency_overrides.values()))()
    monkeypatch.setattr("api.services.chat.ChatClient", FakeChatClient)
    r = client.post(
        f"/api/v1/models/{_model_id(db, 'm-vllm')}/debug",
        data={"input": "这是什么"},
        files={"file": ("pic.jpg", b"fakeimgbytes", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True
    messages, _, _, _, _ = FakeChatClient.last_call
    assert messages[-1].images
    assert messages[-1].images[0].startswith("data:image/jpeg;base64,")

    # vllm without a file -> 400
    r = client.post(f"/api/v1/models/{_model_id(db, 'm-vllm')}/debug", data={"input": "x"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# embedding
# ---------------------------------------------------------------------------


class FakeEmbeddingClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def embed_query(self, text):
        return [0.1] * 768


def test_debug_embedding_dimension(client, monkeypatch):
    db = next(iter(app.dependency_overrides.values()))()
    monkeypatch.setattr("api.services.embedding.EmbeddingClient", FakeEmbeddingClient)
    r = client.post(
        f"/api/v1/models/{_model_id(db, 'm-embedding')}/debug", data={"input": "测试文本"}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["observations"]["dimension"] == 768


# ---------------------------------------------------------------------------
# rerank — real POST /rerank via httpx
# ---------------------------------------------------------------------------


class FakeResp:
    status_code = 200
    text = ""

    def json(self):
        return {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.5}]}


class FakeHttpxClient:
    def __init__(self, timeout=None):
        self.timeout = timeout
        FakeHttpxClient.last_url = None
        FakeHttpxClient.last_body = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        FakeHttpxClient.last_url = url
        FakeHttpxClient.last_body = json
        return FakeResp()


def test_debug_rerank_real_rerank(client, monkeypatch):
    db = next(iter(app.dependency_overrides.values()))()
    monkeypatch.setattr("httpx.Client", FakeHttpxClient)
    r = client.post(
        f"/api/v1/models/{_model_id(db, 'm-rerank')}/debug",
        data={"input": "查询", "documents": json.dumps(["文档A", "文档B"])},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["observations"]["result_count"] == 2
    assert FakeHttpxClient.last_url.endswith("/rerank")
    assert FakeHttpxClient.last_body["query"] == "查询"
    assert FakeHttpxClient.last_body["documents"] == ["文档A", "文档B"]
    assert FakeHttpxClient.last_body["model"] == "m-rerank"

    # rerank without documents -> 400
    r = client.post(f"/api/v1/models/{_model_id(db, 'm-rerank')}/debug", data={"input": "q"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# asr — real transcription via ASRClient
# ---------------------------------------------------------------------------


class FakeASRClient:
    def __init__(self, cfg):
        self.cfg = cfg
        FakeASRClient.last_file = None

    def transcribe(self, audio_bytes, filename):
        FakeASRClient.last_file = (audio_bytes, filename)
        return types.SimpleNamespace(text="转写结果文本", segments=[{"id": 0}, {"id": 1}])


def test_debug_asr_transcribes_audio(client, monkeypatch):
    db = next(iter(app.dependency_overrides.values()))()
    monkeypatch.setattr("api.services.asr.ASRClient", FakeASRClient)
    r = client.post(
        f"/api/v1/models/{_model_id(db, 'm-asr')}/debug",
        files={"file": ("speech.mp3", b"fakeaudio", "audio/mpeg")},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["observations"]["text_characters"] == 6
    assert data["observations"]["segment_count"] == 2
    assert data["raw_response"]["text"] == "转写结果文本"
    assert FakeASRClient.last_file == (b"fakeaudio", "speech.mp3")

    # asr without a file -> 400
    r = client.post(f"/api/v1/models/{_model_id(db, 'm-asr')}/debug", data={"input": "x"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# validation / auth
# ---------------------------------------------------------------------------


def test_debug_chat_real_endpoint_error_path(client):
    """No mock: real httpx connect to a closed port -> ok=false + error, not 500."""
    db = next(iter(app.dependency_overrides.values()))()
    m = db.query(KbModel).filter_by(name="m-chat").first()
    assert m is not None
    m.base_url = "http://127.0.0.1:1/v1"
    db.commit()
    r = client.post(f"/api/v1/models/{m.id}/debug", data={"input": "hi"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert data["error"]


def test_debug_validation_errors(client):
    db = next(iter(app.dependency_overrides.values()))()
    chat_id = _model_id(db, "m-chat")

    # chat without input -> 400
    r = client.post(f"/api/v1/models/{chat_id}/debug", data={"input": ""})
    assert r.status_code == 400

    # invalid options JSON -> 400
    r = client.post(
        f"/api/v1/models/{chat_id}/debug", data={"input": "hi", "options": "not-json"}
    )
    assert r.status_code == 400

    # temperature out of range -> 400
    r = client.post(
        f"/api/v1/models/{chat_id}/debug",
        data={"input": "hi", "options": json.dumps({"temperature": 3})},
    )
    assert r.status_code == 400

    # documents not a string array -> 400
    r = client.post(
        f"/api/v1/models/{chat_id}/debug",
        data={"input": "hi", "documents": json.dumps([1, 2])},
    )
    assert r.status_code == 400

    # input over 64KB -> 400
    r = client.post(f"/api/v1/models/{chat_id}/debug", data={"input": "x" * 70000})
    assert r.status_code == 400

    # unknown model -> 404
    r = client.post("/api/v1/models/nope/debug", data={"input": "hi"})
    assert r.status_code == 404


def test_debug_requires_login():
    c = TestClient(app)
    r = c.post("/api/v1/models/whatever/debug", data={"input": "hi"})
    assert r.status_code == 401
