"""Tests for the system file management API (api/routers/files.py) — explorer
browse / search / upload / download / logical delete / rmdir / zip.
Uses in-memory SQLite for the DB; physical files go to a temp dir.
"""

from __future__ import annotations

import io
import os
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, SysFile

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)

TMP_ROOT = tempfile.mkdtemp(prefix="kb-files-test-")


def _make_session() -> Session:
    Base.metadata.create_all(engine)
    db = Session()
    return db


def test_files_explore_upload_download_delete(monkeypatch) -> None:
    """explore 层级下探 + 上传 + 下载 + 逻辑删除 + 权限隔离。"""
    db = _make_session()
    app.dependency_overrides[get_db] = lambda: db
    # 指向临时目录（不污染部署存储）
    import api.routers.files as files_mod

    monkeypatch.setattr(files_mod, "_file_root", lambda: os.path.join(TMP_ROOT, "root"))
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    import types
    from api.services.identity import Identity, encode_identity_cookie

    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"),
    )
    real_decoder = files_mod.decode_identity_cookie
    files_mod.decode_identity_cookie = lambda cookie: Identity(
        user_id="admin", user_name="admin", team_id="team1", team_name="team1"
    )

    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(
            Identity(user_id="admin", user_name="admin", team_id="team1", team_name="team1")
        ),
    )

    # ---- upload ----
    r = client.post(
        "/api/v1/files/upload",
        data={"module": "docs"},
        files={"file": ("报告.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert r.status_code == 200
    up = r.json()["data"]
    assert up["business_module"] == "docs"
    assert up["team_id"] == "team1"
    assert up["file_name"] == "报告.pdf"
    assert up["storage_type"] == "local"
    assert up["state"] == "1"
    file_id = up["id"]

    # 物理文件存在
    phys = os.path.join(files_mod._file_root(), up["storage_path"])
    assert os.path.exists(phys)

    # ---- explore: 根 → 模块 ----
    r = client.get("/api/v1/files/explore")
    assert r.status_code == 200
    items = r.json()["data"]["list"]
    assert any(i["name"] == "docs" and i["isFolder"] for i in items)

    # ---- explore: 模块 → 团队 ----
    r = client.get("/api/v1/files/explore", params={"current_path": "/docs"})
    assert r.status_code == 200
    items = r.json()["data"]["list"]
    assert any(i["name"] == "team1" and i["isFolder"] for i in items)

    # ---- explore: 团队 → 日期 ----
    r = client.get("/api/v1/files/explore", params={"current_path": "/docs/team1"})
    assert r.status_code == 200
    items = r.json()["data"]["list"]
    assert any(i["isFolder"] and len(i["name"]) == 8 for i in items)

    # ---- explore: 日期 → 文件（用存储路径里的日期段）----
    date_seg = up["storage_path"].split("/")[-2]
    r = client.get("/api/v1/files/explore", params={"current_path": f"/docs/team1/{date_seg}"})
    assert r.status_code == 200
    items = r.json()["data"]["list"]
    assert any(i["id"] == file_id for i in items)

    # ---- search ----
    r = client.get("/api/v1/files/explore", params={"search": "报告"})
    assert r.status_code == 200
    items = r.json()["data"]["list"]
    assert any(i["id"] == file_id for i in items)

    # ---- download ----
    r = client.get(f"/api/v1/files/{file_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert b"%PDF-1.4" in r.content

    # ---- zip (目录打包) ----
    r = client.get("/api/v1/files/zip", params={"path": f"/docs/team1/{date_seg}"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert b"PK" in r.content[:4]

    # ---- 逻辑删除 ----
    r = client.delete(f"/api/v1/files/{file_id}")
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True
    r = client.get("/api/v1/files/explore", params={"current_path": f"/docs/team1/{date_seg}"})
    assert all(i["id"] != file_id for i in r.json()["data"]["list"])

    # ---- rmdir ----
    r = client.post("/api/v1/files/rmdir", params={"path": f"/docs/team1/{date_seg}"})
    # 文件已删，目录空 → 提示无文件可删（或成功但 0）
    assert r.status_code in (200, 400)

    files_mod.decode_identity_cookie = real_decoder
    app.dependency_overrides.clear()


def test_files_team_isolation(monkeypatch) -> None:
    """非 admin 用户只能看到自己的团队文件。"""
    db = _make_session()
    db.query(SysFile).delete()
    db.add(
        SysFile(
            id="F_TEAM_A",
            file_name="a.txt",
            file_size=10,
            storage_type="local",
            storage_path="docs/teamA/20261001/f1_a.txt",
            team_id="teamA",
            business_module="docs",
            created_by="u_a",
            create_date="2026-10-01 10:00:00",
            state="1",
        )
    )
    db.add(
        SysFile(
            id="F_TEAM_B",
            file_name="b.txt",
            file_size=20,
            storage_type="local",
            storage_path="docs/teamB/20261001/f2_b.txt",
            team_id="teamB",
            business_module="docs",
            created_by="u_b",
            create_date="2026-10-01 10:00:00",
            state="1",
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db

    import api.routers.files as files_mod
    import types
    from api.services.identity import Identity, encode_identity_cookie

    monkeypatch.setattr(
        "api.middleware.get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: db})()
    )
    monkeypatch.setattr(
        "api.middleware.get_settings",
        lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="u_a"),
    )
    real_decoder = files_mod.decode_identity_cookie
    files_mod.decode_identity_cookie = lambda cookie: Identity(
        user_id="u_a", user_name="u_a", team_id="teamA", team_name="teamA"
    )

    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(
            Identity(user_id="u_a", user_name="u_a", team_id="teamA", team_name="teamA")
        ),
    )

    # teamA 用户看模块 → 只见 teamA
    r = client.get("/api/v1/files/explore", params={"current_path": "/docs"})
    teams = [i["name"] for i in r.json()["data"]["list"] if i["isFolder"]]
    assert "teamA" in teams
    assert "teamB" not in teams

    # 跨团队删除 → 403
    r = client.delete("/api/v1/files/F_TEAM_B")
    assert r.status_code == 403

    # 同团队删除 → 200
    r = client.delete("/api/v1/files/F_TEAM_A")
    assert r.status_code == 200

    files_mod.decode_identity_cookie = real_decoder
    app.dependency_overrides.clear()
