"""System admin service tests — users / roles / teams / menus / operation logs
and the role-menu / role-user authorization writes (menu RBAC)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import (
    Menu,
    OperLog,
    RoleMenuRela,
    Team,
    TeamMember,
    User,
    UserRole,
    UserRoleRela,
)
from api.services.system_admin import (
    assign_user_roles,
    create_team,
    create_user,
    delete_menu,
    delete_role,
    delete_team,
    delete_user,
    get_menu_apis,
    get_role_menus,
    get_role_users,
    is_platform_admin,
    list_menus,
    list_operation_logs,
    list_roles,
    list_teams,
    list_users,
    menu_api_perms,
    my_menus,
    reset_user_pwd,
    save_menu,
    save_menu_apis,
    save_role,
    save_role_menus,
    save_role_users,
    save_team_members,
    team_members,
    update_team,
    update_user,
    user_role_ids,
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
    # 表格「角色」列：随用户列表一并返回，避免逐行查角色
    assert data["items"][0]["role_ids"] == ["r1"]


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


# ============================================================================
# 用户 CRUD / 角色配置 / 重置密码（对齐 data-synth UserManagerNeo）
# ============================================================================


def test_create_user_and_login_pwd_matches(sys_db) -> None:
    from api.lib.crypto import aes_encrypt

    res = create_user(
        sys_db,
        {"userId": "wang_wu", "userName": "王五", "pwd": "Abc@1234", "state": "1"},
    )
    assert res["success"] is True
    row = sys_db.execute(select(User).where(User.user_id == "wang_wu")).scalars().first()
    assert row is not None
    # 登录校验是 user_pwd == aes_encrypt(明文)
    assert row.user_pwd == aes_encrypt("Abc@1234")


def test_create_user_rejects_dup_and_empty(sys_db) -> None:
    assert create_user(sys_db, {"userId": "zhang_san", "pwd": "x"})["success"] is False
    assert create_user(sys_db, {"userId": "", "pwd": "x"})["success"] is False
    assert create_user(sys_db, {"userId": "new1", "pwd": ""})["success"] is False


def test_create_user_with_roles(sys_db) -> None:
    res = create_user(
        sys_db, {"userId": "new2", "userName": "新二", "pwd": "p", "roleIds": ["r1"]}
    )
    assert res["success"] is True
    assert user_role_ids(sys_db, "new2") == ["r1"]


def test_update_user_fields(sys_db) -> None:
    res = update_user(
        sys_db,
        "zhang_san",
        {"userName": "张三改", "email": "new@x.com", "phone": "138", "state": "0"},
    )
    assert res["success"] is True
    row = sys_db.execute(select(User).where(User.user_id == "zhang_san")).scalars().first()
    assert row.user_name == "张三改"
    assert row.state == "0"
    assert update_user(sys_db, "nobody", {})["success"] is False


def test_reset_user_pwd(sys_db) -> None:
    from api.lib.crypto import aes_encrypt

    assert reset_user_pwd(sys_db, "li_si", "NewPwd@1")["success"] is True
    row = sys_db.execute(select(User).where(User.user_id == "li_si")).scalars().first()
    assert row.user_pwd == aes_encrypt("NewPwd@1")
    assert reset_user_pwd(sys_db, "li_si", "")["success"] is False
    assert reset_user_pwd(sys_db, "nobody", "x")["success"] is False


def test_assign_user_roles_overwrite(sys_db) -> None:
    assert assign_user_roles(sys_db, "li_si", ["r1"])["success"] is True
    assert user_role_ids(sys_db, "li_si") == ["r1"]
    assert assign_user_roles(sys_db, "li_si", ["r1", "r2"])["success"] is True
    assert sorted(user_role_ids(sys_db, "li_si")) == ["r1", "r2"]
    assert assign_user_roles(sys_db, "li_si", ["nope"])["success"] is False


def test_delete_user_cascades_relations(sys_db) -> None:
    sys_db.add(TeamMember(member_id="tm1", team_name="ROOT", user_id="li_si"))
    sys_db.commit()
    assert delete_user(sys_db, "li_si")["success"] is True
    assert sys_db.execute(select(User).where(User.user_id == "li_si")).scalars().first() is None
    assert user_role_ids(sys_db, "li_si") == []
    assert (
        sys_db.execute(select(TeamMember).where(TeamMember.user_id == "li_si")).scalars().first()
        is None
    )
    assert delete_user(sys_db, "li_si")["success"] is False


# ============================================================================
# 团队 CRUD + 成员（对齐 data-synth TeamManagerZj）
# ============================================================================


def test_create_team_with_parent(sys_db) -> None:
    res = create_team(
        sys_db,
        {"teamName": "T1", "label": "团队一", "parentTeamName": "ROOT", "state": "1"},
    )
    assert res["success"] is True
    row = sys_db.execute(select(Team).where(Team.team_name == "T1")).scalars().first()
    assert row.parent_team_name == "ROOT"
    assert row.parent_team_id == "t1"
    # dup / bad parent
    assert create_team(sys_db, {"teamName": "T1"})["success"] is False
    assert create_team(sys_db, {"teamName": "T2", "parentTeamName": "NOPE"})["success"] is False


def test_update_team(sys_db) -> None:
    assert update_team(sys_db, "ROOT", {"label": "根改", "state": "0"})["success"] is True
    row = sys_db.execute(select(Team).where(Team.team_name == "ROOT")).scalars().first()
    assert row.label == "根改"
    assert row.state == "0"
    assert update_team(sys_db, "NOPE", {})["success"] is False


def test_delete_team_cascades(sys_db) -> None:
    sys_db.add(TeamMember(member_id="tm1", team_name="ROOT", user_id="zhang_san"))
    sys_db.execute(select(User).where(User.user_id == "zhang_san"))
    u = sys_db.execute(select(User).where(User.user_id == "zhang_san")).scalars().first()
    u.default_team = "ROOT"
    sys_db.commit()
    # ROOT protected
    assert delete_team(sys_db, "ROOT")["success"] is False
    create_team(sys_db, {"teamName": "T9", "label": "九"})
    assert delete_team(sys_db, "T9")["success"] is True
    # deleting ROOT is blocked; create a child team of ROOT and delete it, clearing default_team
    create_team(sys_db, {"teamName": "T10", "label": "十", "parentTeamName": "ROOT"})
    u.default_team = "T10"
    sys_db.commit()
    assert delete_team(sys_db, "T10")["success"] is True
    assert u.default_team == ""


def test_team_members_save(sys_db) -> None:
    assert save_team_members(sys_db, "ROOT", ["zhang_san", "li_si"])["success"] is True
    data = team_members(sys_db, "ROOT")
    assert data["total"] == 2
    names = sorted(x["user_id"] for x in data["items"])
    assert names == ["li_si", "zhang_san"]
    # overwrite semantics
    assert save_team_members(sys_db, "ROOT", ["li_si"])["success"] is True
    assert team_members(sys_db, "ROOT")["total"] == 1
    assert save_team_members(sys_db, "ROOT", ["ghost"])["success"] is False
    assert save_team_members(sys_db, "NOPE", [])["success"] is False


# ============================================================================
# 菜单 API 权限（对齐 data-synth 菜单服务授权）
# ============================================================================


def test_menu_apis_roundtrip(sys_db) -> None:
    res = save_menu_apis(
        sys_db,
        "m1",
        [{"path": "/api/v1/kbs", "method": "get"}, {"path": " /api/v1/kbs/search ", "method": ""}],
    )
    assert res["success"] is True
    got = get_menu_apis(sys_db, "m1")
    assert got["success"] is True
    apis = got["data"]["apis"]
    assert apis == [
        {"path": "/api/v1/kbs", "method": "GET"},
        {"path": "/api/v1/kbs/search", "method": "GET"},
    ]
    assert menu_api_perms(sys_db, "m1") == apis
    assert get_menu_apis(sys_db, "NOPE")["success"] is False


def test_menu_apis_blank_entries_dropped(sys_db) -> None:
    save_menu_apis(sys_db, "m2", [{"path": "", "method": "GET"}, {"path": "/api/v1/system"}])
    apis = menu_api_perms(sys_db, "m2")
    assert apis == [{"path": "/api/v1/system", "method": "GET"}]


def test_menu_apis_none_is_empty(sys_db) -> None:
    assert menu_api_perms(sys_db, "m3") == []
    assert get_menu_apis(sys_db, "m3")["data"]["apis"] == []
    assert is_platform_admin(sys_db, "zhang_san", admin_users=[]) is False


def test_is_admin_team_member_admin_not_in_first_row(sys_db) -> None:
    """is_admin 必须逐行检查团队角色：admin 行不在首行时也要判为管理员。

    回归：api/services/scope.py 旧实现用 .first() 只看第一行团队角色，
    当用户同时持有 member/admin/kb_role 多行（线上 huqiang 即此结构）而
    admin 排在后面时，被误判为非 admin → 本体 Schema 导入等接口 403。
    """
    from api.services.scope import is_admin

    # zhang_san 关联角色 r1（role_name=管理员，非 admin/system）→ 不满足角色路径
    db = sys_db
    # 构造多行团队角色：member 在前、admin 在后、再跟一个普通角色
    db.add(
        TeamMember(
            member_id="tm_s1", team_name="ROOT", user_id="zhang_san", role_name="member", state="1"
        )
    )
    db.add(
        TeamMember(
            member_id="tm_s2", team_name="ROOT", user_id="zhang_san", role_name="admin", state="1"
        )
    )
    db.add(
        TeamMember(
            member_id="tm_s3", team_name="ROOT", user_id="zhang_san", role_name="kb_role", state="1"
        )
    )
    db.commit()

    # 团队角色路径命中 admin → True
    assert is_admin(db, "zhang_san") is True

    # 对照：只有 member 行 → False
    db.add(
        TeamMember(
            member_id="tm_l1", team_name="ROOT", user_id="li_si", role_name="member", state="1"
        )
    )
    db.commit()
    assert is_admin(db, "li_si") is False