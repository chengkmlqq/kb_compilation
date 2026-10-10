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

import json
import os
import re
import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.lib.crypto import aes_encrypt
from api.models.framework import (
    Base,
    CronTask,
    Menu,
    RoleMenuRela,
    Team,
    TeamMember,
    User,
    UserRole,
    UserRoleRela,
)

# 种子账号（README: admin/sys）
# 2026-10-08: 默认账号由 huqiang 改为 admin（可用 KB_SEED_USER_ID 覆盖）。
# 注意：已部署环境里的旧账号不会被 seed 改名/删除（会破坏其知识库归属等外键引用）。
SEED_USER_ID = os.getenv("KB_SEED_USER_ID", "admin")
SEED_PASSWORD = os.getenv("KB_SEED_PASSWORD", "sys")
SEED_TEAM = os.getenv("KB_SEED_TEAM", "默认团队")
# 种子角色：默认与已部署环境一致（kb_role），已存在则复用不重建。
# 可用环境变量 KB_SEED_ROLE 覆盖。
SEED_ROLE = os.getenv("KB_SEED_ROLE", "kb_role")

# KB 菜单树（4 顶级分组 + 21 页面 = 25 项；sort_num 决定同级顺序）
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
    ("orch_tapes", "orch_tapes", "编排管理", "/orchestrations", "grp_data", "ApartmentOutlined", 6),
    ("orch_defines", "orch_defines", "编排组件", "/orchestrations/steps", "grp_data", "BlockOutlined", 7),
    ("chunk_verify", "chunk_verify", "切片核对", "/chunk-verify", "grp_data", "SafetyOutlined", 8),
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
    ("ont_schemas", "ont_schemas", "本体Schema", "/ontology-schemas", "system", "ApartmentOutlined", 10),
    ("sys_engines", "sys_engines", "引擎与存储", "/system/engines", "system", "CloudServerOutlined", 13),
    ("sys_dims", "sys_dims", "参数管理", "/system/dims", "system", "DatabaseOutlined", 14),
    (
        "sys_metadata_collection",
        "sys_metadata_collection",
        "元数据采集",
        "/system/metadata-collection",
        "system",
        "DatabaseOutlined",
        15,
    ),
]

# 除种子角色外，这些角色（若存在）同样授权全量 KB 菜单，
# 保证管理员/普通用户登录后侧栏都是完整菜单。
SEED_GRANT_ROLES = (SEED_ROLE, "plat-mgr", "normal_user")


# 编排内置组件定义（迁移自 data-synth algorithm/steps；step_cfg = dynamic-form FormField[]）
ORCH_STEP_DEFINES: list[tuple[str, str, str, str, str, list[dict], int]] = [
    ("def", "变量定义", "基础", "VariableOutlined",
     "赋值变量到运行上下文（{{ }} 模板引用；支持 JSON 数组或单变量）",
     [{"name": "assignments", "label": "变量赋值(JSON)", "type": "textarea", "required": True,
       "placeholder": '[{"variable":"x","expression":"10"},{"variable":"y","expression":"x*2"}]',
       "props": {"rows": 4}}], 1),
    ("script", "脚本执行", "基础", "CodeOutlined",
     "执行任意 Python 代码（注入 context / bindings / config，可读写变量）",
     [{"name": "code", "label": "Python 代码", "type": "textarea", "required": True,
       "placeholder": "print('hello')\nbindings['res'] = 123",
       "props": {"rows": 8}}], 2),
    ("print", "日志输出", "基础", "MessageOutlined",
     "输出文本到执行日志（支持 {{ }} 变量引用）",
     [{"name": "text", "label": "输出内容", "type": "textarea", "required": True,
       "placeholder": "当前结果: {{res}}"}], 3),
    ("if", "条件分支", "流程控制", "NodeIndexOutlined",
     "按条件真/假走不同后继：真→后继节点，假→跳过（condition 支持 {{ }} 与表达式）",
     [{"name": "condition", "label": "条件表达式", "type": "input", "required": True,
       "placeholder": "len(bindings.get('items',[])) > 3"}], 4),
    ("loop", "循环", "流程控制", "SyncOutlined",
     "迭代集合执行后继子图：每轮设置 {{item_var}} 后重跑连线下游（支持 {{ }} 集合表达式）",
     [{"name": "item_var", "label": "迭代变量名", "type": "input", "required": True, "placeholder": "item"},
      {"name": "collection", "label": "集合表达式", "type": "input", "required": True,
       "placeholder": "[1,2,3] 或 bindings 里的列表变量"}], 5),
    ("run_script", "脚本执行(子进程)", "基础", "CodeOutlined",
     "在节点所在 worker 以子进程运行脚本（技能 run_one.py / build_full.py），支持超时与 env 注入",
     [{"name": "script_path", "label": "脚本路径(worker内置)", "type": "input", "required": False,
       "placeholder": "/srv/kb/worker/builtin_engine/scripts/run_one.py"},
      {"name": "args", "label": "脚本参数(JSON数组,支持{{}})", "type": "textarea", "required": False,
       "placeholder": '["{{kid}}", "--kb", "{{kb_id}}"]'},
      {"name": "command", "label": "或直接命令行(argv列表)", "type": "textarea", "required": False,
       "placeholder": '["python3", "script.py", "--flag"]'},
      {"name": "env", "label": "附加环境变量(JSON)", "type": "textarea", "required": False,
       "placeholder": '{"WEKNORA_KB_ID": "{{kb_id}}", "WEKNORA_DOC_NAME": "{{doc_name}}"}',
       "props": {"rows": 3}},
      {"name": "timeout_sec", "label": "超时(秒,0=不限)", "type": "input", "required": False, "placeholder": "1800"},
      {"name": "queue", "label": "执行队列", "type": "select", "required": False,
       "options": [
           {"label": "默认", "value": ""},
           {"label": "解析/通用 (default)", "value": "default"},
           {"label": "Agent (agent)", "value": "agent"},
           {"label": "构建 (build)", "value": "build"},
           {"label": "编排调度 (orch)", "value": "orch"},
       ]}], 6),
    # ── 业务原子（2026-10-09）：wiki 构建拆出的通用组件，实现在
    #    worker/builtin_engine/scripts/orch_atoms.py，以 run_script 语义被调用
    ("llm_extract", "LLM抽取", "知识构建", "RobotOutlined",
     "按 prompt 模板分批调用 LLM 做结构化抽取，输出 JSON（list/map/lines 三种形状），"
     "失败批次显式记账不静默产出空结果",
     [{"name": "source", "label": "输入JSON(记录数组)", "type": "input", "required": True,
       "placeholder": "/tmp/chunks_kid.json"},
      {"name": "source_key", "label": "取文本字段", "type": "input", "required": False,
       "placeholder": "text（缺失回退 content/chunk）"},
      {"name": "prompt", "label": "Prompt模板(支持{{items}})", "type": "textarea", "required": False,
       "placeholder": "从以下片段抽取…\n{{items}}", "props": {"rows": 4}},
      {"name": "prompt_file", "label": "或Prompt模板文件路径", "type": "input", "required": False,
       "placeholder": "/srv/kb/prompts/extract.txt"},
      {"name": "output", "label": "输出JSON路径", "type": "input", "required": True,
       "placeholder": "/tmp/extracted_kid.json"},
      {"name": "output_shape", "label": "输出形状", "type": "select", "required": False,
       "options": [{"label": "JSON数组 (list)", "value": "list"},
                   {"label": "键值映射 (map)", "value": "map"},
                   {"label": "纯文本行 (lines)", "value": "lines"}]},
      # map 形状必需（orch_atoms.llm_extract 里 output_shape=map 会 _die 校验）——
      # 2026-10-09 回归修复：原表单缺这两个字段，UI 选「键值映射」必然失败。
      {"name": "output_key_field", "label": "map形状-key字段", "type": "input", "required": False,
       "placeholder": "name（map 形状时必填）"},
      {"name": "output_value_field", "label": "map形状-value字段", "type": "input", "required": False,
       "placeholder": "desc（map 形状时必填）"},
      {"name": "batch_size", "label": "每批条数", "type": "input", "required": False, "placeholder": "8"},
      {"name": "max_workers", "label": "并发线程", "type": "input", "required": False, "placeholder": "2"},
      {"name": "max_tokens", "label": "max_tokens", "type": "input", "required": False, "placeholder": "8000"},
      {"name": "retries", "label": "单批重试次数", "type": "input", "required": False, "placeholder": "3"},
      {"name": "dedupe_field", "label": "去重字段(可选)", "type": "input", "required": False, "placeholder": "text"}], 7),
    ("wiki_publish", "知识入库", "知识构建", "UploadOutlined",
     "按 slug 策略与页面类型幂等建页（read-first + create 竞态回退），"
     "支持长句/关键词/实体/规则等页面类型，目录按名解析",
     [{"name": "source", "label": "输入JSON", "type": "input", "required": True,
       "placeholder": "/tmp/ls_kid.json"},
      {"name": "folder", "label": "目标目录名", "type": "input", "required": False,
       "placeholder": "长句原文（按名解析，支持多层）"},
      {"name": "page_type", "label": "页面类型", "type": "select", "required": False,
       "options": [{"label": "长句原文", "value": "original_sentence"},
                   {"label": "高频关键词", "value": "frequent_keyword"},
                   {"label": "业务实体", "value": "entity-b"},
                   {"label": "规则实体", "value": "entity-r"}]},
      {"name": "slug_prefix", "label": "slug前缀", "type": "input", "required": True,
       "placeholder": "longsentence"},
      {"name": "slug_hash_field", "label": "slug哈希字段", "type": "input", "required": False,
       "placeholder": "text（实体页用 title）"},
      {"name": "title_field", "label": "标题字段", "type": "input", "required": False, "placeholder": "title"},
      {"name": "content_template", "label": "内容模板(支持{{title}}/{{text}})", "type": "textarea",
       "required": False, "placeholder": "# {{title}}\n\n{{text}}", "props": {"rows": 4}},
      {"name": "source_ref", "label": "来源引用(kid|文件名)", "type": "input", "required": False,
       "placeholder": "{{kid}}|制度.docx"},
      {"name": "dry_run", "label": "仅统计不写库", "type": "boolean", "required": False}], 8),
]


def _seed_step_defines(db) -> tuple[int, int]:
    from api.models.orchestration import StepDefine

    created = updated = 0
    for inst, label, group, icon, desc, cfg, seq in ORCH_STEP_DEFINES:
        existing = db.execute(
            select(StepDefine).where(StepDefine.step_inst == inst)
        ).scalars().first()
        if not existing:
            db.add(
                StepDefine(
                    id=uuid.uuid4().hex[:64],
                    group_type=group,
                    step_inst=inst,
                    step_label=label,
                    step_icon=icon,
                    step_desc=desc,
                    step_cfg=cfg,
                    step_seq=seq,
                    status="effective",
                )
            )
            created += 1
        else:
            changed = False
            for field, value in (
                ("group_type", group),
                ("step_label", label),
                ("step_icon", icon),
                ("step_desc", desc),
                ("step_cfg", cfg),
                ("step_seq", seq),
                ("status", "effective"),
            ):
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if changed:
                updated += 1
    db.commit()
    return created, updated


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
            f"[5/6] KB 菜单树种子 OK: 共 {len(KB_MENUS)} 项 "
            f"(顶级分组 {sum(1 for _, _, _, _, p, *_ in KB_MENUS if not p)} 个 + 页面 {sum(1 for _, _, _, _, p, *_ in KB_MENUS if p)} 个) "
            f"[新建 {created} / 修正 {updated} / 新增授权 {granted}]"
        )

        # 6) 日志清理定时任务（幂等）：每天 03:00 清理过期操作日志与任务记录
        existing_cron = db.execute(
            select(CronTask).where(CronTask.task_class == "KbLogCleanupTask")
        ).scalars().first()
        if existing_cron:
            print("[6/6] 日志清理 cron 已存在 (skip)")
        else:
            db.add(
                CronTask(
                    id=uuid.uuid4().hex,
                    name="每日日志清理",
                    label="清理过期操作日志与任务记录",
                    cron_expression="0 3 * * *",
                    state="1",
                    task_class="KbLogCleanupTask",
                    fire_params=json.dumps(
                        {"retention_days": 90, "job_retention_days": 90}, ensure_ascii=False
                    ),
                    queue_name="default",
                )
            )
            db.commit()
            print("[6/6] 日志清理 cron 种子 OK: 每天 03:00, 保留 90 天")

        # 7) 编排内置组件定义（幂等 upsert）
        d_created, d_updated = _seed_step_defines(db)
        print(f"[7/7] 编排组件定义种子 OK: 内置 {len(ORCH_STEP_DEFINES)} 个 [新建 {d_created} / 修正 {d_updated}]")

        print("\nDONE — 登录: " + SEED_USER_ID + "/" + SEED_PASSWORD + "（编排内置组件 " + str(len(ORCH_STEP_DEFINES)) + " 个）")
    finally:
        db.close()


if __name__ == "__main__":
    main()