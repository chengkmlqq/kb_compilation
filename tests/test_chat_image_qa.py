"""Image QA: attachment image storage, data-URL injection, upload gate.

Covers:
- service: image attachments stored with media_type=image + file_data,
  attachment_images() -> data URLs, attachment_contents() ignores images,
  build_messages() attaches images to the user message
- API gate: plain KB session rejects images, agent without the
  image_upload_enabled config rejects, agent with it accepts (200)
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
from api.models.knowledge import KbAgent
from api.services import chat_sessions as svc

# StaticPool: :memory: with the default pool is a fresh empty DB per connection,
# so middleware queries (modo_menu etc.) would fail on their own connection.
_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=_ENGINE)
    session = sessionmaker(bind=_ENGINE, expire_on_commit=False)()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# service layer
# ---------------------------------------------------------------------------


def test_image_attachment_storage_and_urls(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    svc.create_attachment(
        db, "user1", s["id"], "pic.png", ".png", 4, "",
        media_type="image", file_data=b"\x89PNG",
    )
    atts = svc.list_attachments(db, "user1", s["id"])
    assert atts[0]["media_type"] == "image"

    urls = svc.attachment_images(db, s["id"])
    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")

    # text attachments do not appear as images
    svc.create_attachment(db, "user1", s["id"], "a.txt", ".txt", 3, "hello")
    assert len(svc.attachment_images(db, s["id"])) == 1


def test_attachment_contents_ignores_images(db) -> None:
    s = svc.create_session(db, "user1", "kb1", None)
    svc.create_attachment(
        db, "user1", s["id"], "pic.png", ".png", 4, "",
        media_type="image", file_data=b"\x89PNG",
    )
    svc.create_attachment(db, "user1", s["id"], "a.txt", ".txt", 3, "hello world")
    ctx = svc.attachment_contents(db, s["id"])
    assert "hello world" in ctx
    assert "pic.png" not in ctx


def test_build_messages_attaches_image_urls(db) -> None:
    from api.services.chat import build_messages

    msgs = build_messages(
        "图里是什么",
        hits=[],
        image_urls=["data:image/png;base64,AAAA"],
    )
    assert msgs[-1].role == "user"
    assert msgs[-1].images == ["data:image/png;base64,AAAA"]
    # without images the field stays None (string content serialization)
    msgs2 = build_messages("普通问题", hits=[])
    assert msgs2[-1].images is None


# ---------------------------------------------------------------------------
# API layer — upload gate
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, db):
    # StaticPool shared engine across tests — clear rows from previous tests
    from api.models import chat_session as cs_mod

    db.query(KbAgent).delete()
    db.query(TeamMember).delete()
    db.query(User).delete()
    db.query(cs_mod.ChatSession).delete()
    db.query(cs_mod.ChatAttachment).delete()
    db.commit()
    db.add_all(
        [
            User(id="u1", user_id="alice", user_name="Alice", state="1", default_team="T1"),
            TeamMember(member_id="m1", team_name="T1", user_id="alice", role_name="", state="1"),
        ]
    )
    db.commit()

    def _make_agent(name: str, image_upload_enabled: bool) -> str:
        agent = KbAgent(
            id=name,
            name=name,
            config={"agent_mode": "quick-answer", "image_upload_enabled": image_upload_enabled},
            state="1",
        )
        db.add(agent)
        return agent.id

    _make_agent("agent-imgs", True)
    _make_agent("agent-noimgs", False)
    db.commit()

    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    monkeypatch.setattr(
        "api.middleware.get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u1")
    )
    app.dependency_overrides[get_db] = lambda: db
    # docreader 解析不依赖真实库：文档附件走 mock
    monkeypatch.setattr(
        "api.services.ingest.parse_document",
        lambda name, ext, data: f"parsed-{name}",
    )
    try:
        from api.services.identity import Identity, encode_identity_cookie

        c = TestClient(app)
        c.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id="alice", user_name="Alice", team_name="T1")),
        )
        yield c
    finally:
        app.dependency_overrides.clear()


def _session_id(client, db, agent_id=None) -> str:
    if agent_id:
        s = svc.create_agent_session(db, "alice", agent_id)
        return s["id"]
    s = svc.create_session(db, "alice", "kb1", None)
    return s["id"]


def test_upload_image_plain_session_allowed(client, db) -> None:
    """2026-10-18 对齐 WeKnora（95f51a6）：普通会话（无 agent）图片放行——
    非 Agent 图片问答由平台视觉模型兜底（image_qa.fallback_enabled(None)=True）。
    原名 test_upload_image_plain_session_rejected（旧行为：普通会话拒绝图片）。"""
    sid = _session_id(client, db)
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["data"]["media_type"] == "image"


def test_bind_agent_to_plain_session_then_upload_image_accepted(client, db) -> None:
    """2026-10-15 修复：前端选智能体后复用已有普通会话传图——先 PUT 绑定 agent
    再上传即通过图片门禁（此前按 session.agent_id 校验被拒）。"""
    sid = _session_id(client, db)  # 普通会话，agent_id 为空
    # 绑定智能体（开启图片上传的 agent-imgs）
    r = client.put(f"/api/v1/sessions/{sid}", json={"agent_id": "agent-imgs"})
    assert r.status_code == 200
    assert r.json()["data"]["agent_id"] == "agent-imgs"

    r2 = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["media_type"] == "image"

    # 解绑回普通会话后：2026-10-18 起普通会话图片放行（对齐 WeKnora），仍可上传
    r3 = client.put(f"/api/v1/sessions/{sid}", json={"agent_id": ""})
    assert r3.status_code == 200
    assert r3.json()["data"]["agent_id"] is None
    r4 = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r4.status_code == 200
    assert r4.json()["data"]["media_type"] == "image"


def test_bind_agent_to_session_missing_agent_rejected(client, db) -> None:
    """绑定不存在的智能体 → 400（避免绑定后图片门禁报「未知智能体」）。"""
    sid = _session_id(client, db)
    r = client.put(f"/api/v1/sessions/{sid}", json={"agent_id": "ghost-agent"})
    assert r.status_code == 400
    assert "智能体不存在" in r.json()["detail"]


def test_upload_image_agent_without_gate_rejected(client, db) -> None:
    sid = _session_id(client, db, agent_id="agent-noimgs")
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 400
    assert "未开启图片上传" in r.json()["detail"]


def test_upload_image_agent_with_gate_accepted(client, db) -> None:
    sid = _session_id(client, db, agent_id="agent-imgs")
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["media_type"] == "image"

    # document attachment still lands as text
    r2 = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("doc.md", b"# hello", "text/markdown")},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["media_type"] == "text"

    listed = client.get(f"/api/v1/sessions/{sid}/attachments").json()["data"]["items"]
    assert {a["media_type"] for a in listed} == {"image", "text"}


# ---------------------------------------------------------------------------
# 2026-10-18 对齐 WeKnora：当轮图片 / 图片描述回退 / 对象存储
# ---------------------------------------------------------------------------


def _cfg(**kw):
    from api.services.chat import ChatConfig

    return ChatConfig(base_url="http://vision.local/v1", api_key="k", model="m", **kw)


def test_attachment_image_data_urls_only_current_turn(db) -> None:
    """只注入当轮图片 id（对齐 WeKnora attachment_ids），不再重发会话内历史图片。"""
    s = svc.create_session(db, "user1", "kb1", None)
    a1 = svc.create_attachment(
        db, "user1", s["id"], "old.png", ".png", 8, "", media_type="image", file_data=b"\x89PNGold"
    )
    a2 = svc.create_attachment(
        db, "user1", s["id"], "new.jpg", ".jpg", 8, "", media_type="image", file_data=b"\xff\xd8newimg"
    )
    assert svc.attachment_image_ids(db, s["id"]) == [a1["id"], a2["id"]]

    scoped = svc.attachment_image_data_urls(db, s["id"], [a2["id"]])
    assert len(scoped) == 1
    assert scoped[0].startswith("data:image/jpeg;base64,")
    # 会话级兜底仍返回全部（仅旧客户端走这条）
    assert len(svc.attachment_images(db, s["id"])) == 2
    # 其他会话的 id 不允许注入
    s2 = svc.create_session(db, "user1", "kb1", None)
    assert svc.attachment_image_data_urls(db, s2["id"], [a2["id"]]) == []
    assert svc.attachment_image_data_urls(db, s["id"], []) == []


def test_image_caption_history_roundtrip(db) -> None:
    """图片描述写附件 caption，历史轮次以「用户上传图片内容」回带（不回传图片本体）。"""
    s = svc.create_session(db, "user1", "kb1", None)
    a = svc.create_attachment(
        db, "user1", s["id"], "chart.png", ".png", 4, "", media_type="image", file_data=b"\x89PNG"
    )
    svc.set_attachment_caption(db, a["id"], "柱状图：2025 年销量逐季上升")
    svc.append_message(db, "user1", s["id"], "user", "帮我看看这张图", images=[a["id"]])
    svc.append_message(db, "user1", s["id"], "assistant", "图上显示逐季上升")

    msgs = svc.get_context_messages(db, s["id"], with_image_captions=True)
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert "[用户上传图片内容]" in user_msg["content"]
    assert "柱状图" in user_msg["content"]
    assert user_msg["images"] == []

    # 默认不注入（既有调用方行为不变）
    default_msgs = svc.get_context_messages(db, s["id"])
    assert "[用户上传图片内容]" not in next(m for m in default_msgs if m["role"] == "user")["content"]


def test_store_attachment_image_object_storage(monkeypatch) -> None:
    """MinIO 已配置时图片落对象存储；未配置返回空串由调用方回落 DB 列。"""
    from api.services import storage as storage_mod

    calls: dict = {}

    def _fake_put(path, data, content_type=None):  # noqa: ANN001
        calls.update(path=path, size=len(data), content_type=content_type)
        return path

    monkeypatch.setattr(storage_mod, "remote_enabled", lambda: True)
    monkeypatch.setattr(storage_mod, "resolve_new_path", lambda key, **kw: "minio://kb/" + key)
    monkeypatch.setattr(storage_mod, "put_bytes", _fake_put)

    key = svc.store_attachment_image("sess1", "att1", b"\x89PNG", ".png")
    assert key == "minio://kb/chat_images/sess1/att1.png"
    assert calls == {"path": key, "size": 4, "content_type": "image/png"}

    monkeypatch.setattr(storage_mod, "remote_enabled", lambda: False)
    assert svc.store_attachment_image("sess1", "att1", b"\x89PNG", ".png") == ""


def test_attachment_image_bytes_prefers_object_storage(db, monkeypatch) -> None:
    """object_key 优先于 file_data（新行落存储、历史行仍在列，两种都要能读）。"""
    import base64

    from api.services import storage as storage_mod

    s = svc.create_session(db, "user1", "kb1", None)
    a = svc.create_attachment(
        db, "user1", s["id"], "pic.png", ".png", 5, "",
        media_type="image", object_key="minio://kb/chat_images/s/pic.png",
    )
    monkeypatch.setattr(storage_mod, "get_bytes", lambda p: b"BYTES")
    urls = svc.attachment_image_data_urls(db, s["id"], [a["id"]])
    assert urls == ["data:image/png;base64," + base64.b64encode(b"BYTES").decode()]


def test_upload_image_goes_to_object_storage(client, db, monkeypatch) -> None:
    from api.models import chat_session as cs_mod
    from api.services import storage as storage_mod

    stored: dict = {}
    monkeypatch.setattr(storage_mod, "remote_enabled", lambda: True)
    monkeypatch.setattr(storage_mod, "resolve_new_path", lambda key, **kw: "minio://kb/" + key)
    monkeypatch.setattr(
        storage_mod,
        "put_bytes",
        lambda path, data, content_type=None: stored.update(path=path, size=len(data)) or path,
    )

    sid = _session_id(client, db, agent_id="agent-imgs")
    r = client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("pic.png", b"\x89PNGdata", "image/png")},
    )
    assert r.status_code == 200
    att_id = r.json()["data"]["id"]
    assert stored["path"] == f"minio://kb/chat_images/{sid}/{att_id}.png"

    row = db.get(cs_mod.ChatAttachment, att_id)
    assert row is not None
    assert row.object_key == stored["path"]
    assert row.file_data is None  # 字节已落存储，不再进 DB


def test_plan_vision_when_model_supports_images() -> None:
    from api.services import image_qa

    plan = image_qa.plan_image_input(
        None, image_urls=["data:image/png;base64,A"], question="这是什么", chat_cfg=_cfg(supports_vision=True)
    )
    assert plan.mode == "vision"
    assert plan.images == ["data:image/png;base64,A"]
    assert plan.description == ""


def test_plan_none_without_images() -> None:
    from api.services import image_qa

    plan = image_qa.plan_image_input(None, image_urls=[], question="q", chat_cfg=_cfg(supports_vision=False))
    assert plan.mode == "none"


def test_plan_describe_fallback_for_non_vision_model(monkeypatch) -> None:
    """非视觉模型：视觉模型生成描述 → 文本注入（对齐 WeKnora image_description）。"""
    from api.services import image_qa

    monkeypatch.setattr(image_qa, "resolve_vision_config", lambda db, **kw: _cfg(supports_vision=True))
    monkeypatch.setattr(
        image_qa, "describe_images", lambda cfg, images, question, **kw: ["一只橘猫坐在垫子上"]
    )
    plan = image_qa.plan_image_input(
        None, image_urls=["data:image/png;base64,A"], question="图里是什么", chat_cfg=_cfg(supports_vision=False)
    )
    assert plan.mode == "describe"
    assert plan.captions == ["一只橘猫坐在垫子上"]
    assert plan.description == "一只橘猫坐在垫子上"
    assert plan.error == ""


def test_plan_describe_for_oip_protocol(monkeypatch) -> None:
    """OIP(/v2) 无多模态绑定：即便模型标了支持视觉也必须走描述回退。"""
    from api.services import image_qa

    monkeypatch.setattr(image_qa, "resolve_vision_config", lambda db, **kw: _cfg(supports_vision=True))
    monkeypatch.setattr(image_qa, "describe_images", lambda cfg, images, question, **kw: ["描述"])
    plan = image_qa.plan_image_input(
        None,
        image_urls=["data:image/png;base64,A"],
        question="q",
        chat_cfg=_cfg(supports_vision=True, interface_type="oip"),
    )
    assert plan.mode == "describe"
    assert plan.description == "描述"


def test_plan_reject_when_no_vision_model(monkeypatch) -> None:
    from api.services import image_qa, models as models_mod

    monkeypatch.setattr(models_mod, "resolve_vision_model_config", lambda db, **kw: None)
    plan = image_qa.plan_image_input(
        None, image_urls=["data:image/png;base64,A"], question="q", chat_cfg=_cfg(supports_vision=False)
    )
    assert plan.mode == "reject"
    assert plan.error == image_qa.NO_VISION_MESSAGE


def test_agent_image_understanding_gates_fallback(monkeypatch) -> None:
    """智能体未开「附件图片理解」且未配 vlm_model_id → 不做视觉回退（对齐 WeKnora 门禁）。"""
    from api.services import image_qa, models as models_mod
    from api.services.agents import AgentConfig

    calls: list = []
    monkeypatch.setattr(
        models_mod, "resolve_vision_model_config", lambda db, **kw: calls.append(1) or None
    )

    plan = image_qa.plan_image_input(
        None,
        image_urls=["u"],
        question="q",
        chat_cfg=_cfg(supports_vision=False),
        agent_cfg=AgentConfig(),
    )
    assert plan.mode == "reject"
    assert calls == []  # 门禁关闭时不解析视觉模型

    plan2 = image_qa.plan_image_input(
        None,
        image_urls=["u"],
        question="q",
        chat_cfg=_cfg(supports_vision=False),
        agent_cfg=AgentConfig(attachment_image_understanding=True),
    )
    assert plan2.mode == "reject"
    assert calls == [1]  # 开启后才尝试解析（此处置空 → 仍拒绝）


def test_resolve_vision_config_uses_vision_model(monkeypatch) -> None:
    from api.services import image_qa, models as models_mod

    monkeypatch.setattr(
        models_mod,
        "resolve_vision_model_config",
        lambda db, **kw: {
            "base_url": "http://vision.local/v1",
            "api_key": "k",
            "model": "qwen-vl",
            "interface_type": "openai",
        },
    )
    monkeypatch.setattr(image_qa, "describe_images", lambda cfg, images, question, **kw: ["猫"])
    cfg = image_qa.resolve_vision_config(None)
    assert cfg is not None
    assert cfg.model == "qwen-vl"
    assert cfg.supports_vision is True


def test_build_messages_injects_image_description() -> None:
    from api.services.chat import build_messages

    msgs = build_messages("图里是什么", hits=[], image_description="一只猫坐在垫子上")
    assert msgs[-1].role == "user"
    assert "[用户上传图片内容]" in msgs[-1].content
    assert msgs[-1].content.endswith("一只猫坐在垫子上")
    assert msgs[-1].images is None


def test_answer_question_describe_mode_skips_all_images(monkeypatch) -> None:
    """describe 路径下用户图片与文档图片都不直传（非视觉模型收到 image part 会报错）。"""
    from api.services import chat as chat_mod

    captured: dict = {}

    class FakeClient:
        def __init__(self, cfg):  # noqa: ANN001
            captured["cfg"] = cfg

        def stream_events(self, messages, **kw):  # noqa: ANN003
            captured["messages"] = messages
            yield {"type": "delta", "text": "ok"}
            yield {"type": "done"}

    monkeypatch.setattr(chat_mod, "ChatClient", FakeClient)
    monkeypatch.setattr(
        chat_mod, "_chunk_document_images", lambda hits, limit=4: ["data:image/png;base64,DOC"]
    )
    base = dict(kb_db=None, kb=None, question="看图", query_embedding=None,
                chat_cfg=chat_mod.ChatConfig(base_url="http://x/v1", api_key="k", model="m"))

    # describe：不直传任何图片，描述走文本
    list(
        chat_mod.answer_question(
            **base, image_description="一只猫", include_doc_images=False
        )
    )
    last = captured["messages"][-1]
    assert last.images is None
    assert "一只猫" in last.content

    # vision（默认）：用户图片 + 命中片段里的文档图片照旧直传
    list(
        chat_mod.answer_question(
            **base, image_urls=["data:image/png;base64,U"]
        )
    )
    last2 = captured["messages"][-1]
    assert last2.images == ["data:image/png;base64,U", "data:image/png;base64,DOC"]