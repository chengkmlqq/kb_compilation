"""System admin service tests — users / roles / teams / menus / operation logs
and the role-menu / role-user authorization writes (menu RBAC)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import (
    Menu,
    OperLog,
    RoleMenuRela,
    Team,
    User,
    UserRole,
    UserRoleRela,
)
from api.services.system_admin import (
    delete_menu,
    delete_role,
    get_role_menus,
    get_role_users,
    is_platform_admin,
    list_menus,
    list_operation_logs,
    list_roles,
    list_teams,
    list_users,
    my_menus,
    save_menu,
    save_role,
    save_role_menus,
    save_role_users,
    user_roles,
)


@pytest.fixture()
def sys_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            User(id="u1", user_id="zhang_san", user_name="张三", email="z@x.com", state="1"),
            User(id="u2", user_id="li_si", user_name="李四", state="0"),
            UserRole(role_id="r1", role_name="管理员", role_type="plat-mgr", state="1"),
            UserRole(role_id="r2", role_name="只读", role_type="team-role", state="1"),
            UserRoleRela(rela_id="x1", role_id="r1", user_id="zhang_san"),
            UserRoleRela(rela_id="x2", role_id="r2", user_id="li_si"),
            RoleMenuRela(rela_id="rm1", role_id="r1", menu_id="m1"),
            RoleMenuRela(rela_id="rm2", role_id="r2", menu_id="m2"),
            Team(team_id="t1", team_name="ROOT", label="根团队", state="1"),
            Menu(menu_id="m1", menu_name="kbs", menu_label="知识库", route="/kbs", sort_num=1, state="1"),
            Menu(menu_id="m2", menu_name="system", menu_label="系统管理", route="/system", sort_num=9, state="1"),
            Menu(menu_id="m3", menu_name="chat", menu_label="智能问答", route="/chat", parent_id="m1", sort_num=2, state="1"),
            OperLog(
                id="l1",
                user_name="张三",
                oper_type="LOGIN",
                oper_content="张三登录的系统",
                oper_time="2026-09-30 12:00:00",
            ),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_list_users_paginated(sys_db) -> None:
    data = list_users(sys_db, page=1, page_size=10)
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["user_name"] == "张三"


def test_list_users_keyword(sys_db) -> None:
    data = list_users(sys_db, keyword="张三")
    assert data["total"] == 1
    assert data["items"][0]["user_id"] == "zhang_san"


def test_list_roles(sys_db) -> None:
    data = list_roles(sys_db)
    assert data["total"] == 2
    names = {r["role_name"] for r in data["items"]}
    assert names == {"管理员", "只读"}


def test_list_teams(sys_db) -> None:
    data = list_teams(sys_db)
    assert data["total"] == 1
    assert data["items"][0]["team_name"] == "ROOT"


def test_list_menus_ordered(sys_db) -> None:
    data = list_menus(sys_db)
    assert data["total"] == 3
    assert data["items"][0]["route"] == "/kbs"  # sort_num asc


def test_list_operation_logs(sys_db) -> None:
    data = list_operation_logs(sys_db)
    assert data["total"] == 1
    assert data["items"][0]["oper_type"] == "LOGIN"


def test_user_roles_resolution(sys_db) -> None:
    assert user_roles(sys_db, "zhang_san") == ["管理员"]
    assert user_roles(sys_db, "li_si") == ["只读"]


# ============================================================================
# Role CRUD
# ============================================================================


def test_save_role_create_and_duplicate(sys_db) -> None:
    res = save_role(
        sys_db, {"role_id": "r3", "role_name": "访客", "role_type": "team-role", "state": "1"}
    )
    assert res["success"] is True
    res = save_role(sys_db, {"role_id": "r3", "role_name": "访客", "role_type": "team-role"})
    assert res["success"] is False  # duplicate role_id
    data = list_roles(sys_db)
    assert data["total"] == 3


def test_save_role_edit(sys_db) -> None:
    res = save_role(
        sys_db,
        {"role_id": "r2", "role_name": "只读改名", "role_type": "team-role", "state": "0", "is_edit": True},
    )
    assert res["success"] is True
    role = next(r for r in list_roles(sys_db)["items"] if r["role_id"] == "r2")
    assert role["role_name"] == "只读改名"
    assert role["state"] == "0"


def test_save_role_requires_fields(sys_db) -> None:
    assert save_role(sys_db, {"role_name": "x"})["success"] is False
    assert save_role(sys_db, {"role_id": "r9", "role_name": ""})["success"] is False
    assert save_role(sys_db, {"role_id": "r9", "role_name": "x", "role_type": "bad"})["success"] is False


def test_delete_role_bound_user_refused(sys_db) -> None:
    res = delete_role(sys_db, "r1")
    assert res["success"] is False  # u1 still bound
    assert list_roles(sys_db)["total"] == 2


def test_delete_role_cascades_menu_links(sys_db) -> None:
    # r2 has no users when we remove its rela first
    sys_db.execute(
        __import__("sqlalchemy").delete(UserRoleRela).where(UserRoleRela.role_id == "r2")
    )
    sys_db.commit()
    res = delete_role(sys_db, "r2")
    assert res["success"] is True
    assert get_role_menus(sys_db, "r2") == {"menuIds": []}


# ============================================================================
# Role -> Menu assignment
# ============================================================================


def test_get_role_menus_initial(sys_db) -> None:
    assert set(get_role_menus(sys_db, "r1")["menuIds"]) == {"m1"}
    assert set(get_role_menus(sys_db, "r2")["menuIds"]) == {"m2"}


def test_save_role_menus_replaces(sys_db) -> None:
    res = save_role_menus(sys_db, "r1", ["m1", "m3", "not-exist"])
    assert res["success"] is True
    assert set(get_role_menus(sys_db, "r1")["menuIds"]) == {"m1", "m3"}  # invalid id dropped
    res = save_role_menus(sys_db, "r1", [])
    assert res["success"] is True
    assert get_role_menus(sys_db, "r1") == {"menuIds": []}


def test_save_role_menus_unknown_role(sys_db) -> None:
    assert save_role_menus(sys_db, "ghost", ["m1"])["success"] is False


# ============================================================================
# Role -> User assignment
# ============================================================================


def test_get_role_users(sys_db) -> None:
    assert get_role_users(sys_db, "r1") == {"userIds": ["zhang_san"]}
    assert get_role_users(sys_db, "r2") == {"userIds": ["li_si"]}


def test_save_role_users_replaces(sys_db) -> None:
    res = save_role_users(sys_db, "r1", ["li_si", "ghost"])
    assert res["success"] is True
    assert get_role_users(sys_db, "r1") == {"userIds": ["li_si"]}
    assert user_roles(sys_db, "zhang_san") == []


# ============================================================================
# Menu CRUD
# ============================================================================


def test_save_menu_create_and_edit(sys_db) -> None:
    res = save_menu(
        sys_db,
        {
            "menu_name": "agents",
            "menu_label": "智能体配置",
            "route": "/agents",
            "sort_num": 3,
            "menu_ext_conf": {"open_type": "0", "link_type": "inner", "fetch_mode": "route"},
        },
    )
    assert res["success"] is True
    item = next(m for m in list_menus(sys_db)["items"] if m["menu_name"] == "agents")
    assert item["route"] == "/agents"
    assert '"open_type": "0"' in sys_db.get(Menu, item["menu_id"]).menu_ext_conf

    # edit: set parent + change label
    res = save_menu(
        sys_db,
        {
            "menu_id": item["menu_id"],
            "menu_name": "agents",
            "menu_label": "智能体管理",
            "parent_id": "m1",
            "is_edit": True,
        },
    )
    assert res["success"] is True
    updated = sys_db.get(Menu, item["menu_id"])
    assert updated.menu_label == "智能体管理"
    assert updated.parent_id == "m1"


def test_save_menu_requires_name(sys_db) -> None:
    assert save_menu(sys_db, {"menu_name": "x"})["success"] is False
    assert save_menu(sys_db, {"menu_label": "x"})["success"] is False


def test_delete_menu_child_refused(sys_db) -> None:
    # m1 has child m3
    assert delete_menu(sys_db, "m1")["success"] is False
    res = delete_menu(sys_db, "m3")
    assert res["success"] is True
    assert sys_db.get(Menu, "m3") is None


def test_delete_menu_role_refused(sys_db) -> None:
    # m1 is referenced by role r1 (rm1) and has child m3
    assert delete_menu(sys_db, "m1")["success"] is False  # child + role ref
    save_role_menus(sys_db, "r1", ["m3"])  # r1 now points at m3; m1 no longer referenced
    assert delete_menu(sys_db, "m1")["success"] is False  # still has child m3
    assert delete_menu(sys_db, "m3")["success"] is False  # now referenced by r1
    save_role_menus(sys_db, "r1", [])  # release the reference
    assert delete_menu(sys_db, "m3")["success"] is True
    assert delete_menu(sys_db, "m1")["success"] is True  # now free


# ============================================================================
# my-menus (Sider) + platform admin
# ============================================================================


def test_my_menus_admin_full(sys_db) -> None:
    data = my_menus(sys_db, "zhang_san", is_admin=True)
    assert data["total"] == 3


def test_my_menus_by_role_links(sys_db) -> None:
    # zhang_san -> r1 -> {m1}; li_si -> r2 -> {m2}; m3 is not linked to anyone
    u1 = my_menus(sys_db, "zhang_san")
    assert [m["menu_id"] for m in u1["items"]] == ["m1"]
    u2 = my_menus(sys_db, "li_si")
    assert [m["menu_id"] for m in u2["items"]] == ["m2"]


def test_my_menus_empty_without_roles(sys_db) -> None:
    sys_db.add(User(id="u3", user_id="nobody", user_name="无人", state="1"))
    sys_db.commit()
    assert my_menus(sys_db, "nobody") == {"items": [], "total": 0}


def test_is_platform_admin(sys_db) -> None:
    # bypass is driven by the explicit AUTH_ADMIN_USERS list (source proxy
    # behaviour) — role_type is NOT used because shared seed data marks
    # several non-admin roles with role_type='plat-mgr'.
    assert is_platform_admin(sys_db, "admin", admin_users=["admin"]) is True
    assert is_platform_admin(sys_db, "zhang_san", admin_users=["admin"]) is False
    assert is_platform_admin(sys_db, "zhang_san") is False
    assert is_platform_admin(sys_db, "zhang_san", admin_users=[]) is False