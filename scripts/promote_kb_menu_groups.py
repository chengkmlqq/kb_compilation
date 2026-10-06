#!/usr/bin/env python
"""菜单结构迁移（方案A）：4 个分组提升为顶级，root_kb「知识库平台」停用。

背景（2026-10-06）：
  顶部 Header 顶级导航原只有 root_kb「知识库平台」一个顶级项，与左上角
  Logo 品牌区重复。本脚本把 grp_knowledge/grp_ai/grp_data/system 4 个分组
  的 parent_id 从 root_kb 提升为顶级（NULL），并将 root_kb 停用（state='0'，
  保留行防 RoleMenuRela 外键与审计）。

用法（与 seed_framework_db.py 同风格，env 注入 DATABASE_URL）：
  cd /home/jenkins/chengkai/kb_compilation && uv run python scripts/promote_kb_menu_groups.py

幂等：重复执行无副作用（条件匹配 + 打印影响行数）。
"""
from __future__ import annotations

import os

from sqlalchemy import select, update

from api.db import get_sessionmaker
from api.models.framework import Menu

PROMOTE_IDS = ("grp_knowledge", "grp_ai", "grp_data", "system")
ROOT_KB_ID = "root_kb"


def main() -> None:
    db = get_sessionmaker()()

    # 1) 4 分组提升为顶级（仅当仍挂在 root_kb 下）
    result = db.execute(
        update(Menu)
        .where(Menu.menu_id.in_(PROMOTE_IDS), Menu.parent_id == ROOT_KB_ID)
        .values(parent_id=None)
    )
    print(f"[1/3] 分组提升为顶级: {result.rowcount} 行 (grp_knowledge/grp_ai/grp_data/system)")

    # 2) root_kb 停用（保留行）
    result = db.execute(
        update(Menu)
        .where(Menu.menu_id == ROOT_KB_ID, Menu.state == "1")
        .values(state="0")
    )
    print(f"[2/3] root_kb 停用: {result.rowcount} 行")

    # 3) 校验：4 分组确认顶级、root_kb 确认停用
    rows = db.execute(
        select(Menu.menu_id, Menu.parent_id, Menu.state)
        .where(Menu.menu_id.in_(PROMOTE_IDS + (ROOT_KB_ID,)))
        .order_by(Menu.sort_num)
    ).all()
    print("[3/3] 校验:")
    for mid, parent, state in rows:
        print(f"  {mid}: parent={parent!r} state={state}")

    db.commit()
    db.close()
    print("完成。")


if __name__ == "__main__":
    main()