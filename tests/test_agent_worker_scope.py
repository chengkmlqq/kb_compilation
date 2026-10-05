"""agent worker 的 KB 权限解析测试：技能/MCP 按知识库创建者的个人/团队权限可见。"""
from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import KbDatasource
from api.models.mcp_skill import KbMcpServer, KbSkill
import worker.tasks.agent_worker as aw


@pytest.fixture()
def db_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)
    # agent_worker 顶层 `from api.db import get_sessionmaker` 已在 import 时绑定，
    # monkeypatch 模块属性对它无效（经典坑）——必须一并 patch 顶层名字。
    monkeypatch.setattr(aw, "get_sessionmaker", lambda: Session)
    # is_admin 查框架表（UserRoleRela 等），与本测试目标（技能/MCP 可见性）无关；
    # 统一视为非管理员，避免 pytest 收集期模块级状态差异
    monkeypatch.setattr("api.services.scope.is_admin", lambda db, uid: False)
    yield Session


def _seed(db, owner_uid="u-owner", owner_team="t-owner"):
    db.add(
        KbDatasource(
            id="kb-1", name="kb", state="1",
            owner_user_id=owner_uid, owner_team_name=owner_team, team_name=owner_team,
            scope="team", indexing_strategy={},
        )
    )
    # 技能：个人(owner 的)、团队(owner 的)、系统、他人个人（不可见）
    db.add(KbSkill(id="s1", name="my-personal-skill", scope="personal",
                   owner_user_id=owner_uid, state="1", package_zip=b"zip1", package_size=4))
    db.add(KbSkill(id="s2", name="team-skill", scope="team",
                   owner_team_name=owner_team, state="1", package_zip=b"zip2", package_size=4))
    db.add(KbSkill(id="s3", name="sys-skill", scope="system", state="1",
                   package_zip=b"zip3", package_size=4))
    db.add(KbSkill(id="s4", name="other-personal", scope="personal",
                   owner_user_id="someone-else", state="1", package_zip=b"zip4", package_size=4))
    # MCP：个人、团队、系统、他人个人
    db.add(KbMcpServer(id="m1", name="my-mcp", scope="personal", owner_user_id=owner_uid,
                       enabled=True, state="1", type="streamable_http", url="http://a"))
    db.add(KbMcpServer(id="m2", name="team-mcp", scope="team", owner_team_name=owner_team,
                       enabled=True, state="1", type="streamable_http", url="http://b"))
    db.add(KbMcpServer(id="m3", name="sys-mcp", scope="system",
                       enabled=True, state="1", type="streamable_http", url="http://c"))
    db.add(KbMcpServer(id="m4", name="other-mcp", scope="personal", owner_user_id="someone-else",
                       enabled=True, state="1", type="streamable_http", url="http://d"))
    db.commit()


def test_kb_owner_context_reads_owner(db_env) -> None:
    Session = db_env
    db = Session()
    _seed(db)
    ctx = aw._kb_owner_context(db, "kb-1")
    assert ctx == {"user_id": "u-owner", "team_name": "t-owner", "is_admin": False}
    db.close()


def test_kb_owner_context_missing_kb(db_env) -> None:
    Session = db_env
    db = Session()
    ctx = aw._kb_owner_context(db, "nope")
    assert ctx == {"user_id": "", "team_name": "", "is_admin": False}
    db.close()


def test_attach_skill_zip_uses_owner_visibility(db_env, monkeypatch) -> None:
    """技能解析按 owner 上下文：个人+团队+系统可见，他人的不可见。"""
    Session = db_env
    db = Session()
    _seed(db)

    # owner 绑定个人技能 → 取到
    cfg1 = {"skill": "my-personal-skill", "kb_id": "kb-1"}
    aw._attach_skill_zip(cfg1, kb_id="kb-1")
    assert cfg1.get("skill_zip") == b"zip1"

    # owner 绑定团队技能 → 取到
    cfg2 = {"skill": "team-skill", "kb_id": "kb-1"}
    aw._attach_skill_zip(cfg2, kb_id="kb-1")
    assert cfg2.get("skill_zip") == b"zip2"

    # 系统技能：平台预置资源，owner 非 admin 也可见（2026-10-05 语义）
    cfg3 = {"skill": "sys-skill", "kb_id": "kb-1"}
    aw._attach_skill_zip(cfg3, kb_id="kb-1")
    assert cfg3.get("skill_zip") == b"zip3"

    # 他人个人技能 → 不可见
    cfg4 = {"skill": "other-personal", "kb_id": "kb-1"}
    aw._attach_skill_zip(cfg4, kb_id="kb-1")
    assert cfg4.get("skill_zip") is None
    db.close()


def test_attach_mcp_servers_uses_owner_visibility(db_env) -> None:
    """MCP 按 owner 上下文解析：个人+团队可见，他人的不可见。"""
    Session = db_env
    db = Session()
    _seed(db)

    config = {"kb_id": "kb-1"}
    aw._attach_mcp_servers(config, kb_id="kb-1")
    names = [m["name"] for m in config.get("mcp_servers", [])]
    assert "my-mcp" in names, f"个人 MCP 应可见, got {names}"
    assert "team-mcp" in names, f"团队 MCP 应可见, got {names}"
    assert "sys-mcp" in names, f"系统 MCP(平台预置) 应可见, got {names}"
    assert "other-mcp" not in names, f"他人个人 MCP 不应可见, got {names}"
    db.close()
