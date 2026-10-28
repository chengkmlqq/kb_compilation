"""代码仓库知识库 API 测试（2026-10）。

覆盖：
- create_kb 带 repo 配置：凭证 AES 加密落库、_kb_dict 序列化不回显密文
- update_kb：改凭证重加密、凭证留空保持、改仓库配置后清空拉取状态
- delete_kb：软删清空凭证列
- POST /repo/pull：类型校验、pulling 409 幂等
- GET /repo：状态返回且无凭证
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.lib.crypto import aes_decrypt
from api.main import app
from api.models.knowledge import KbDatasource
from api.services.identity import Identity, encode_identity_cookie
from conftest import create_test_kb as create_kb, seed_llm_model
import api.middleware as mw
import api.routers.kbs as kbs_router


@pytest.fixture()
def kb_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


@pytest.fixture()
def kb_client(kb_db, monkeypatch):
    seed_llm_model(kb_db)
    app.dependency_overrides[get_db] = lambda: kb_db
    monkeypatch.setattr(
        kbs_router,
        "decode_identity_cookie",
        lambda cookie: types.SimpleNamespace(user_id="admin", user_name="admin", team_name="ROOT"),
    )
    monkeypatch.setattr(mw, "get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: kb_db})())
    monkeypatch.setattr(mw, "get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="***"))
    monkeypatch.setattr(mw, "decode_identity_cookie", lambda cookie: Identity(user_id="admin"))
    # 入网 code_repo pull 不真发 Celery
    monkeypatch.setattr(
        kbs_router, "_enqueue_code_repo_pull",
        lambda kb_id, user_id, cookie: {"success": True, "data": {"job_id": f"REPO_{kb_id}"}},
    )
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    yield client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# create / serialize
# --------------------------------------------------------------------------- #
def test_create_repo_kb_encrypts_secret(kb_db, kb_client):
    res = kb_client.post("/api/v1/kbs", json={
        "name": "代码库A",
        "scope": "personal",
        "summary_model_id": "test-llm",
        "repo_type": "git",
        "repo_url": "https://git.example.com/team/a.git",
        "repo_branch": "main",
        "repo_auth_type": "token",
        "repo_auth_secret": "ghp_secret123",
    })
    assert res.status_code == 200, res.text
    kb = kb_db.execute(select(KbDatasource)).scalars().first()
    assert kb.repo_type == "git"
    assert kb.repo_auth_type == "token"
    # 密文存储
    assert kb.repo_auth_secret != "ghp_secret123"
    assert "ghp_secret123" not in kb.repo_auth_secret
    # 解密可还原
    payload = json_loads(aes_decrypt(kb.repo_auth_secret))
    assert payload["type"] == "token"
    assert payload["secret"] == "ghp_secret123"
    # 详情序列化不回显
    detail = kb_client.get(f"/api/v1/kbs/{kb.id}").json()
    assert detail["success"]
    d = detail["data"]
    assert d["repo_type"] == "git"
    assert d["repo_url"] == "https://git.example.com/team/a.git"
    assert "repo_auth_secret" not in d
    assert d["repo_pull_status"] == ""


def test_create_repo_kb_missing_url_rejected(kb_client):
    res = kb_client.post("/api/v1/kbs", json={
        "name": "代码库B",
        "scope": "personal",
        "summary_model_id": "test-llm",
        "repo_type": "git",
        "repo_url": "",
        "repo_auth_type": "none",
    })
    assert res.status_code == 400
    assert "仓库地址" in res.json()["detail"]


def test_create_repo_kb_auth_requires_secret(kb_client):
    res = kb_client.post("/api/v1/kbs", json={
        "name": "代码库C",
        "scope": "personal",
        "summary_model_id": "test-llm",
        "repo_type": "git",
        "repo_url": "https://git.example.com/team/c.git",
        "repo_auth_type": "token",
        "repo_auth_secret": "",
    })
    assert res.status_code == 400
    assert "凭证" in res.json()["detail"]


def test_create_normal_kb_ignores_repo(kb_db, kb_client):
    res = kb_client.post("/api/v1/kbs", json={"name": "普通库", "scope": "personal", "summary_model_id": "test-llm"})
    assert res.status_code == 200
    kb = kb_db.execute(select(KbDatasource)).scalars().first()
    assert kb.repo_type == ""
    assert kb.repo_auth_secret == ""


def test_create_repo_kb_triggers_pull(kb_client, monkeypatch):
    pulled: list[str] = []

    def _fake_enqueue(kb_id, user_id, cookie):
        pulled.append(kb_id)
        return {"success": True}

    monkeypatch.setattr(kbs_router, "_enqueue_code_repo_pull", _fake_enqueue)
    res = kb_client.post("/api/v1/kbs", json={
        "name": "代码库D",
        "scope": "personal",
        "summary_model_id": "test-llm",
        "repo_type": "git",
        "repo_url": "https://git.example.com/team/d.git",
    })
    assert res.status_code == 200
    assert len(pulled) == 1


def json_loads(s: str) -> dict:
    import json as _json

    return _json.loads(s)


# --------------------------------------------------------------------------- #
# update
# --------------------------------------------------------------------------- #
def test_update_repo_secret_reencrypts(kb_db, kb_client):
    kb = create_kb(
        kb_db, "代码库E", scope="personal", owner_user_id="admin",
        repo_type="git",
        repo_url="https://e.git", repo_auth_type="token",
        repo_auth_secret="old_secret",
    )
    res = kb_client.put(f"/api/v1/kbs/{kb.id}", json={
        "repo_auth_secret": "new_secret",
    })
    assert res.status_code == 200, res.text
    kb2 = kb_db.get(KbDatasource, kb.id)
    payload = json_loads(aes_decrypt(kb2.repo_auth_secret))
    assert payload["secret"] == "new_secret"
    # 改配置后清空拉取状态
    assert kb2.repo_pull_status == ""


def test_update_repo_secret_blank_keeps(kb_db, kb_client):
    kb = create_kb(
        kb_db, "代码库F", scope="personal", owner_user_id="admin",
        repo_type="git",
        repo_url="https://f.git", repo_auth_type="token",
        repo_auth_secret="keep_me",
    )
    res = kb_client.put(f"/api/v1/kbs/{kb.id}", json={"repo_branch": "dev"})
    assert res.status_code == 200
    kb2 = kb_db.get(KbDatasource, kb.id)
    assert kb2.repo_branch == "dev"
    payload = json_loads(aes_decrypt(kb2.repo_auth_secret))
    assert payload["secret"] == "keep_me"


# --------------------------------------------------------------------------- #
# repo endpoints
# --------------------------------------------------------------------------- #
def test_repo_detail_hides_secret(kb_db, kb_client):
    kb = create_kb(
        kb_db, "代码库G", scope="personal", owner_user_id="admin",
        repo_type="git",
        repo_url="https://g.git", repo_auth_type="ssh_key",
        repo_auth_secret="PRIVATE KEY BODY",
    )
    res = kb_client.get(f"/api/v1/kbs/{kb.id}/repo")
    assert res.status_code == 200, res.text
    d = res.json()["data"]
    assert d["repo_url"] == "https://g.git"
    assert d["repo_auth_type"] == "ssh_key"
    assert "secret" not in d and "auth_secret" not in d


def test_repo_detail_rejects_normal_kb(kb_db, kb_client):
    kb = create_kb(kb_db, "普通库", scope="personal", owner_user_id="admin")
    res = kb_client.get(f"/api/v1/kbs/{kb.id}/repo")
    assert res.status_code == 400


def test_repo_pull_enqueues(kb_db, kb_client):
    kb = create_kb(kb_db, "代码库H", scope="personal", owner_user_id="admin",
                   repo_type="git", repo_url="https://h.git")
    res = kb_client.post(f"/api/v1/kbs/{kb.id}/repo/pull")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["job_id"] == f"REPO_{kb.id}"


def test_repo_pull_rejects_normal_kb(kb_db, kb_client):
    kb = create_kb(kb_db, "普通库", scope="personal", owner_user_id="admin")
    res = kb_client.post(f"/api/v1/kbs/{kb.id}/repo/pull")
    assert res.status_code == 400


def test_repo_pull_conflict_when_pulling(kb_db, kb_client):
    kb = create_kb(kb_db, "代码库I", scope="personal", owner_user_id="admin",
                   repo_type="git", repo_url="https://i.git")
    kb.repo_pull_status = "pulling"
    kb_db.commit()
    res = kb_client.post(f"/api/v1/kbs/{kb.id}/repo/pull")
    assert res.status_code == 409


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #
def test_delete_kb_clears_repo_secret(kb_db, kb_client):
    kb = create_kb(
        kb_db, "代码库J", scope="personal", owner_user_id="admin",
        repo_type="git",
        repo_url="https://j.git", repo_auth_type="token",
        repo_auth_secret="erase_me",
    )
    res = kb_client.delete(f"/api/v1/kbs/{kb.id}")
    assert res.status_code == 200, res.text
    kb2 = kb_db.get(KbDatasource, kb.id)
    assert kb2.state == "0"
    assert kb2.repo_auth_secret is None
    assert kb2.repo_url == ""