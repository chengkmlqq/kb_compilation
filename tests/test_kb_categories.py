"""知识库分类 API 测试（/api/v1/kb-categories，2026-10）。

覆盖：
- 列表 = kb_category 行 ∪ 可见知识库的 label（含计数、倒序）
- 新建 → 空分类出现；重复名 409；空名 400
- 重命名 → 改行 + 可管理知识库的 label；他人 KB（不可管理）不动；目标已存在 409
- 删除 → 删行 + 可管理知识库的 label 清空；不存在 404
- 未登录 401

每个用例独立 in-memory 库；teardown 清理 app.dependency_overrides。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.knowledge import KbCategory, KbDatasource
from api.services.identity import Identity, encode_identity_cookie


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    Base.metadata.create_all(engine)
    import api.config as config_mod

    # is_admin 走 AUTH_ADMIN_USERS 旁路：默认空（非管理员）
    ns = type("S", (), {})()
    setattr(ns, "AUTH" + "_ADMIN_USERS", "")
    monkeypatch.setattr(config_mod, "get_settings", lambda: ns)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: db})(),
    )
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    yield db, client
    app.dependency_overrides.pop(get_db, None)


def _add_kb(db, kb_id: str, label: str | None, *, scope: str = "personal",
            owner: str = "u1"):
    db.add(
        KbDatasource(
            id=kb_id,
            name=f"库-{kb_id}",
            label=label,
            scope=scope,
            owner_user_id=owner,
            owner_team_name="team1" if scope == "team" else "",
            team_name="team1" if scope == "team" else "",
            type="document",
            state="1",
        )
    )
    db.commit()


def _add_cat(db, name: str):
    db.add(KbCategory(id=f"cat-{name}", name=name, created_by="u1"))
    db.commit()


def test_list_merges_rows_and_labels_with_counts(env) -> None:
    db, client = env
    _add_cat(db, "制度")  # 空分类：0 个库
    _add_kb(db, "kb1", "技术")
    _add_kb(db, "kb2", "技术")

    r = client.get("/api/v1/kb-categories")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    by_name = {i["name"]: i for i in items}
    assert by_name["技术"]["count"] == 2
    assert by_name["制度"]["count"] == 0
    assert by_name["制度"]["id"] == "cat-制度"
    assert items[0]["name"] == "技术"  # 按库数倒序


def test_create_and_duplicate(env) -> None:
    db, client = env
    r = client.post("/api/v1/kb-categories", json={"name": " 新分类 "})
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "新分类"
    # 空名 400
    assert client.post("/api/v1/kb-categories", json={"name": "  "}).status_code == 400
    # 重复名 409
    assert client.post("/api/v1/kb-categories", json={"name": "新分类"}).status_code == 409


def test_rename_updates_row_and_manageable_kbs(env) -> None:
    db, client = env
    _add_cat(db, "旧名")
    _add_kb(db, "mine", "旧名")          # 属主 u1：可管理 → 跟着改名
    _add_kb(db, "others", "旧名", owner="u2")  # 他人 personal：不可管理 → 不动

    r = client.patch("/api/v1/kb-categories?name=旧名", json={"name": "新名"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "新名"
    assert data["updated_kbs"] == 1
    mine = db.execute(select(KbDatasource).where(KbDatasource.id == "mine")).scalars().one()
    others = db.execute(select(KbDatasource).where(KbDatasource.id == "others")).scalars().one()
    assert mine.label == "新名"
    assert others.label == "旧名"
    assert (
        db.execute(select(KbCategory).where(KbCategory.name == "新名")).scalars().first()
        is not None
    )

    # 改名后旧名消失、新名在树中
    items = client.get("/api/v1/kb-categories").json()["data"]["items"]
    names = {i["name"] for i in items}
    assert "新名" in names
    assert "旧名" not in names


def test_rename_target_conflict(env) -> None:
    db, client = env
    _add_cat(db, "A")
    _add_cat(db, "B")
    r = client.patch("/api/v1/kb-categories?name=A", json={"name": "B"})
    assert r.status_code == 409


def test_delete_clears_labels_and_row(env) -> None:
    db, client = env
    _add_cat(db, "废弃")
    _add_kb(db, "mine", "废弃")
    _add_kb(db, "others", "废弃", owner="u2")

    r = client.delete("/api/v1/kb-categories?name=废弃")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["updated_kbs"] == 1
    assert data["row_deleted"] is True
    mine = db.execute(select(KbDatasource).where(KbDatasource.id == "mine")).scalars().one()
    others = db.execute(select(KbDatasource).where(KbDatasource.id == "others")).scalars().one()
    assert mine.label == ""
    assert others.label == "废弃"  # 他人库不动
    assert db.get(KbCategory, "cat-废弃") is None
    names = {i["name"] for i in client.get("/api/v1/kb-categories").json()["data"]["items"]}
    assert "废弃" not in names


def test_delete_missing_404(env) -> None:
    db, client = env
    r = client.delete("/api/v1/kb-categories?name=不存在")
    assert r.status_code == 404


def test_requires_login(env) -> None:
    db, _client = env
    _add_cat(db, "私有")
    anonymous = TestClient(app)
    assert anonymous.get("/api/v1/kb-categories").status_code == 401
    assert anonymous.post("/api/v1/kb-categories", json={"name": "x"}).status_code == 401
    assert anonymous.patch("/api/v1/kb-categories?name=x", json={"name": "y"}).status_code == 401
    assert anonymous.delete("/api/v1/kb-categories?name=x").status_code == 401


def test_put_kb_updates_label(env) -> None:
    """卡片拖拽 / 配置弹窗改分类：PUT /kbs/{id} 更新 label = 归入分类树对应分类。"""
    db, client = env
    _add_cat(db, "技术")
    _add_kb(db, "mine", None)  # 未分类

    # 拖卡到分类「技术」：label = 分类名，分类计数 +1
    r = client.put("/api/v1/kbs/mine", json={"label": "技术"})
    assert r.status_code == 200
    assert db.get(KbDatasource, "mine").label == "技术"
    by_name = {
        i["name"]: i for i in client.get("/api/v1/kb-categories").json()["data"]["items"]
    }
    assert by_name["技术"]["count"] == 1


def test_put_kb_label_empty_clears_category(env) -> None:
    """拖回「全部」/ 配置弹窗清空分类：空串 '' 生效（未分类）。"""
    db, client = env
    _add_cat(db, "技术")
    _add_kb(db, "mine", "技术")
    r = client.put("/api/v1/kbs/mine", json={"label": ""})
    assert r.status_code == 200
    assert db.get(KbDatasource, "mine").label == ""
    # 分类行仍在（空分类），但计数归零
    by_name = {
        i["name"]: i for i in client.get("/api/v1/kb-categories").json()["data"]["items"]
    }
    assert by_name["技术"]["count"] == 0


def test_put_kb_label_null_keeps_existing(env) -> None:
    """null 被 PUT 端点 exclude_none 丢弃：label 保持原值——前端清空分类必须传空串。"""
    db, client = env
    _add_kb(db, "mine", "技术")
    r = client.put("/api/v1/kbs/mine", json={"label": None})
    assert r.status_code == 200
    assert db.get(KbDatasource, "mine").label == "技术"


def test_put_kb_basic_info_updates_name_description(env) -> None:
    """配置弹窗基本信息分区：PUT /kbs/{id} 更新 name / description。"""
    db, client = env
    _add_kb(db, "mine", None)
    r = client.put(
        "/api/v1/kbs/mine",
        json={"name": "监管制度知识库", "description": "收录集团市场监督管理相关制度文件"},
    )
    assert r.status_code == 200
    kb = db.get(KbDatasource, "mine")
    assert kb.name == "监管制度知识库"
    assert kb.description == "收录集团市场监督管理相关制度文件"


def test_put_kb_basic_info_blank_name_keeps_existing(env) -> None:
    """配置弹窗名称留空（前端已 required 拦截，后端兜底）：空名不覆盖原名。"""
    db, client = env
    _add_kb(db, "mine", None)
    r = client.put("/api/v1/kbs/mine", json={"name": "   "})
    assert r.status_code == 200
    assert db.get(KbDatasource, "mine").name == "库-mine"