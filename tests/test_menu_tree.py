"""菜单骨架测试：三级层级 + 防成环校验（对齐 data-synth 墨斗平台菜单结构）。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import get_db
from api.main import app
from api.models.framework import Base, Menu, RoleMenuRela, UserRole
from api.services.system_admin import save_menu, delete_menu
import pytest


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _seed_two_level(db):
    """root → grp_a → page_a，root → page_b"""
    db.add_all([
        Menu(menu_id="root", menu_name="root", menu_label="根", parent_id=None, sort_num=0, state="1"),
        Menu(menu_id="grp_a", menu_name="grp_a", menu_label="分组A", parent_id="root", route=None, sort_num=1, state="1"),
        Menu(menu_id="page_a", menu_name="page_a", menu_label="页面A", parent_id="grp_a", route="/a", sort_num=1, state="1"),
        Menu(menu_id="page_b", menu_name="page_b", menu_label="页面B", parent_id="root", route="/b", sort_num=2, state="1"),
    ])
    db.commit()


def test_menu_multilevel_tree(db):
    """三级菜单可以正常保存并保留层级。"""
    _seed_two_level(db)
    res = save_menu(db, {"menu_name": "sub", "menu_label": "三级页面", "route": "/a/sub", "parent_id": "page_a", "sort_num": 1})
    assert res["success"] is True
    row = db.execute(select(Menu).where(Menu.menu_name == "sub")).scalars().first()
    assert row.parent_id == "page_a" and row.route == "/a/sub"


def test_menu_cycle_rejected(db):
    """防成环：不能把父级设为自己的后代。"""
    _seed_two_level(db)
    # grp_a 的父级设为自己的子级 page_a → 应拒绝
    res = save_menu(db, {"is_edit": True, "menu_id": "grp_a", "menu_name": "grp_a", "menu_label": "分组A", "parent_id": "page_a"})
    assert res["success"] is False
    assert "环" in res["message"] or "子菜单" in res["message"]


def test_menu_self_parent_rejected(db):
    """防成环：不能把父级设为自身。"""
    _seed_two_level(db)
    res = save_menu(db, {"is_edit": True, "menu_id": "grp_a", "menu_name": "grp_a", "menu_label": "分组A", "parent_id": "grp_a"})
    assert res["success"] is False


def test_menu_parent_not_found(db):
    """父模块不存在时应拒绝。"""
    res = save_menu(db, {"menu_name": "x", "menu_label": "X", "parent_id": "nope"})
    assert res["success"] is False
    assert "不存在" in res["message"]


def test_menu_delete_with_children_blocked(db):
    """有子菜单的节点不允许直接删除。"""
    _seed_two_level(db)
    res = delete_menu(db, "grp_a")
    assert res["success"] is False
    assert "子菜单" in res["message"]


def test_menu_delete_leaf_ok(db):
    """叶子节点可删除。"""
    _seed_two_level(db)
    res = delete_menu(db, "page_b")
    assert res["success"] is True
    assert db.execute(select(Menu).where(Menu.menu_id == "page_b")).scalars().first() is None


def test_menu_delete_blocked_by_role(db):
    """被角色引用的菜单不可删除。"""
    _seed_two_level(db)
    db.add(UserRole(role_id="r1", role_name="kb_role", role_type="plat-mgr", state="1"))
    db.add(RoleMenuRela(rela_id="rel1", role_id="r1", menu_id="page_a"))
    db.commit()
    res = delete_menu(db, "page_a")
    assert res["success"] is False
    assert "授权" in res["message"]


def test_my_menus_returns_three_levels(db):
    """my-menus 端点：平台管理员应拿到完整三级树（含 parent_id）。"""
    _seed_two_level(db)
    app.dependency_overrides[get_db] = lambda: db
    import api.middleware as mw
    import api.routers.system as sys_mod
    from api.services.identity import Identity, encode_identity_cookie
    import types as _t

    orig_sm, orig_gs, orig_sgs = mw.get_sessionmaker, mw.get_settings, sys_mod.get_settings
    mw.get_sessionmaker = lambda: type("SM", (), {"__call__": lambda s: db})()
    mw.get_settings = lambda: _t.SimpleNamespace(AUTH_ADMIN_USERS="admin")
    sys_mod.get_settings = lambda: _t.SimpleNamespace(AUTH_ADMIN_USERS="admin")
    try:
        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
        )
        r = client.get("/api/v1/system/my-menus")
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        by_id = {m["menu_id"]: m for m in items}
        assert by_id["page_a"]["parent_id"] == "grp_a"
        assert by_id["grp_a"]["parent_id"] == "root"
        assert by_id["page_a"]["route"] == "/a"
    finally:
        mw.get_sessionmaker, mw.get_settings = orig_sm, orig_gs
        sys_mod.get_settings = orig_sgs
        app.dependency_overrides.clear()