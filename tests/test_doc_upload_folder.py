"""KB 文档上传的目录归属测试（目录上传落库路径，2026-10-17）。

覆盖：
- folder_id 必须走 **query 参数**：后端 upload_document 声明为 Query(...)，
  multipart 表单里的同名字段会被静默忽略（文档落到库根层级）。前端
  apiUploadDocumentWithProgress 已改为拼接 ?folder_id=xxx，此处固化该约定。
- 目录归属校验：不存在 / 跨库的 folder_id 直接 400，不产生孤儿文档。
"""

from __future__ import annotations

import io
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.knowledge import KbDocument
from api.services.identity import Identity, encode_identity_cookie
from api.services.kb_admin import doc_create_folder
from conftest import create_test_kb as create_kb
import api.middleware as mw
import api.routers.kbs as kbs_router


@pytest.fixture()
def up_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 共享底层连接：TestClient 线程与主线程同一库
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


@pytest.fixture()
def up_client(up_db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: up_db
    monkeypatch.setattr(
        kbs_router,
        "decode_identity_cookie",
        lambda cookie: types.SimpleNamespace(user_id="admin", user_name="admin", team_name="ROOT"),
    )
    # middleware 守卫：受控路径需身份——mock 身份解析并带上登录 cookie
    monkeypatch.setattr(mw, "get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: up_db})())
    monkeypatch.setattr(mw, "get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"))
    monkeypatch.setattr(mw, "decode_identity_cookie", lambda cookie: Identity(user_id="admin"))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    yield client
    app.dependency_overrides.clear()


def _post_upload(client, kb_id: str, *, query_folder: str = "", form_folder: str = ""):
    """上传一个最小 pdf：query_folder 拼在 URL 上，form_folder 作为表单字段。"""
    url = f"/api/v1/kbs/{kb_id}/documents/upload"
    if query_folder:
        url += f"?folder_id={query_folder}"
    data = {"folder_id": form_folder} if form_folder else {}
    return client.post(
        url,
        data=data,
        files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
    )


def _only_doc(db, kb_id: str) -> KbDocument:
    doc = db.execute(select(KbDocument).where(KbDocument.kb_id == kb_id)).scalars().first()
    assert doc is not None
    return doc


def test_upload_uses_query_folder_id(up_client, up_db) -> None:
    """上传到所选目录：folder_id 走 query → 落到该目录（目录上传的核心依赖）。"""
    kb = create_kb(up_db, "目录上传库", scope="personal", owner_user_id="admin", created_by="admin")
    folder = doc_create_folder(up_db, kb.id, {"name": "2024"})

    r = _post_upload(up_client, kb.id, query_folder=folder["id"])
    assert r.status_code == 200, r.text
    assert _only_doc(up_db, kb.id).folder_id == folder["id"]


def test_upload_form_field_folder_id_is_ignored(up_client, up_db) -> None:
    """回归护栏：folder_id 放进 multipart 表单会被后端静默忽略（落到根层级）。

    前端若改回 form.append("folder_id", ...)，本用例会失败——说明约定的 query 传参被破坏。
    """
    kb = create_kb(up_db, "目录上传库", scope="personal", owner_user_id="admin", created_by="admin")
    folder = doc_create_folder(up_db, kb.id, {"name": "2024"})

    r = _post_upload(up_client, kb.id, form_folder=folder["id"])
    assert r.status_code == 200, r.text
    assert _only_doc(up_db, kb.id).folder_id in ("", None)


def test_upload_rejects_unknown_folder(up_client, up_db) -> None:
    """目录不存在 → 400，且不落库（防止孤儿文档）。"""
    kb = create_kb(up_db, "目录上传库", scope="personal", owner_user_id="admin", created_by="admin")

    r = _post_upload(up_client, kb.id, query_folder="not-exist-folder")
    assert r.status_code == 400
    assert "目录不存在" in r.text
    assert up_db.execute(select(KbDocument).where(KbDocument.kb_id == kb.id)).scalars().first() is None


def test_upload_rejects_cross_kb_folder(up_client, up_db) -> None:
    """目录属于其它知识库 → 400（归属校验按 KB 维度）。"""
    kb_a = create_kb(up_db, "库A", scope="personal", owner_user_id="admin", created_by="admin")
    kb_b = create_kb(up_db, "库B", scope="personal", owner_user_id="admin", created_by="admin")
    folder_b = doc_create_folder(up_db, kb_b.id, {"name": "外库目录"})

    r = _post_upload(up_client, kb_a.id, query_folder=folder_b["id"])
    assert r.status_code == 400
    assert up_db.execute(select(KbDocument).where(KbDocument.kb_id == kb_a.id)).scalars().first() is None
