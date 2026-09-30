"""System admin service tests — users / roles / teams / menus / operation logs."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import (
    Menu,
    OperLog,
    Team,
    User,
    UserRole,
    UserRoleRela,
)
from api.services.system_admin import (
    list_menus,
    list_operation_logs,
    list_roles,
    list_teams,
    list_users,
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
            UserRole(role_id="r1", role_name="管理员", role_type="admin", state="1"),
            UserRole(role_id="r2", role_name="只读", role_type="user", state="1"),
            UserRoleRela(rela_id="x1", role_id="r1", user_id="u1"),
            Team(team_id="t1", team_name="ROOT", label="根团队", state="1"),
            Menu(menu_id="m1", menu_name="kbs", menu_label="知识库", route="/kbs", sort_num=1),
            Menu(menu_id="m2", menu_name="system", menu_label="系统管理", route="/system", sort_num=9),
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
    assert data["total"] == 2
    assert data["items"][0]["route"] == "/kbs"  # sort_num asc


def test_list_operation_logs(sys_db) -> None:
    data = list_operation_logs(sys_db)
    assert data["total"] == 1
    assert data["items"][0]["oper_type"] == "LOGIN"


def test_user_roles_resolution(sys_db) -> None:
    assert user_roles(sys_db, "u1") == ["管理员"]
    assert user_roles(sys_db, "u2") == []  # no rela rows