"""Scoped model registry service tests — visibility, permissions, secrets.

Covers the business scoping rule (personal=owner only, team=team members,
system=admins only), CRUD permission gates, AES secret encryption +
masking conventions, and the runtime default resolution chain.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import TeamMember, User, UserRole, UserRoleRela
from api.models.model import KbModel
from api.services.models import (
    create_model,
    delete_model,
    get_model,
    is_admin,
    list_visible_models,
    mask_api_key,
    resolve_model_config,
    set_default,
    update_model,
)


@pytest.fixture()
def model_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    # alice: normal user of team T1; bob: admin; carol: normal user of team T2
    db.add_all(
        [
            User(id="u1", user_id="alice", user_name="Alice", state="1", default_team="T1"),
            User(id="u2", user_id="bob", user_name="Bob", state="1", default_team="T1"),
            User(id="u3", user_id="carol", user_name="Carol", state="1", default_team="T2"),
            UserRole(role_id="r1", role_name="admin", role_type="system", state="1"),
            UserRoleRela(rela_id="x1", role_id="r1", user_id="bob"),
            TeamMember(member_id="m1", team_name="T1", user_id="alice", role_name="", state="1"),
            TeamMember(member_id="m2", team_name="T1", user_id="bob", role_name="admin", state="1"),
            TeamMember(member_id="m3", team_name="T2", user_id="carol", role_name="", state="1"),
        ]
    )
    db.commit()
    yield db
    db.close()


def _create(db, scope, **kw):
    return create_model(
        db,
        caller_user_id=kw.pop("caller_user_id", "alice"),
        caller_team_name=kw.pop("caller_team_name", "T1"),
        is_sys_admin=kw.pop("is_sys_admin", False),
        payload={
            "scope": scope,
            "name": kw.pop("name", "deepseek-v4-pro"),
            "type": kw.pop("type", "chat"),
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test-123456",
            **kw,
        },
    )


# ---------------------------------------------------------------------------
# is_admin
# ---------------------------------------------------------------------------


def test_is_admin_by_role_and_team_member(model_db):
    assert is_admin(model_db, "bob") is True  # via user_role_rela role_name=admin
    assert is_admin(model_db, "alice") is False
    assert is_admin(model_db, "carol") is False
    assert is_admin(model_db, "") is False


# ---------------------------------------------------------------------------
# create + scope ownership
# ---------------------------------------------------------------------------


def test_create_personal_sets_owner(model_db):
    item = _create(model_db, "personal")
    assert item["scope"] == "personal"
    assert item["owner_user_id"] == "alice"
    assert item["owner_team_name"] == ""
    row = get_model(model_db, item["id"])
    assert row is not None
    assert row.state == "1"


def test_create_team_requires_matching_team(model_db):
    item = _create(model_db, "team", owner_team_name="T1")
    assert item["scope"] == "team"
    assert item["owner_team_name"] == "T1"
    assert item["owner_user_id"] == ""
    # wrong team -> PermissionError
    with pytest.raises(PermissionError):
        _create(model_db, "team", owner_team_name="T2")


def test_create_system_requires_admin(model_db):
    with pytest.raises(PermissionError):
        _create(model_db, "system", caller_user_id="alice")
    item = _create(model_db, "system", caller_user_id="bob", is_sys_admin=True)
    assert item["scope"] == "system"
    assert item["owner_user_id"] == ""


def test_create_invalid_scope_and_type(model_db):
    with pytest.raises(ValueError):
        _create(model_db, "banana")
    with pytest.raises(ValueError):
        _create(model_db, "personal", type="vision")  # 不在 5 类型内


def test_create_all_five_types(model_db):
    """WeKnora 对齐：chat/embedding/rerank/vllm/asr 五种类型均可创建。"""
    for t in ("chat", "embedding", "rerank", "vllm", "asr"):
        m = _create(model_db, "personal", type=t, name=f"模型-{t}")
        assert m["type"] == t


def test_create_with_concurrency_and_thinking(model_db):
    m = _create(
        model_db,
        "personal",
        type="chat",
        max_concurrency=8,
        thinking_control="auto",
    )
    assert m["max_concurrency"] == 8
    assert m["thinking_control"] == "auto"
    # 0/空 → 不落库（None）
    m2 = _create(model_db, "personal", type="chat", max_concurrency=0)
    assert m2["max_concurrency"] is None


def test_copy_model(model_db):
    from api.services.models import copy_model

    orig = _create(model_db, "personal", type="chat", name="deepseek-v4-pro")
    clone = copy_model(
        model_db,
        orig["id"],
        caller_user_id="alice",
        caller_team_name="",
        is_sys_admin=False,
    )
    # name 是调 API 时的 model id，复制品必须保持不变（否则测试/调试
    # 会拿 `xxx-copy` 去请求供应商 → Model Not Found）；-copy 后缀落在
    # display_name（显示名）上用于区分。
    assert clone["name"] == "deepseek-v4-pro"
    assert clone["display_name"] == "deepseek-v4-pro-copy"
    assert clone["type"] == "chat"
    assert clone["is_default"] is False
    # 复制第二次 → 显示名去重递增
    clone2 = copy_model(
        model_db,
        orig["id"],
        caller_user_id="alice",
        caller_team_name="",
        is_sys_admin=False,
    )
    assert clone2["name"] == "deepseek-v4-pro"
    assert clone2["display_name"] == "deepseek-v4-pro-copy 2"
    # 非 owner 复制 → 无权
    with pytest.raises(PermissionError):
        copy_model(
            model_db,
            orig["id"],
            caller_user_id="carol",
            caller_team_name="",
            is_sys_admin=False,
        )


# ---------------------------------------------------------------------------
# visibility
# ---------------------------------------------------------------------------


def test_list_visibility_rules(model_db):
    _create(model_db, "personal", name="m-alice")
    _create(model_db, "team", name="m-team", owner_team_name="T1")
    _create(model_db, "system", name="m-sys", caller_user_id="bob", is_sys_admin=True)
    # alice (non-admin): own personal + her team, NOT system
    alice_items = list_visible_models(model_db, "alice", "T1", False)
    names = {i["name"] for i in alice_items}
    assert {"m-alice", "m-team"} <= names
    assert "m-sys" not in names
    # bob (admin): personal(none) + team T1 + system
    bob_items = list_visible_models(model_db, "bob", "T1", True)
    bob_names = {i["name"] for i in bob_items}
    assert "m-team" in bob_names and "m-sys" in bob_names
    # carol (T2): nothing shared with T1
    carol_items = list_visible_models(model_db, "carol", "T2", False)
    assert all(i["name"] not in ("m-team", "m-sys") for i in carol_items)


def test_list_type_filter(model_db):
    _create(model_db, "personal", name="m-chat", type="chat")
    _create(model_db, "personal", name="m-emb", type="embedding")
    chat_items = list_visible_models(model_db, "alice", "T1", False, model_type="chat")
    assert [i["name"] for i in chat_items] == ["m-chat"]
    emb_items = list_visible_models(model_db, "alice", "T1", False, scope="personal")
    assert {i["name"] for i in emb_items} == {"m-chat", "m-emb"}


# ---------------------------------------------------------------------------
# update / delete permissions
# ---------------------------------------------------------------------------


def test_update_requires_owner(model_db):
    item = _create(model_db, "personal")
    # another user cannot update
    with pytest.raises(PermissionError):
        update_model(
            model_db,
            item["id"],
            caller_user_id="carol",
            caller_team_name="T2",
            is_sys_admin=False,
            payload={"display_name": "carol's"},
        )
    updated = update_model(
        model_db,
        item["id"],
        caller_user_id="alice",
        caller_team_name="T1",
        is_sys_admin=False,
        payload={"display_name": "Alice 的模型", "base_url": "https://new.example.org/v1"},
    )
    assert updated["display_name"] == "Alice 的模型"
    assert updated["base_url"] == "https://new.example.org/v1"


def test_delete_requires_owner(model_db):
    item = _create(model_db, "personal")
    with pytest.raises(PermissionError):
        delete_model(
            model_db, item["id"], caller_user_id="carol", caller_team_name="T2", is_sys_admin=False
        )
    delete_model(
        model_db, item["id"], caller_user_id="alice", caller_team_name="T1", is_sys_admin=False
    )
    assert get_model(model_db, item["id"]) is None  # soft-deleted


def test_team_update_by_member(model_db):
    item = _create(model_db, "team", owner_team_name="T1")
    # carol from another team cannot manage it
    with pytest.raises(PermissionError):
        update_model(
            model_db,
            item["id"],
            caller_user_id="carol",
            caller_team_name="T2",
            is_sys_admin=False,
            payload={"display_name": "x"},
        )
    # alice (same team) can
    updated = update_model(
        model_db,
        item["id"],
        caller_user_id="alice",
        caller_team_name="T1",
        is_sys_admin=False,
        payload={"display_name": "团队模型"},
    )
    assert updated["display_name"] == "团队模型"


def test_system_update_admin_only(model_db):
    item = _create(model_db, "system", caller_user_id="bob", is_sys_admin=True)
    with pytest.raises(PermissionError):
        update_model(
            model_db,
            item["id"],
            caller_user_id="alice",
            caller_team_name="T1",
            is_sys_admin=False,
            payload={"display_name": "x"},
        )
    updated = update_model(
        model_db,
        item["id"],
        caller_user_id="bob",
        caller_team_name="T1",
        is_sys_admin=True,
        payload={"display_name": "系统模型"},
    )
    assert updated["display_name"] == "系统模型"


# ---------------------------------------------------------------------------
# secrets (AES at rest, masked on read, ****-keep semantics)
# ---------------------------------------------------------------------------


def test_api_key_encrypted_at_rest_and_masked(model_db):
    item = _create(model_db, "personal", api_key="sk-super-secret-99")
    assert "sk-super-secret-99" not in item["api_key_masked"]
    # masked form never equals the plaintext
    assert item["api_key_masked"] != "sk-super-secret-99"
    assert item["api_key_configured"] is True
    # DB stores ciphertext, not plaintext
    row = get_model(model_db, item["id"])
    assert row.api_key != "sk-super-secret-99"
    assert "secret" not in (row.api_key or "")
    # mask_api_key accepts the stored (encrypted) value
    assert mask_api_key(row.api_key) == item["api_key_masked"]


def test_update_masked_keeps_existing(model_db):
    item = _create(model_db, "personal", api_key="sk-original-abc")
    updated = update_model(
        model_db,
        item["id"],
        caller_user_id="alice",
        caller_team_name="T1",
        is_sys_admin=False,
        payload={"api_key": "****(masked)"},
    )
    assert updated["api_key_configured"] is True
    # empty string clears the key
    cleared = update_model(
        model_db,
        item["id"],
        caller_user_id="alice",
        caller_team_name="T1",
        is_sys_admin=False,
        payload={"api_key": ""},
    )
    assert cleared["api_key_configured"] is False


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------


def test_single_default_per_scope_type(model_db):
    a = _create(model_db, "personal", name="a", is_default=True)
    b = _create(model_db, "personal", name="b", is_default=True)
    assert get_model(model_db, a["id"]).is_default is False
    assert get_model(model_db, b["id"]).is_default is True
    # set_default flips
    set_default(
        model_db, a["id"], caller_user_id="alice", caller_team_name="T1", is_sys_admin=False
    )
    assert get_model(model_db, a["id"]).is_default is True
    assert get_model(model_db, b["id"]).is_default is False


def test_resolve_chain_personal_team_system(model_db):
    _create(model_db, "system", name="sys-chat", is_default=True, caller_user_id="bob", is_sys_admin=True)
    # a logged-in non-admin with no personal/team model does NOT get the
    # admin-only system model (falls back to legacy/env -> None here)
    resolved = resolve_model_config(model_db, "chat", caller_user_id="carol", caller_team_name="T2")
    assert resolved is None
    # worker/MCP (no user context) resolves the platform-wide system default
    resolved = resolve_model_config(model_db, "chat")
    assert resolved and resolved["model"] == "sys-chat"
    # admin sees personal > team > system
    assert resolve_model_config(model_db, "chat", caller_user_id="bob", caller_team_name="T1", is_sys_admin=True)["model"] == "sys-chat"
    # team default overrides system for team members
    _create(
        model_db,
        "team",
        name="team-chat",
        is_default=True,
        owner_team_name="T2",
        caller_user_id="carol",
        caller_team_name="T2",
    )
    resolved = resolve_model_config(model_db, "chat", caller_user_id="carol", caller_team_name="T2")
    assert resolved and resolved["model"] == "team-chat"
    # personal default overrides team
    _create(model_db, "personal", name="my-chat", is_default=True, caller_user_id="carol")
    resolved = resolve_model_config(model_db, "chat", caller_user_id="carol", caller_team_name="T2")
    assert resolved and resolved["model"] == "my-chat"
    assert resolved["api_key"] == "sk-test-123456"  # decrypted for runtime use