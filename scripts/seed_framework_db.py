#!/usr/bin/env python
"""在 kb-mysql 框架库建表 + 种子（部署后首次初始化用）。

用法:
  cd /home/ctyun/kb_compilation && uv run python scripts/seed_framework_db.py

需要环境（compose 已注入，脚本不自己连 .env）:
  DATABASE_URL        mysql+pymysql://...  (框架库)
  或在本机直跑时先 export 后执行。
"""
from __future__ import annotations

import os
import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.lib.crypto import aes_encrypt
from api.models.framework import (
    Base,
    Menu,
    RoleMenuRela,
    Team,
    TeamMember,
    User,
    UserRole,
    UserRoleRela,
)

# 种子账号（README: huqiang/sys）
SEED_USER_ID = os.getenv("KB_SEED_USER_ID", "huqiang")
SEED_PASSWORD = os.getenv("KB_SEED_PASSWORD", "sys")
SEED_TEAM = os.getenv("KB_SEED_TEAM", "默认团队")
SEED_ROLE = os.getenv("KB_SEED_ROLE", "admin")

# kb 两级菜单（对齐 data-synth root_data_synth 结构：1 顶级 + 子菜单）
KB_MENUS: list[tuple[str, str, str, str | None, str | None]] = [
    # (menu_id, menu_name, menu_label, route, parent_id)
    ("root_kb", "kb", "知识库平台", None, None),
    ("kbs", "kbs", "知识库管理", "/kbs", "root_kb"),
    ("chat", "chat", "智能问答", "/chat", "root_kb"),
    ("agents", "agents", "智能体配置", "/agents", "root_kb"),
    ("datasources", "datasources", "数据源", "/datasources", "root_kb"),
    ("wiki", "wiki", "Wiki 总览", "/wiki", "root_kb"),
    ("jobs", "jobs", "任务监控", "/jobs", "root_kb"),
    ("models", "models", "模型配置", "/models", "root_kb"),
    ("system", "system", "系统管理", "/system", "root_kb"),
]


def main() -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("KB_DATABASE_URL")
    if not url:
        raise SystemExit("Missing DATABASE_URL (run inside compose, or export first)")
    assert "mysql" in url or "postgres" in url, f"unexpected DATABASE_URL scheme: {url}"

    db = get_sessionmaker()()
    try:
        # 1) 建框架表（幂等）
        Base.metadata.create_all(bind=db.get_bind())
        print("[1/4] 框架表 create_all OK")

        # 2) 用户
        existing = db.execute(
            select(User).where(User.user_id == SEED_USER_ID)
        ).scalars().first()
        if existing:
            print(f"[2/4] 用户已存在: {SEED_USER_ID} (skip)")
        else:
            db.add(
                User(
                    id=uuid.uuid4().hex,
                    user_id=SEED_USER_ID,
                    user_name=SEED_USER_ID,
                    user_pwd=aes_encrypt(SEED_PASSWORD),
                    state="1",
                    default_team=SEED_TEAM,
                )
            )
            print(f"[2/4] 种子用户: {SEED_USER_ID}/{SEED_PASSWORD} (AES 加密)")
        db.commit()

        # 3) 团队 + 成员（identity 校验需要）
        team = db.execute(
            select(Team).where(Team.team_name == SEED_TEAM)
        ).scalars().first()
        if not team:
            db.add(
                Team(
                    team_id=uuid.uuid4().hex,
                    team_name=SEED_TEAM,
                    label=SEED_TEAM,
                    state="1",
                )
            )
            print(f"[3/4] 种子团队: {SEED_TEAM}")
        db.commit()
        member = db.execute(
            select(TeamMember).where(
                TeamMember.user_id == SEED_USER_ID,
                TeamMember.team_name == SEED_TEAM,
            )
        ).scalars().first()
        if not member:
            db.add(
                TeamMember(
                    member_id=uuid.uuid4().hex,
                    user_id=SEED_USER_ID,
                    team_name=SEED_TEAM,
                    role_name=SEED_ROLE,
                    state="1",
                )
            )
            print(f"[3/4] 种子成员: {SEED_USER_ID} @ {SEED_TEAM} ({SEED_ROLE})")
        db.commit()

        # 4) 角色 + 关联
        role = db.execute(
            select(UserRole).where(UserRole.role_name == SEED_ROLE)
        ).scalars().first()
        if not role:
            role = UserRole(
                role_id=uuid.uuid4().hex,
                role_name=SEED_ROLE,
                role_type="system",
                state="1",
            )
            db.add(role)
            db.commit()
        rel = db.execute(
            select(UserRoleRela).where(
                UserRoleRela.user_id == SEED_USER_ID,
                UserRoleRela.role_id == role.role_id,
            )
        ).scalars().first()
        if not rel:
            db.add(
                UserRoleRela(
                    rela_id=uuid.uuid4().hex,
                    user_id=SEED_USER_ID,
                    role_id=role.role_id,
                )
            )
            db.commit()
        print(f"[4/4] 角色/关联 OK: {SEED_ROLE}")

        # 5) kb 菜单树 + 全部关联到种子角色（幂等）
        menu_role = db.execute(
            select(UserRole).where(UserRole.role_name == SEED_ROLE)
        ).scalars().first()
        for mid, mname, mlabel, route, parent in KB_MENUS:
            existing = db.execute(
                select(Menu).where(Menu.menu_id == mid)
            ).scalars().first()
            if not existing:
                db.add(
                    Menu(
                        menu_id=mid,
                        menu_name=mname,
                        menu_label=mlabel,
                        menu_type="frame",
                        route=route,
                        parent_id=parent,
                        sort_num=KB_MENUS.index((mid, mname, mlabel, route, parent)),
                        state="1",
                    )
                )
            if menu_role:
                rel = db.execute(
                    select(RoleMenuRela).where(
                        RoleMenuRela.role_id == menu_role.role_id,
                        RoleMenuRela.menu_id == mid,
                    )
                ).scalars().first()
                if not rel:
                    db.add(
                        RoleMenuRela(
                            rela_id=uuid.uuid4().hex,
                            role_id=menu_role.role_id,
                            menu_id=mid,
                        )
                    )
        db.commit()
        print(f"[5/5] kb 菜单树 OK: {len(KB_MENUS)} 项 (root_kb + 8 子菜单) → 角色 {SEED_ROLE}")

        print("\nDONE — 登录: " + SEED_USER_ID + "/" + SEED_PASSWORD)
    finally:
        db.close()


if __name__ == "__main__":
    main()