"""把 KB 平台菜单图标换成 modo-design 彩色图标（对齐山东数据供给平台菜单形态），幂等可重放。

背景（2026-10-16）：
  山东数据供给平台（data_supply_shandong）侧边栏菜单使用 modo-design 彩色图标
  （Icon*Color，viewBox 0 0 12 12、currentColor 分层 opacity）。KB 平台此前全用
  @ant-design/icons 单色线框图标，与山东平台观感不一致。本次为语义可对应的 10 个
  菜单项换用同族彩色图标；前端 web/src/components/ModoMenuIcons.tsx 内联官方 SVG
  路径（不做自绘），AppSider ICON_MAP 按 menu_icon 名取组件，菜单管理页下拉与后端
  MENU_ICONS 目录同步补齐这 9 个图标名。

匹配口径（menu_name → modo 图标名）：
  - 用 menu_name（菜单唯一键）匹配，不写死 menu_id——同一菜单在不同环境 menu_id 形态
    不一致（本部署 files/workers 是 UUID，其它是语义名），按 menu_id 会漏改。
  - 语义对位山东平台：知识库=IconBook1Color、智能问答/智能体=IconSemanticSearchColor、
    运维监控/任务监控=IconTaskMonitorColor、系统管理=IconSystemMgrColor、
    数据源=IconDatabaseColor、文件管理/主机监控=IconCloudServerColor（山东：云盘）、
    编排管理=IconResourceOrchestrationColor（山东：知识编排）。
  - 仅改这 10 项，其余菜单图标保持原值（antd 图标，未纳入本次范围）。

用法（与其它 apply_* 脚本一致，容器内跑，勿在宿主直连库）：
  docker cp scripts/apply_menu_modo_icons.py kb-api-server:/tmp/ && \
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_menu_modo_icons.py

幂等：仅当 menu_icon 与目标不一致才写；重放第二次报告 0 更新。
新建环境无需本脚本——scripts/seed_framework_db.py 的 KB_MENUS 已直接写入这些图标名。
"""
from __future__ import annotations

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.framework import Menu

# menu_name -> (目标图标名, 说明)
ICON_MAPPING: dict[str, tuple[str, str]] = {
    "kbs": ("IconBook1Color", "知识库（山东：语料库/知识库）"),
    "files": ("IconCloudServerColor", "文件管理（山东：云盘）"),
    "chat": ("IconSemanticSearchColor", "智能问答（山东：语义搜索/智能体）"),
    "agents": ("IconSemanticSearchColor", "智能体"),
    "grp_ops": ("IconTaskMonitorColor", "运维监控（分组）"),
    "jobs": ("IconTaskMonitorColor", "任务监控"),
    "workers": ("IconCloudServerColor", "主机监控（山东：云盘/主机）"),
    "datasources": ("IconDatabaseColor", "数据源管理（山东：知识集）"),
    "system": ("IconSystemMgrColor", "系统管理"),
    "orch_tapes": ("IconResourceOrchestrationColor", "编排管理（山东：知识编排）"),
}


def main() -> None:
    db = get_sessionmaker()()
    try:
        rows = db.execute(select(Menu)).scalars().all()
        by_name = {m.menu_name: m for m in rows}
        updated = skipped = missing = 0
        for menu_name, (target, label) in ICON_MAPPING.items():
            menu = by_name.get(menu_name)
            if menu is None:
                missing += 1
                print(f"[missing] menu_name={menu_name}（{label}）库中不存在，跳过")
                continue
            current = menu.menu_icon or ""
            if current == target:
                skipped += 1
                print(f"[skip] {menu.menu_id}（{label}）已是 {target}")
                continue
            menu.menu_icon = target
            updated += 1
            print(f"[update] {menu.menu_id} / {menu_name}（{label}）{current or '<空>'} → {target}")
        db.commit()

        # 校验：逐条回读
        print("\n[校验] 回读各菜单图标：")
        for menu_name, (target, label) in ICON_MAPPING.items():
            menu = db.execute(select(Menu).where(Menu.menu_name == menu_name)).scalar_one_or_none()
            got = (menu.menu_icon if menu else None) or "<行不存在>"
            flag = "OK" if got == target else "!!"
            print(f"  {flag} {menu_name}: {got}")
        print(f"\n完成：更新 {updated} / 已一致 {skipped} / 缺行 {missing}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
