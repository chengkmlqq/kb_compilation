#!/usr/bin/env python
"""在框架库建表 + 种子（部署后首次初始化用）。

用法:
  cd /home/jenkins/chengkai/kb_compilation && uv run python scripts/seed_framework_db.py

需要环境（compose 已注入，脚本不自己连 .env）:
  DATABASE_URL        mysql+pymysql://...  (框架库)
  或在本机直跑时先 export 后执行。

菜单种子说明:
  KB 菜单树是本平台的**种子数据**（与前端页面路由一一对应），初始化后由
  「系统管理 → 菜单管理 / 角色管理」维护，不再写死在代码里。前端
  FALLBACK_MENUS 仅在 my-menus 为空时兜底，正常运行以本表为准。
"""
from __future__ import annotations

import os
import re
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
# 种子角色：默认与已部署环境一致（kb_role），已存在则复用不重建。
# 可用环境变量 KB_SEED_ROLE 覆盖。
SEED_ROLE = os.getenv("KB_SEED_ROLE", "kb_role")

# KB 菜单树（4 顶级分组 + 13 页面 = 17 项；sort_num 决定同级顺序）
# (menu_id, menu_name, menu_label, route, parent_id, menu_icon, sort_num)
# menu_id 用固定值（workers/mcps/skills/cron 用生产环境已验证的 UUID），
# 保证多次初始化幂等、且与已部署环境完全一致。
#
# 2026-10-06 调整（方案A）：原 root_kb「知识库平台」顶级节点移除——左上角 Logo
# 品牌区已承担该语义，顶部 Header 顶级导航改为 4 个分组直接提升为顶级，
# 避免「知识库平台」标签重复出现。
# 结构：Header 顶级(nav)= 知识管理/智能应用/数据与任务/系统管理 → Sider 分组 → 页面。
#   - route 留空 = 纯目录分组（只展开不跳转，对齐 ds 的 dir 节点）
#   - route 有值且带子级 = 分组兼页面（点击自身跳该路由，对齐 ds「系统管理 /system/users」模式）
KB_MENUS: list[tuple[str, str, str, str | None, str | None, str, int]] = [
    # ---- 知识管理（纯目录，顶级） ----
    ("grp_knowledge", "grp_knowledge", "知识管理", None, None, "FolderOutlined", 1),
    ("kbs", "kbs", "知识库管理", "/kbs", "grp_knowledge", "AppstoreOutlined", 1),
    ("ac9b271c90b6450c92bd14e9681da520", "files", "文件管理", "/files", "grp_knowledge", "FileOutlined", 2),
    # ---- 智能应用（纯目录，顶级） ----
    ("grp_ai", "grp_ai", "智能应用", None, None, "FolderOutlined", 2),
    ("chat", "chat", "智能问答", "/chat", "grp_ai", "CommentOutlined", 1),
    ("agents", "agents", "智能体配置", "/agents", "grp_ai", "RobotOutlined", 2),
    ("d83aee0d1d8a4fce8a6bd121b6efc5fa", "websearch", "联网搜索", "/websearch", "grp_ai", "SearchOutlined", 3),
    # ---- 数据与任务（纯目录，顶级） ----
    ("grp_data", "grp_data", "数据与任务", None, None, "FolderOutlined", 3),
    ("datasources", "datasources", "数据源", "/datasources", "grp_data", "DatabaseOutlined", 1),
    ("jobs", "jobs", "任务监控", "/jobs", "grp_data", "DashboardOutlined", 2),
    ("4cd17410ab29495aa131cdf763fc3549", "cron", "任务管理", "/cron", "grp_data", "ScheduleOutlined", 3),
    ("b2d585de4d824b96bfed2a7798ad6880", "workers", "主机监控", "/workers", "grp_data", "CloudServerOutlined", 4),
    ("datagrid", "datagrid", "数据查询", "/datagrid", "grp_data", "TableOutlined", 5),
    # ---- 系统管理（分组兼页面，顶级：点击自身跳 /system → 重定向到默认子页 /system/users）。
    # 用户/角色/团队/菜单/日志均为独立页面路由（对齐 ds system/* 独立页面，无顶部 Tab 聚合页）。
    ("system", "system", "系统管理", "/system", None, "SettingOutlined", 4),
    ("sys_users", "sys_users", "用户管理", "/system/users", "system", "TeamOutlined", 1),
    ("sys_roles", "sys_roles", "角色管理", "/system/roles", "system", "SafetyOutlined", 2),
    ("sys_teams", "sys_teams", "团队管理", "/system/teams", "system", "PartitionOutlined", 3),
    ("sys_menus", "sys_menus", "菜单管理", "/system/menus", "system", "MenuOutlined", 4),
    ("sys_logs", "sys_logs", "操作日志", "/system/logs", "system", "ProfileOutlined", 5),    ("models", "models", "模型配置", "/models", "system", "CloudServerOutlined", 7),
    ("8829900dd6be4c45bc584df804ba0d4a", "mcps", "MCP 管理", "/mcps", "system", "ApiOutlined", 8),
    ("256b44596e6e43f5a847e0c3ae7b2ba0", "skills", "技能管理", "/skills", "system", "ToolOutlined", 9),
]

# 除种子角色外，这些角色（若存在）同样授权全量 KB 菜单，
# 保证管理员/普通用户登录后侧栏都是完整菜单。
SEED_GRANT_ROLES = (SEED_ROLE, "plat-mgr", "normal_user")


def main() -> None:
    # KB_DATABASE_URL 优先（compose 注入），其次 DATABASE_URL；
    # 打印实际目标库，避免误连到其它平台的库（本仓库根 .env 历史上指向 data-synth 的库）。
    url = os.environ.get("KB_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Missing KB_DATABASE_URL/DATABASE_URL (run inside compose, or export first)")
    m = re.search(r"//[^:]+:[^@]+@([^/:?]+)(?::(\d+))?/([^?]+)", url)
    if m:
        host, port, db_name = m.group(1), m.group(2) or "3306", m.group(3)
        print(f"[db] 目标库: {host}:{port}/{db_name}")
        if not db_name.lower().startswith("kb"):
            print("[db] ⚠️ 库名不是 kb* —— 请确认这是知识库平台自己的库，勿误写其它平台数据")
    assert "mysql" in url or "postgres" in url, f"unexpected DATABASE_URL scheme: {url}"

    db = get_sessionmaker()()
    try:
        # 1) 建框架表（幂等）
        Base.metadata.create_all(bind=db.get_bind())
        print("[1/5] 框架表 create_all OK")

        # 2) 用户
        existing = db.execute(
            select(User).where(User.user_id == SEED_USER_ID)
        ).scalars().first()
        if existing:
            print(f"[2/5] 用户已存在: {SEED_USER_ID} (skip)")
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
            print(f"[2/5] 种子用户: {SEED_USER_ID}/{SEED_PASSWORD} (AES 加密)")
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
            print(f"[3/5] 种子团队: {SEED_TEAM}")
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
            print(f"[3/5] 种子成员: {SEED_USER_ID} @ {SEED_TEAM} ({SEED_ROLE})")
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
        print(f"[4/5] 角色/关联 OK: {SEED_ROLE}")

        # 5) KB 菜单树种子（幂等 upsert）+ 授权给种子角色
        created = updated = 0
        for mid, mname, mlabel, route, parent, icon, sort in KB_MENUS:
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
                        menu_icon=icon,
                        sort_num=sort,
                        state="1",
                    )
                )
                created += 1
            else:
                # 已存在则补齐（幂等：修正 icon/sort/parent 等漂移）
                changed = False
                for field, value in (
                    ("menu_name", mname),
                    ("menu_label", mlabel),
                    ("route", route),
                    ("parent_id", parent),
                    ("menu_icon", icon),
                    ("sort_num", sort),
                    ("state", "1"),
                ):
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    updated += 1
        db.commit()

        # 授权：种子角色 + plat-mgr/normal_user（存在则一并授权）
        granted = 0
        for role_name in dict.fromkeys(SEED_GRANT_ROLES):
            r = db.execute(
                select(UserRole).where(UserRole.role_name == role_name)
            ).scalars().first()
            if not r:
                continue
            for mid, *_ in KB_MENUS:
                rel = db.execute(
                    select(RoleMenuRela).where(
                        RoleMenuRela.role_id == r.role_id,
                        RoleMenuRela.menu_id == mid,
                    )
                ).scalars().first()
                if not rel:
                    db.add(
                        RoleMenuRela(
                            rela_id=uuid.uuid4().hex,
                            role_id=r.role_id,
                            menu_id=mid,
                        )
                    )
                    granted += 1
        db.commit()
        print(
            f"[5/5] KB 菜单树种子 OK: 共 {len(KB_MENUS)} 项 "
            f"(顶级分组 {sum(1 for _, _, _, _, p, *_ in KB_MENUS if not p)} 个 + 页面 {sum(1 for _, _, _, _, p, *_ in KB_MENUS if p)} 个) "
            f"[新建 {created} / 修正 {updated} / 新增授权 {granted}]"
        )

        print("\nDONE — 登录: " + SEED_USER_ID + "/" + SEED_PASSWORD)
    finally:
        db.close()


if __name__ == "__main__":
    main()