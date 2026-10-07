from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base
from api.models.knowledge import KbDatasource, KbDocument
from api.services.identity import Identity, encode_identity_cookie
import api.middleware as mw
import api.routers.kbs as kbs_mod


@pytest.fixture()
def client_and_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(
        mw,
        "get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"),
    )
    monkeypatch.setattr(
        mw, "decode_identity_cookie", lambda cookie: Identity(user_id="admin")
    )
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(
            Identity(user_id="admin", user_name="admin", team_name="ROOT")
        ),
    )
    yield client, db
    app.dependency_overrides.clear()
    db.close()


def _mk_kb(db) -> str:
    kb = KbDatasource(
        id="kbdl0001",
        name="dl-test",
        label="DlTest",
        scope="personal",
        owner_user_id="admin",
    )
    db.add(kb)
    db.commit()
    return kb.id


def _mk_doc(db, storage_path: str) -> str:
    _mk_kb(db)
    doc = KbDocument(
        id="docdl0001",
        kb_id="kbdl0001",
        file_name="a.md",
        file_ext="md",
        storage_path=storage_path,
        parse_state="READY",
    )
    db.add(doc)
    db.commit()
    return doc.id


def test_download_fallback_reads_storage_path(client_and_db, monkeypatch):
    """sys_file_id 为空时走 storage_path 兜底（MinIO 上传场景）。"""
    client, db = client_and_db
    doc_id = _mk_doc(db, "kb_documents/kbdl0001/a.md")
    monkeypatch.setattr(
        "api.services.storage.get_bytes", lambda p: b"hello-preview-bytes"
    )

    r = client.get(f"/api/v1/kbs/kbdl0001/documents/{doc_id}/download")

    assert r.status_code == 200
    assert r.content == b"hello-preview-bytes"


def test_download_fallback_404_when_missing(client_and_db, monkeypatch):
    """兜底读取失败时返回 404 而非 200 null。"""
    client, db = client_and_db
    doc_id = _mk_doc(db, "kb_documents/kbdl0001/missing.md")

    def _boom(p):
        raise FileNotFoundError(p)

    monkeypatch.setattr("api.services.storage.get_bytes", _boom)

    r = client.get(f"/api/v1/kbs/kbdl0001/documents/{doc_id}/download")

    assert r.status_code == 404
    assert r.json().get("detail") == "物理文件缺失"


def test_download_404_when_no_storage_path(client_and_db):
    """既无 sys_file_id 也无 storage_path 时明确 404。"""
    client, db = client_and_db
    doc_id = _mk_doc(db, "")

    r = client.get(f"/api/v1/kbs/kbdl0001/documents/{doc_id}/download")

    assert r.status_code == 404
    assert r.json().get("detail") == "物理文件缺失"