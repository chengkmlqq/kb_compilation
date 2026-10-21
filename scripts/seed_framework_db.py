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

重新初始化菜单（恢复纯种子状态）:
  uv run python scripts/seed_framework_db.py --reset-menus
  该选项会**删除全部现有菜单与角色-菜单授权**（业务自定义菜单一并清除），
  再按 KB_MENUS 种子重建并重新授权种子角色；其余数据（用户/角色/团队/
  参数等）不受影响。不带参数则维持幂等 upsert（补缺/修正漂移，不删除）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import uuid

from sqlalchemy import delete, or_, select

from api.db import get_sessionmaker
from api.lib.crypto import aes_encrypt
from api.models.framework import (
    Base,
    CronTask,
    Dim,
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

# KB 菜单树（sort_num 决定同级顺序；每个菜单/目录都有对应图标）
# (menu_id, menu_name, menu_label, route, parent_id, menu_icon, sort_num)
# menu_id 用固定值（workers/mcps/skills/cron 用生产环境已验证的 UUID），
# 保证多次初始化幂等、且与已部署环境完全一致。
#
# 2026-10-06 调整（方案A）：原 root_kb「知识库平台」顶级节点移除——左上角 Logo
# 品牌区已承担该语义。
# 2026-10-10 调整（方案B）：切片核对 /chunk-verify 挪入知识管理（文档切片属知识域工具）。
# 2026-10-12 调整（方案C）：新增「运维监控」顶级分组，任务监控/任务管理/主机监控移入。
# 2026-10-12 调整（方案D）：
#   - 操作日志移入运维监控
#   - 新增顶级「配置管理」目录（运维监控之后），收拢模型配置/MCP/技能/参数管理
#   - 拆开「知识管理」「智能应用」两个目录，其下菜单直接提升为顶级（减少一层点击）
#   - 所有菜单/目录均配图标
# 2026-10-12 调整（方案E）：新增顶级「调试工具」目录（系统管理之后），收拢
# 引擎与存储/本体Schema/编排组件/联网搜索/切片核对（开发调试类页面集中）。
# 2026-10-12 调整（方案F）：
#   - 文件管理/数据查询(日常性工具) 移入调试工具；切片核对移入运维监控
#   - 改名：知识库管理→知识库、智能体配置→智能体、数据源→数据源管理
# 2026-10-12 调整（方案G）：
#   - 智能问答排到知识库前；数据源管理/联网搜索移入配置管理；元数据采集移入运维监控
# 2026-10-12 调整（方案H）：编排管理/编排模板 从顶级移入「调试工具」目录
#   （与编排组件相邻，编排域聚合在调试工具下）
# 2026-10-14 调整（方案I）：移除「引擎与存储」页面（/system/engines）及其菜单项
#   （解析引擎开关/存储类型/向量库类型/系统信息 页面下架；后端 /api/v1/system/* 接口保留）
# 2026-10-15 调整：移除「新版问答」页面（/chat-next，assistant-ui 试验页）——页面/组件/依赖
#   全量删除，恢复方案J 引入前的菜单（chat_next 已从 KB_MENUS 移除，顶级 sort_num 顺延还原）
# 2026-10-21 调整：新增顶级「语义层」（grp_semantic_layer，目录）——知识库（kbs，sort 2）之后插入，
#   子级：元数据/文档库/知识本体/知识图谱（/semantic/* 占位页，后续功能填充）；
#   后续顶级 sort_num 顺延（运维监控 5→6，配置管理 6→7，系统管理 7→8，调试工具 8→9）。
#   注：menu_id 用 grp_semantic_layer——2026-10-21 实测库内既有「语义资产管理」树占用 grp_semantic，
#   沿用会 upsert 覆写，故避开；该树（grp_semantic 目录 + 5 子项）无代码页面且非平台功能，
#   同日随本次调整一并下架（见 REMOVED_MENUS）。
# 默认隐藏菜单（state=0，菜单管理页可恢复启用）；chat_next 已随页面删除退出该集合
HIDDEN_MENUS: set[str] = set()
# 已下架菜单（2026-10-15 移除「新版问答」/chat-next 页面及其组件；
#  2026-10-21 移除库内遗留「语义资产管理」树——grp_semantic 顶级目录 + 5 个子菜单
#  business_extract/semantic_process/semantic_assets/version_release/conflict_handling，
#  均无对应代码页面，属旧业务迁移残留，非平台功能）：
# KB_MENUS 是幂等 upsert 语义——只增改不删，seed 里删掉条目不会动旧库残留行；
# 故 main() 里按此集合显式清理 modo_menu + modo_role_menu_rela（新库同样幂等无操作）。
REMOVED_MENUS: set[str] = {
    "chat_next",
    # 「语义资产管理」树（2026-10-21 下架）
    "grp_semantic",
    "business_extract",
    "semantic_process",
    "semantic_assets",
    "version_release",
    "conflict_handling",
}
# 结构：Header 顶级 = 总览/智能问答/知识库/智能体/高频词/语义层(目录)/
#         运维监控(目录)/配置管理(目录)/系统管理/调试工具(目录)。
#   - route 留空 = 纯目录分组（只展开不跳转）
#   - route 有值且带子级 = 分组兼页面（点击自身跳该路由）
KB_MENUS: list[tuple[str, str, str, str | None, str | None, str, int]] = [
    # ---- 顶级页面 ----
    ("overview", "overview", "总览", "/overview", None, "IconHomeColor", 0),
    ("chat", "chat", "智能问答", "/chat", None, "IconSemanticSearchColor", 1),
    ("kbs", "kbs", "知识库", "/kbs", None, "IconBook1Color", 2),
    ("agents", "agents", "智能体", "/agents", None, "IconSemanticSearchColor", 3),
    ("wiki_keywords", "wiki_keywords", "高频词", "/wiki-keywords", None, "BarChartOutlined", 4),
    # ---- 语义层（顶级目录：元数据/文档库/知识本体/知识图谱） ----
    # 注意：menu_id 用 grp_semantic_layer，避开库内既有「语义资产管理」树占用的
    # grp_semantic（2026-10-21 实测该 id 已被业务菜单占用，沿用会 upsert 覆写）。
    ("grp_semantic_layer", "grp_semantic_layer", "语义层", None, None, "ApartmentOutlined", 5),
    ("sem_metadata", "sem_metadata", "元数据", "/semantic/metadata", "grp_semantic_layer", "DatabaseOutlined", 1),
    ("sem_docs", "sem_docs", "文档库", "/semantic/docs", "grp_semantic_layer", "FileOutlined", 2),
    ("sem_ontology", "sem_ontology", "知识本体", "/semantic/ontology", "grp_semantic_layer", "ApartmentOutlined", 3),
    ("sem_graph", "sem_graph", "知识图谱", "/semantic/graph", "grp_semantic_layer", "PartitionOutlined", 4),
    # ---- 运维监控（顶级目录：任务监控/任务管理/主机监控/操作日志/切片核对/元数据采集） ----
    ("grp_ops", "grp_ops", "运维监控", None, None, "IconTaskMonitorColor", 6),
    ("jobs", "jobs", "任务监控", "/jobs", "grp_ops", "IconTaskMonitorColor", 1),
    ("4cd17410ab29495aa131cdf763fc3549", "cron", "任务管理", "/cron", "grp_ops", "ScheduleOutlined", 2),
    ("b2d585de4d824b96bfed2a7798ad6880", "workers", "主机监控", "/workers", "grp_ops", "IconCloudServerColor", 3),
    ("sys_logs", "sys_logs", "操作日志", "/system/logs", "grp_ops", "ProfileOutlined", 4),
    ("chunk_verify", "chunk_verify", "切片核对", "/chunk-verify", "grp_ops", "SafetyOutlined", 5),
    (
        "sys_metadata_collection",
        "sys_metadata_collection",
        "元数据采集",
        "/system/metadata-collection",
        "grp_ops",
        "DatabaseOutlined",
        6,
    ),
    (
        "feedback_stats",
        "feedback_stats",
        "问答反馈",
        "/feedback-stats",
        "grp_ops",
        "LikeOutlined",
        7,
    ),
    (
        "llm_usage",
        "llm_usage",
        "LLM 使用",
        "/usage",
        "grp_ops",
        "BarChartOutlined",
        8,
    ),
    # 2026-10-20 维护中心（Wiki 页面反馈汇总与处理；知识库卡片「反馈 N」也可进入）
    (
        "wiki_maintenance",
        "wiki_maintenance",
        "维护中心",
        "/wiki-maintenance",
        "grp_ops",
        "CommentOutlined",
        9,
    ),
    # ---- 配置管理（顶级目录：数据源管理/模型/MCP/技能/参数/联网搜索） ----
    ("grp_config", "grp_config", "配置管理", None, None, "ToolOutlined", 7),
    ("datasources", "datasources", "数据源管理", "/datasources", "grp_config", "IconDatabaseColor", 1),
    ("models", "models", "模型配置", "/models", "grp_config", "CloudServerOutlined", 2),
    ("8829900dd6be4c45bc584df804ba0d4a", "mcps", "MCP 管理", "/mcps", "grp_config", "ApiOutlined", 3),
    ("256b44596e6e43f5a847e0c3ae7b2ba0", "skills", "技能管理", "/skills", "grp_config", "ToolOutlined", 4),
    ("sys_dims", "sys_dims", "参数管理", "/system/dims", "grp_config", "DatabaseOutlined", 5),
    ("d83aee0d1d8a4fce8a6bd121b6efc5fa", "websearch", "联网搜索", "/websearch", "grp_config", "SearchOutlined", 6),
    # ---- 系统管理（分组兼页面，顶级：点击自身跳 /system → 重定向到 /system/users） ----
    ("system", "system", "系统管理", "/system", None, "IconSystemMgrColor", 8),
    ("sys_users", "sys_users", "用户管理", "/system/users", "system", "TeamOutlined", 1),
    ("sys_roles", "sys_roles", "角色管理", "/system/roles", "system", "SafetyOutlined", 2),
    ("sys_teams", "sys_teams", "团队管理", "/system/teams", "system", "PartitionOutlined", 3),
    ("sys_menus", "sys_menus", "菜单管理", "/system/menus", "system", "MenuOutlined", 4),
    # ---- 调试工具（顶级目录：本体Schema/编排管理/编排模板/编排组件/文件管理/数据查询） ----
    ("grp_debug", "grp_debug", "调试工具", None, None, "BugOutlined", 9),
    (
        "ont_schemas",
        "ont_schemas",
        "本体Schema",
        "/ontology-schemas",
        "grp_debug",
        "ApartmentOutlined",
        2,
    ),
    # 方案H：编排管理/编排模板 从顶级移入调试工具（与编排组件相邻）
    ("orch_tapes", "orch_tapes", "编排管理", "/orchestrations", "grp_debug", "IconResourceOrchestrationColor", 3),
    ("orch_templates", "orch_templates", "编排模板", "/tape-templates", "grp_debug", "AppstoreOutlined", 4),
    ("orch_defines", "orch_defines", "编排组件", "/orchestrations/steps", "grp_debug", "BlockOutlined", 5),
    ("ac9b271c90b6450c92bd14e9681da520", "files", "文件管理", "/files", "grp_debug", "IconCloudServerColor", 6),
    ("datagrid", "datagrid", "数据查询", "/datagrid", "grp_debug", "TableOutlined", 7),
    ("quality", "quality", "质量评估", "/quality", "grp_debug", "CheckCircleOutlined", 8),
]

# 除种子角色外，这些角色（若存在）同样授权全量 KB 菜单，
# 保证管理员/普通用户登录后侧栏都是完整菜单。
SEED_GRANT_ROLES = (SEED_ROLE, "plat-mgr", "normal_user")

# 系统参数种子（modo_dim, dim_group=SYSTEM_CONFIG）
# 2026-10-12: 参数表清理后仅保留代码确实读取的 9 个参数，此处固化进种子——
#   全新部署跑本脚本即建好这 9 行，已部署环境已有则跳过（不覆盖已有配置值）。
# 2026-10-21: 移除 4 个失效参数（代码读取端用大写 key 查询，而这几行是小写，
#   config_cache 大小写敏感匹配不上 → 页面可改但不生效）：
#   ai_chat_api_endpoint / ai_chat_api_key / ai_chat_model / dify_chat_api_endpoint。
#   问答模型/向量模型由「模型配置」页（MODEL_CONFIG_FIELDS 大写 key）管理；
#   REMOVED_SYSTEM_CONFIG_DIMS 让重跑本脚本时显式清理旧库残留行（对齐 REMOVED_MENUS 语义）。
# dim_value 默认留空 = 走容器 env / 代码默认（与线上实际生效路径一致）；
# 敏感字段（*_API_KEY）不写默认值。seq 与已部署库一致，保证「参数管理」列表顺序稳定。
# (dim_code, dim_value, dim_desc, seq)
# 已失效参数（2026-10-21 移除）：小写 key 与代码大写读取端不匹配，页面改了不生效
REMOVED_SYSTEM_CONFIG_DIMS: set[str] = {
    "ai_chat_api_endpoint",
    "ai_chat_api_key",
    "ai_chat_model",
    "dify_chat_api_endpoint",
}

SYSTEM_CONFIG_DIMS: list[tuple[str, str, str, int]] = [
    ("EMBEDDING_BASE_URL", "", "向量模型端点（OpenAI 兼容 /v1；留空=容器 env EMBEDDING_BASE_URL）", 0),
    ("EMBEDDING_API_KEY", "", "向量模型密钥（留空=容器 env EMBEDDING_API_KEY）", 1),
    ("EMBEDDING_MODEL", "", "向量模型，如 BAAI/bge-m3（留空=容器 env EMBEDDING_MODEL）", 2),
    ("EMBEDDING_DIM", "", "向量维度，如 1024（留空=容器 env EMBEDDING_DIM）", 3),
    ("metadata_full_collection_enabled", "true", "元数据全量采集开关（true/false）", 9),
    # 2026-10-13 上传链摘要：三个标准摘要编排的 tape_id（Stuff/Map-Reduce/Refine）。
    # 留空 = 上传不触发摘要（即使 KB summary_strategy=auto 也静默跳过）；
    # 写入后 KB 上传按策略自动路由。运维换编排（自建复制）改这里即可，无需改代码。
    ("SUMMARY_TAPE_STUFF", "", "上传摘要编排-Stuff全文直塞（kb_tape.id，留空=不触发）", 38),
    ("SUMMARY_TAPE_MAPREDUCE", "", "上传摘要编排-Map-Reduce分块合并（kb_tape.id，留空=不触发）", 39),
    ("SUMMARY_TAPE_REFINE", "", "上传摘要编排-Refine增量修订（kb_tape.id，留空=不触发）", 40),
    # 2026-10-16 题材分析：未选构建引擎时，摘要完成后由该编排自动判定
    # 文档属于哪个领域、选对应的领域 wiki 构建编排继续构建（留空=回退技能链）。
    ("DOMAIN_RESOLVE_TAPE", "", "题材分析编排-自动选择领域构建（kb_tape.id，留空=不启用自动领域分发）", 41),
]


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
    # ── 细粒度基础原子（2026-10-12）：编排自己实现 wiki 构建逻辑。
    #    进程内执行（引擎 BaseStep），结果经 bindings 流转；原子 = 纯基础能力。
    ("kb_read_doc", "读文档切片", "知识构建", "ReadOutlined",
     "从 doc_chunk 读指定文档的全部切片内容，写入运行变量（默认 chunks），供下游 LLM/脚本使用",
     [{"name": "document_id", "label": "文档 ID", "type": "input", "required": True,
       "placeholder": "{{document_id}}"},
      {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
       "placeholder": "chunks（默认）"},
      {"name": "max_chunks", "label": "最多读 N 块(0=全部)", "type": "input", "required": False,
       "placeholder": "0"}], 9),
    ("kb_llm_chat", "LLM对话", "知识构建", "RobotOutlined",
     "一次通用 LLM 调用（OpenAI 兼容，走平台 chat 配置），prompt 支持 {{ }} 模板引用前序变量，输出写入变量",
     [{"name": "prompt", "label": "Prompt(支持{{}}模板)", "type": "textarea", "required": True,
       "placeholder": "请总结：{{chunks}}", "props": {"rows": 4}},
      {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
       "placeholder": "llm_out（默认）"},
      {"name": "model", "label": "模型(留空=平台默认)", "type": "input", "required": False,
       "placeholder": "deepseek-v4-pro"},
      {"name": "temperature", "label": "温度", "type": "input", "required": False, "placeholder": "0.2"},
      {"name": "max_tokens", "label": "max_tokens", "type": "input", "required": False, "placeholder": "2048"},
      {"name": "json_mode", "label": "强制解析JSON输出", "type": "boolean", "required": False},
      {"name": "skip_empty", "label": "空Prompt时跳过(不失败)", "type": "boolean", "required": False}], 10),
    ("wiki_upsert_page", "写Wiki页(单页)", "知识构建", "UploadOutlined",
     "幂等写入一张 wiki 页（slug 冲突则更新并恢复 active），支持 {{ }} 模板字段",
     [{"name": "kb_id", "label": "知识库ID", "type": "input", "required": True, "placeholder": "{{kb_id}}"},
      {"name": "title", "label": "标题", "type": "input", "required": True, "placeholder": "{{title}}"},
      {"name": "content", "label": "内容(Markdown)", "type": "textarea", "required": True,
       "placeholder": "# {{title}}\\n\\n{{summary}}", "props": {"rows": 6}},
      {"name": "slug", "label": "Slug", "type": "input", "required": True, "placeholder": "doc-{{doc_hash}}"},
      {"name": "page_type", "label": "页面类型", "type": "input", "required": False, "placeholder": "summary"},
      {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
       "placeholder": "page_slug（默认）"}], 11),
    ("kb_write_doc_summary", "写文档摘要", "知识构建", "FileTextOutlined",
     "把编排产出的最终摘要写入 kb_document.summary / summary_status（直连 LLM 时代摘要的存储位置）",
     [{"name": "document_id", "label": "文档ID", "type": "input", "required": True, "placeholder": "{{document_id}}"},
      {"name": "summary_var", "label": "摘要来源变量", "type": "input", "required": False,
       "placeholder": "final_summary（默认）"},
      {"name": "status", "label": "写入状态", "type": "input", "required": False, "placeholder": "READY"},
      {"name": "skip_empty", "label": "摘要为空时跳过(不失败)", "type": "boolean", "required": False}], 11),
    ("wiki_rebuild_links", "重建Wiki互链", "知识构建", "LinkOutlined",
     "重建一个知识库全部 wiki 页之间的互链（无输入输出，纯动作）",
     [{"name": "kb_id", "label": "知识库ID", "type": "input", "required": True, "placeholder": "{{kb_id}}"}], 12),
    ("wiki_ensure_folder", "初始化Wiki目录", "知识构建", "FolderAddOutlined",
     "幂等确保目录存在（同名复用不重复建），支持多级路径（sep 分隔）与批量（paths 数组），目录 id 写入变量供下游建页用",
     [{"name": "kb_id", "label": "知识库ID", "type": "input", "required": True,
       "placeholder": "{{kb_id}}"},
      {"name": "path", "label": "目录路径(多级用/分隔)", "type": "input", "required": False,
       "placeholder": "长句原文/2024"},
      {"name": "paths", "label": "批量路径(JSON数组,可选)", "type": "textarea", "required": False,
       "placeholder": '["长句原文", "高频关键词", "业务实体"]', "props": {"rows": 3}},
      {"name": "sep", "label": "路径分隔符", "type": "input", "required": False, "placeholder": "/"},
      {"name": "parent_id", "label": "顶层父级ID(默认根)", "type": "input", "required": False,
       "placeholder": ""},
      {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
       "placeholder": "folder_id（单条输出 id，批量输出列表）"},
      {"name": "dry_run", "label": "仅统计不写库", "type": "boolean", "required": False}], 13),
    ("kb_read_table", "读表源数据", "知识构建", "DatabaseOutlined",
     "从数据源实时读一张绑定表（复用 datagrid 连接层）+ 元数据列注释/类型补全，输出 rows/columns/col_comments/col_types/table_comment 等到运行变量，供下游脚本构造 markdown 与 wiki 页",
     [{"name": "document_id", "label": "表源文档ID", "type": "input", "required": True,
       "placeholder": "{{document_id}}"},
      {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
       "placeholder": "table_data（默认）"},
      {"name": "limit", "label": "样例行数上限", "type": "input", "required": False,
       "placeholder": "500"}], 14),
    ("long_sentence_extract", "长句拆分", "知识构建", "ScissorOutlined",
     "长句原文拆分（包装 WeKnora extract_long_sentences_v2：切句→LLM整篇归组→拼接校验），输入 chunks JSON 输出候选长句 JSON",
     [{"name": "chunks_file", "label": "输入chunks JSON路径", "type": "input", "required": True,
       "placeholder": "/tmp/chunks_kid.json"},
      {"name": "output", "label": "输出候选长句JSON路径", "type": "input", "required": False,
       "placeholder": "/tmp/candidates_kid.json"},
      {"name": "summary_file", "label": "摘要文件路径(可选)", "type": "input", "required": False,
       "placeholder": ""},
      {"name": "summary_slug", "label": "摘要页slug(可选)", "type": "input", "required": False,
       "placeholder": ""},
      {"name": "max_tokens", "label": "max_tokens", "type": "input", "required": False, "placeholder": "8000"},
      {"name": "model", "label": "模型(默认wr默认)", "type": "input", "required": False, "placeholder": ""},
      {"name": "timeout_sec", "label": "超时(秒)", "type": "input", "required": False, "placeholder": "1800"}], 15),
    ("keyword_extract", "关键词抽取", "知识构建", "TagsOutlined",
     "高频关键词抽取（两步：程序统计 discover_terms → LLM 过滤 filter_terms_llm），输入长句 JSON 输出过滤后关键词 JSON",
     [{"name": "ls_file", "label": "输入长句JSON路径", "type": "input", "required": True,
       "placeholder": "/tmp/candidates_kid.json"},
      {"name": "output", "label": "输出关键词JSON路径", "type": "input", "required": False,
       "placeholder": "/tmp/terms_filtered_kid.json"},
      {"name": "min_freq", "label": "最低频次", "type": "input", "required": False, "placeholder": "3"},
      {"name": "top", "label": "候选Top N", "type": "input", "required": False, "placeholder": "50"},
      {"name": "max_len", "label": "最长短语字数", "type": "input", "required": False, "placeholder": "6"},
      {"name": "batch_size", "label": "LLM过滤每批条数", "type": "input", "required": False, "placeholder": "15"},
      {"name": "kb", "label": "知识库ID(LLM配置来源)", "type": "input", "required": False, "placeholder": "{{kb_id}}"},
      {"name": "model", "label": "模型(可选)", "type": "input", "required": False, "placeholder": ""},
      {"name": "dry_run", "label": "只统计不写过滤文件", "type": "boolean", "required": False},
      {"name": "timeout_sec", "label": "超时(秒)", "type": "input", "required": False, "placeholder": "1800"}], 16),
    # ── Agent 执行（2026-10-15）：投递 agent 队列由 kb-celery-agent-worker
    #    跑 openai-agents 循环（自主规划 + 技能工具 + MCP 工具）
    ("agent_run", "Agent执行", "Agent", "RobotOutlined",
     "把一次 agent 循环投递到 agent-worker 执行（支持配置提示词/系统指令/技能/MCP/模型）："
     "agent 自主规划并调用技能与 MCP 工具完成任务，输出写入运行变量供下游节点使用",
     [{"name": "prompt", "label": "任务提示词(支持{{}}模板)", "type": "textarea", "required": True,
       "placeholder": "请查阅知识库并总结以下内容：{{doc_summary}}", "props": {"rows": 4}},
      {"name": "instructions", "label": "系统指令(可选)", "type": "textarea", "required": False,
       "placeholder": "你是知识库问答助手，只输出结论，不要复述问题", "props": {"rows": 3}},
      {"name": "agent_name", "label": "Agent名称", "type": "input", "required": False,
       "placeholder": "kb-agent-worker（默认）"},
      {"name": "skill", "label": "技能名(可选)", "type": "input", "required": False,
       "placeholder": "留空=不挂技能；技能按知识库创建者权限解析"},
      {"name": "kb_id", "label": "知识库ID(技能/MCP权限基准)", "type": "input", "required": False,
       "placeholder": "{{kb_id}}"},
      {"name": "model", "label": "模型(留空=agent默认)", "type": "input", "required": False,
       "placeholder": "deepseek-v4-pro"},
      {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
       "placeholder": "agent_out（默认）"},
      {"name": "timeout_sec", "label": "等待超时(秒)", "type": "input", "required": False,
       "placeholder": "1800"},
      {"name": "skip_empty", "label": "空Prompt时跳过(不失败)", "type": "boolean", "required": False}], 17),
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


def _seed_system_config_dims(db) -> int:
    """幂等播种系统参数（modo_dim, dim_group=SYSTEM_CONFIG）。

    仅当 (dim_group, dim_code) 不存在时插入；已存在则完全不动
    （不覆盖已配置值，也不修正漂移）。返回新建行数。

    2026-10-21：播种前先按 REMOVED_SYSTEM_CONFIG_DIMS 显式清理失效参数行
    （种子是 upsert 只增不删，旧库残留的小写失效行必须显式删才能真正下架，
    对齐 REMOVED_MENUS 语义；新库幂等无操作）。
    """
    # 0) 失效参数清理（2026-10-21：ai_chat_api_endpoint 等 4 个小写 key 与代码
    #    大写读取端不匹配，页面改了不生效——从库里删除，模型配置交给「模型配置」页）
    if REMOVED_SYSTEM_CONFIG_DIMS:
        removed_dims = 0
        for code in REMOVED_SYSTEM_CONFIG_DIMS:
            rows = db.execute(
                select(Dim).where(
                    Dim.dim_group == "SYSTEM_CONFIG",
                    Dim.dim_code == code,
                )
            ).scalars().all()
            for row in rows:
                db.delete(row)
                removed_dims += 1
        if removed_dims:
            db.commit()
            print(
                f"  清理失效参数 {removed_dims} 行: "
                + ", ".join(sorted(REMOVED_SYSTEM_CONFIG_DIMS))
            )
    created = 0
    for code, value, desc, seq in SYSTEM_CONFIG_DIMS:
        existing = db.execute(
            select(Dim).where(
                Dim.dim_group == "SYSTEM_CONFIG",
                Dim.dim_code == code,
            )
        ).scalars().first()
        if existing:
            continue
        db.add(
            Dim(
                id=uuid.uuid4().hex[:32],
                dim_code=code,
                dim_group="SYSTEM_CONFIG",
                dim_value=value,
                dim_desc=desc,
                parent_dim_code=None,
                seq=seq,
                state="1",
            )
        )
        created += 1
    db.commit()
    return created


def _reset_menus(db) -> dict:
    """删除全部菜单与角色-菜单授权，恢复种子前状态（--reset-menus 用）。

    仅清理 modo_menu / modo_role_menu_rela 两表；菜单-接口绑定（api_perms）
    存于 Menu.menu_ext_conf JSON 列，随菜单行一并删除。其余框架数据
    （用户/角色/团队/参数等）不受影响。返回删除计数。
    """
    n_rela = getattr(db.execute(delete(RoleMenuRela)), "rowcount", 0)
    n_menu = getattr(db.execute(delete(Menu)), "rowcount", 0)
    db.commit()
    print(
        f"[reset] 菜单硬重置: 删除角色-菜单授权 {n_rela} 条、菜单 {n_menu} 项；"
        f"将按种子重建 {len(KB_MENUS)} 项并重新授权"
    )
    return {"role_menu_rela": n_rela, "menu": n_menu}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="KB 平台框架库种子初始化")
    parser.add_argument(
        "--reset-menus",
        action="store_true",
        help="重新初始化菜单：删除全部菜单与角色-菜单授权，按 KB_MENUS 种子重建（业务自定义菜单一并清除）",
    )
    args = parser.parse_args(argv)
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
        # 5a) 可选硬重置：--reset-menus 先清空菜单与角色-菜单授权，再按种子重建
        if args.reset_menus:
            _reset_menus(db)
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
                        state="0" if mid in HIDDEN_MENUS else "1",
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
                    ("state", "0" if mid in HIDDEN_MENUS else "1"),
                ):
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    updated += 1
        db.commit()

        # 授权：种子角色 + plat-mgr/normal_user（存在则一并授权）
        # ⚠️ 2026-10-16 修复：此处原按 role_name 精确匹配，但共享库角色表
        # role_id 为英文（plat-mgr/normal_user）而 role_name 为中文
        # （平台管理员/普通用户），导致两个角色从未获得任何菜单授权 ——
        # 新建用户绑定 plat-mgr/normal_user 后登录,受控接口(/api/v1/kbs、
        # /api/v1/models、/api/v1/skills 等)全部 403「无菜单权限」。
        # 改为 role_id 或 role_name 任一匹配。
        granted = 0
        for role_ref in dict.fromkeys(SEED_GRANT_ROLES):
            r = db.execute(
                select(UserRole).where(
                    or_(UserRole.role_id == role_ref, UserRole.role_name == role_ref)
                )
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

        # 废弃菜单清理（REMOVED_MENUS，2026-10-15 下架「新版问答」chat_next）：
        # 菜单行与角色-菜单授权一并删除；KB_MENUS 是 upsert 不删旧行，
        # 旧库残留的 chat_next 必须显式清理才真正下架（新库幂等无操作）。
        removed_menus = removed_menu_rels = 0
        for mid in REMOVED_MENUS:
            rows = db.execute(select(Menu).where(Menu.menu_id == mid)).scalars().all()
            rels = db.execute(
                select(RoleMenuRela).where(RoleMenuRela.menu_id == mid)
            ).scalars().all()
            for rel in rels:
                db.delete(rel)
            for mrow in rows:
                db.delete(mrow)
            removed_menus += len(rows)
            removed_menu_rels += len(rels)
        db.commit()
        if removed_menus or removed_menu_rels:
            print(
                f"[5/6] 废弃菜单清理 OK: 删除菜单 {removed_menus} 行 / 角色授权 {removed_menu_rels} 行"
                f"（{', '.join(sorted(REMOVED_MENUS))}）"
            )

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

        # 6a) 兼容修正（2026-10-15）：历史遗留的 LogCleanupTask（无 Kb 前缀）在
        #     TASK_CLASS_REGISTRY 未注册 handler，每天触发必然 FAILED
        #     （no handler registered for LogCleanupTask，实测 2026-10-14 cleanLog
        #     cron 00:00 稳定失败）。种子统一停用（state=0，保留配置痕迹），
        #     正式清理任务以 KbLogCleanupTask 为准。幂等：已停用/不存在则跳过。
        legacy_crons = db.execute(
            select(CronTask).where(
                CronTask.task_class == "LogCleanupTask", CronTask.state == "1"
            )
        ).scalars().all()
        if legacy_crons:
            for c in legacy_crons:
                c.state = "0"
            db.commit()
            print(
                f"[6/6] 日志清理 cron 兼容修正: 停用遗留 LogCleanupTask {len(legacy_crons)} 条"
                " (handler 未注册, 以 KbLogCleanupTask 为准)"
            )
        else:
            print("[6/6] 日志清理 cron 兼容检查: 无遗留 LogCleanupTask")

        # 6b) 系统参数种子（modo_dim SYSTEM_CONFIG，幂等：缺失才建，不覆盖已有值）
        dims_created = _seed_system_config_dims(db)
        print(
            f"[6b/7] 系统参数种子 OK: {len(SYSTEM_CONFIG_DIMS)} 个 "
            f"[新建 {dims_created} / 已有跳过 {len(SYSTEM_CONFIG_DIMS) - dims_created}]"
        )

        # 6c) 构建失败补位自愈 cron（2026-10-17）：批量 wiki 构建的 failed 文档
        #     滚动补位搬进平台内（宿主 retrigger_batched.py 无自愈，进程被杀即
        #     停滞）；默认每 6 小时一次，定时任务管理页可改频率/水位。幂等。
        existing_cron = db.execute(
            select(CronTask).where(CronTask.task_class == "KbBuildRetriggerTask")
        ).scalars().first()
        if existing_cron:
            print("[6c/7] 构建补位自愈 cron 已存在 (skip)")
        else:
            db.add(
                CronTask(
                    id=uuid.uuid4().hex,
                    name="构建失败补位自愈",
                    label="批量 wiki 构建 failed 文档滚动补位（默认每 6 小时，水位 40）",
                    cron_expression="0 */6 * * *",
                    state="1",
                    task_class="KbBuildRetriggerTask",
                    fire_params=json.dumps({"watermark": 40}, ensure_ascii=False),
                    queue_name="default",
                )
            )
            db.commit()
            print("[6c/7] 构建补位自愈 cron 种子 OK: 每 6 小时")

        # 7) 编排内置组件定义（幂等 upsert）
        d_created, d_updated = _seed_step_defines(db)
        print(f"[7/7] 编排组件定义种子 OK: 内置 {len(ORCH_STEP_DEFINES)} 个 [新建 {d_created} / 修正 {d_updated}]")

        # 8) 种子编排（2026-10-17 统一为 seed_tape_data/ 单源数据）
        #    编排数据唯一真源 = scripts/seed_tape_data/*.json（线上编排管理数据的
        #    导出固化：20 领域构建 + 表构建Wiki + 摘要三模式 + 题材分析 + 质量评估，
        #    2026-10-17 与线上一致共 26 条；由 export_orchestrations_to_seed.py 重新导出）。
        #    按 tape_name 幂等，已存在跳过；status/category/tape_type 从文件恢复。
        #    旧的逐编排播种函数（_seed_wiki_build_tape / _seed_table_wiki_tape /
        #    _seed_summary_tapes / _seed_domain_resolve_tape / _seed_quality_tape）
        #    已停用（保留仅供查阅）——以常量为准会与线上编排数据脱节。
        _seed_tapes_from_exports(db)

        # 15) 种子本体 Schema：25 套领域/业务本体（2026-10-21 固化，数据 seed_ontology_data/*.json）
        _seed_ontology_schemas(db)

        print("\nDONE — 登录: " + SEED_USER_ID + "/" + SEED_PASSWORD + "（编排内置组件 " + str(len(ORCH_STEP_DEFINES)) + " 个）")
    finally:
        db.close()


# 种子编排模板：wiki 构建（细粒度基础原子组合）
def _seed_wiki_build_tape(db) -> None:
    """幂等建「wiki 构建」编排模板（存在则 skip；内容变更用 seed 重跑覆盖字段不删步骤）。"""
    from api.models.orchestration import Tape

    TAPE_NAME = "wiki构建-基础原子版"
    existing = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
    if existing:
        print(f"[8/8] 种子编排「{TAPE_NAME}」已存在 (skip)")
        return

    tape = Tape(
        id=uuid.uuid4().hex[:32],
        tape_name=TAPE_NAME,
        tape_label="文档 wiki 构建（基础原子编排，替代 KbSkillWikiBuildTask 黑盒）",
        tape_descr=(
            "用细粒度基础原子组装 wiki 构建流程：kb_read_doc 读切片 → script 拼 prompt → "
            "kb_llm_chat 生成摘要/实体 → script 解析 → wiki_upsert_page 写摘要页 → "
            "loop 逐实体写页 → wiki_rebuild_links 重建互链。"
            "输入 bindings：kb_id / document_id。"
        ),
        tape_type="kb_build",  # 2026-10-15 功能标签：知识构建
        status="draft",
        nodes=list(WIKI_BUILD_TAPE["nodes"]),
        edges=list(WIKI_BUILD_TAPE["edges"]),
        exec_params=WIKI_BUILD_TAPE.get("exec_params") or {},
        create_user=None,
    )
    db.add(tape)
    db.commit()

    # 同步 kb_tape_step（复用服务层设计保存逻辑，保证 pre/next 映射一致）
    from api.services.orchestration import save_tape_design

    save_tape_design(db, tape.id, WIKI_BUILD_TAPE["nodes"], WIKI_BUILD_TAPE["edges"], WIKI_BUILD_TAPE.get("exec_params") or {})
    print(f"[8/8] 种子编排「{TAPE_NAME}」OK: id={tape.id[:12]}… 节点 {len(WIKI_BUILD_TAPE['nodes'])} 边 {len(WIKI_BUILD_TAPE['edges'])}（status=draft，页面点执行即可用）")


# ── wiki 构建编排（React Flow nodes/edges 结构，种子/测试共用）──────────
WIKI_BUILD_TAPE: dict = {
    "exec_params": {"kb_id": "", "document_id": ""},
    "nodes": [
        {
            "id": "n1", "type": "custom",
            "data": {"label": "读文档切片", "stepInst": "kb_read_doc",
                     "config": {"document_id": "{{document_id}}", "output_var": "chunks"}},
        },
        {
            "id": "n2", "type": "custom",
            "data": {"label": "拼摘要Prompt", "stepInst": "script",
                     "config": {"code": (
                         "import hashlib\n"
                         "chunks = bindings.get('chunks') or []\n"
                         "parts = []\n"
                         "for c in chunks:\n"
                         "    text = (c.get('content') or '').strip()\n"
                         "    if len(text) > 2000:\n"
                         "        text = text[:2000] + '…'\n"
                         "    if text:\n"
                         "        parts.append('[块%s]\\n%s' % (c.get('seq') or 0, text))\n"
                         "content = '\\n\\n'.join(parts)[:30000]\n"
                         "bindings['prompt'] = (\n"
                         "    '你是知识库 wiki 构建专家。根据下面的文档内容，生成结构化 wiki 页面数据。'\n"
                         "    '必须返回**纯 JSON**（不要 markdown 代码块），格式：\\n'\n"
                         "    '{\"title\": \"文档标题（中文，去除序号前缀）\", \"summary\": \"文档摘要（200-400 字）\", '\n"
                         "    '\"entities\": [{\"name\": \"实体/概念名\", \"definition\": \"一句话定义（30-80 字）\"}]}\\n'\n"
                         "    'entities 数量 3-8 个。只输出 JSON，不要任何额外文字。\\n\\n文档内容：\\n' + content\n"
                         ")"
                     )}},
        },
        {
            "id": "n3", "type": "custom",
            "data": {"label": "LLM生成JSON", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{prompt}}", "output_var": "llm_out",
                                "json_mode": True, "temperature": "0.2", "max_tokens": "4096"}},
        },
        {
            "id": "n4", "type": "custom",
            "data": {"label": "解析JSON", "stepInst": "script",
                     "config": {"code": (
                         "import hashlib\n"
                         "d = bindings.get('llm_out') or {}\n"
                         "if not isinstance(d, dict):\n"
                         "    raise ValueError('LLM 输出不是对象')\n"
                         "title = str(d.get('title') or '文档').strip()\n"
                         "summary = str(d.get('summary') or '').strip()\n"
                         "entities = [e for e in (d.get('entities') or []) if str(e.get('name') or '').strip()][:6]\n"
                         "kid = str(bindings.get('kb_id') or '')\n"
                         "doc_id = str(bindings.get('document_id') or '')\n"
                         "bindings['title'] = title\n"
                         "bindings['summary'] = summary\n"
                         "bindings['entities'] = entities\n"
                         "bindings['summary_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10]\n"
                         "bindings['entity_slugs'] = {\n"
                         "    str(e.get('name') or '').strip(): 'entity-' + hashlib.md5(str(e.get('name') or '').encode()).hexdigest()[:8]\n"
                         "    for e in entities\n"
                         "}"
                     )}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "写摘要页", "stepInst": "wiki_upsert_page",
                     "config": {"kb_id": "{{kb_id}}", "title": "{{title}}", "content": "{{summary}}",
                                "slug": "{{summary_slug}}", "page_type": "summary"}},
        },
        {
            "id": "n6", "type": "custom",
            "data": {"label": "逐实体循环", "stepInst": "loop",
                     "config": {"item_var": "entity", "collection": "bindings.get('entities') or []"}},
        },
        {
            "id": "n7", "type": "custom",
            "data": {"label": "准备实体页变量", "stepInst": "script",
                     "config": {"code": (
                         "ent = bindings.get('entity') or {}\n"
                         "name = str(ent.get('name') or '').strip()\n"
                         "definition = str(ent.get('definition') or '').strip()\n"
                         "slugs = bindings.get('entity_slugs') or {}\n"
                         "bindings['page_title'] = name\n"
                         "bindings['page_slug'] = str(slugs.get(name) or '')\n"
                         "bindings['page_content'] = ('# %s\\n\\n%s' % (name, definition)) if name else ''\n"
                     )}},
        },
        {
            "id": "n8", "type": "custom",
            "data": {"label": "写实体页", "stepInst": "wiki_upsert_page",
                     "config": {"kb_id": "{{kb_id}}", "title": "{{page_title}}",
                                "content": "{{page_content}}", "slug": "{{page_slug}}",
                                "page_type": "entity", "skip_empty": True}},
        },
        {
            "id": "n9", "type": "custom",
            "data": {"label": "重建互链", "stepInst": "wiki_rebuild_links",
                     "config": {"kb_id": "{{kb_id}}"}},
        },
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
        {"source": "n5", "target": "n6"},
        {"source": "n6", "target": "n7"},
        {"source": "n7", "target": "n8"},
        {"source": "n8", "target": "n9"},
    ],
}


# ── 表→wiki 构建编排（2026-10-12）──────────────────────────────
# 用细粒度基础原子 + script 组件定义逻辑，替代 table_ingest._build_table_wiki
# 黑盒：kb_read_table 取数 → script 构列定义/样例 markdown → kb_llm_chat 生成
# 表用途说明 → script 拼 wiki 正文 → wiki_upsert_page 写表主页(page_type=table)
# → wiki_rebuild_links 重建互链。输入 bindings：kb_id / document_id。
TABLE_WIKI_TAPE: dict = {
    "exec_params": {"kb_id": "", "document_id": ""},
    "nodes": [
        {
            "id": "n1", "type": "custom",
            "data": {"label": "读表源数据", "stepInst": "kb_read_table",
                     "config": {"document_id": "{{document_id}}", "output_var": "table_data"}},
        },
        {
            "id": "n2", "type": "custom",
            "data": {"label": "构建表元数据Markdown", "stepInst": "script",
                     "config": {"code": (
                         "import json\n"
                         "td = bindings.get('table_data') or {}\n"
                         "columns = td.get('columns') or []\n"
                         "col_comments = td.get('col_comments') or {}\n"
                         "col_types = td.get('col_types') or {}\n"
                         "table_name = td.get('table_name') or td.get('table_name') or ''\n"
                         "table_comment = td.get('table_comment') or ''\n"
                         "schema = td.get('schema') or ''\n"
                         "ds_name = td.get('ds_name') or ''\n"
                         "row_count = len(td.get('rows') or [])\n"
                         "lines = ['# %s' % (table_name or '表')]\n"
                         "if table_comment:\n"
                         "    lines.append('> 表注释：%s' % table_comment)\n"
                         "src = ds_name + ('.' + schema if schema else '')\n"
                         "lines.append('> 数据来源：数据源 %s · 表 %s' % (src or '-', table_name or '-'))\n"
                         "lines.append('> 行数：%s · 列数：%s' % (row_count, len(columns)))\n"
                         "if columns:\n"
                         "    lines.append('\\\\n## 列定义\\\\n')\n"
                         "    lines.append('| 列名 | 类型 | 注释 |')\n"
                         "    lines.append('|------|------|------|')\n"
                         "    for c in columns:\n"
                         "        cmt = (col_comments.get(c) or '').strip()\n"
                         "        lines.append('| %s | %s | %s |' % (c, col_types.get(c, '-'), cmt))\n"
                         "bindings['meta_md'] = '\\\\n'.join(lines)\n"
                         "bindings['row_count'] = row_count\n"
                     )}},
        },
        {
            "id": "n3", "type": "custom",
            "data": {"label": "构建样例数据Markdown", "stepInst": "script",
                     "config": {"code": (
                         "td = bindings.get('table_data') or {}\n"
                         "rows = td.get('rows') or []\n"
                         "if not rows:\n"
                         "    bindings['sample_md'] = ''\n"
                         "else:\n"
                         "    cols = list(rows[0].keys())\n"
                         "    lines = ['\\\\n## 样例数据（前 %d 行）\\\\n' % len(rows)]\n"
                         "    lines.append('| ' + ' | '.join(cols) + ' |')\n"
                         "    lines.append('|' + '|'.join('---' for _ in cols) + '|')\n"
                         "    for r in rows:\n"
                         "        cells = []\n"
                         "        for c in cols:\n"
                         "            v = r.get(c)\n"
                         "            cells.append('' if v is None else str(v).replace('|', '\\\\\\\\|')[:80])\n"
                         "        lines.append('| ' + ' | '.join(cells) + ' |')\n"
                         "    bindings['sample_md'] = '\\\\n'.join(lines)\n"
                     )}},
        },
        {
            "id": "n4", "type": "custom",
            "data": {"label": "LLM生成表用途说明", "stepInst": "kb_llm_chat",
                     "config": {"prompt": (
                         "你是数据分析师。请基于下面这张表的真实元数据，写一段中文「表用途说明」。\\n"
                         "内容要求：1) 这是一张什么维度的表；2) 这张表在什么场景下使用"
                         "（如作为报表分组维度、指标口径解释依据等，按列名和注释合理推断）；"
                         "3) 关键字段说明（3-5 个代表性列及其含义）。\\n"
                         "严格约束：只能依据给出的元数据客观描述，不要编造表中不存在的信息，"
                         "不要臆测具体业务场景细节。总字数 150~300 字，用 2-3 个自然段输出，"
                         "不要输出列表或标题。\\n\\n{{meta_md}}"),
                                "output_var": "table_usage", "temperature": "0.3",
                                "max_tokens": "800", "model": "{{table_data.summary_model_id}}"}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "拼Wiki正文", "stepInst": "script",
                     "config": {"code": (
                         "import hashlib\n"
                         "td = bindings.get('table_data') or {}\n"
                         "parts = [bindings.get('meta_md') or '']\n"
                         "sample = bindings.get('sample_md') or ''\n"
                         "if sample:\n"
                         "    parts.append(sample)\n"
                         "usage = bindings.get('table_usage') or ''\n"
                         "if usage:\n"
                         "    parts.append('\\\\n## 表用途说明\\\\n\\\\n' + usage)\n"
                         "summary = td.get('summary') or ''\n"
                         "if summary:\n"
                         "    parts.append('\\\\n## 摘要\\\\n\\\\n' + summary)\n"
                         "content = '\\\\n'.join([p for p in parts if p and p.strip()])\n"
                         "table_name = td.get('table_name') or '表'\n"
                         "if not content.strip():\n"
                         "    content = '# %s' % table_name\n"
                         "schema = td.get('schema') or ''\n"
                         "slug = ('table-%s-%s' % (schema, table_name)) if schema else ('table-%s' % table_name)\n"
                         "bindings['wiki_title'] = table_name\n"
                         "bindings['wiki_content'] = content\n"
                         "bindings['wiki_slug'] = slug\n"
                     )}},
        },
        {
            "id": "n6", "type": "custom",
            "data": {"label": "写表主页Wiki页", "stepInst": "wiki_upsert_page",
                     "config": {"kb_id": "{{kb_id}}", "title": "{{wiki_title}}",
                                "content": "{{wiki_content}}", "slug": "{{wiki_slug}}",
                                "page_type": "table"}},
        },
        {
            "id": "n7", "type": "custom",
            "data": {"label": "重建互链", "stepInst": "wiki_rebuild_links",
                     "config": {"kb_id": "{{kb_id}}"}},
        },
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
        {"source": "n5", "target": "n6"},
        {"source": "n6", "target": "n7"},
    ],
}


def _seed_table_wiki_tape(db) -> None:
    """幂等建「表构建Wiki-基础原子版」编排模板（存在则 skip）。"""
    from api.models.orchestration import Tape

    TAPE_NAME = "表构建Wiki-基础原子版"
    existing = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
    if existing:
        print(f"[9/9] 种子编排「{TAPE_NAME}」已存在 (skip)")
        return

    tape = Tape(
        id=uuid.uuid4().hex[:32],
        tape_name=TAPE_NAME,
        tape_label="表→wiki 构建（基础原子编排，替代 table_ingest 表主页黑盒）",
        tape_descr=(
            "用细粒度基础原子组装表 wiki 构建流程：kb_read_table 取数 → script 构列定义/样例 "
            "markdown → kb_llm_chat 生成表用途说明 → script 拼 wiki 正文 → "
            "wiki_upsert_page 写表主页(page_type=table) → wiki_rebuild_links 重建互链。"
            "输入 bindings：kb_id / document_id（表源文档）。"
        ),
        tape_type="kb_build",  # 2026-10-15 功能标签：知识构建
        status="draft",
        nodes=list(TABLE_WIKI_TAPE["nodes"]),
        edges=list(TABLE_WIKI_TAPE["edges"]),
        exec_params=TABLE_WIKI_TAPE.get("exec_params") or {},
        create_user=None,
    )
    db.add(tape)
    db.commit()

    from api.services.orchestration import save_tape_design

    save_tape_design(db, tape.id, TABLE_WIKI_TAPE["nodes"], TABLE_WIKI_TAPE["edges"], TABLE_WIKI_TAPE.get("exec_params") or {})
    print(f"[9/9] 种子编排「{TAPE_NAME}」OK: id={tape.id[:12]}… 节点 {len(TABLE_WIKI_TAPE['nodes'])} 边 {len(TABLE_WIKI_TAPE['edges'])}（status=draft，页面点执行即可用）")


# ── 文档摘要三模式编排（2026-10-12）────────────────────────────
#   A. Stuff（全文直塞）：kb_read_doc 读全部切片 → script 拼全文 prompt →
#      kb_llm_chat 一次生成 → script 解析 → wiki_upsert_page 写摘要页。
#      适合中小文档（直连 LLM 报表摘要 generate_document_summary 已移除，
#      2026-10-14 起文档摘要统一走这些编排）。
#   B. Map-Reduce（分块再合并）：kb_read_doc → script 分批 → loop：
#      每批 {script 拼子摘要 prompt → kb_llm_chat 子摘要 → script 累积}，
#      最后一批时 script 拼合并 prompt → kb_llm_chat 合并（skip_empty 兜底，
#      非最后一批空 prompt 跳过不调 LLM）→ script 解析 → wiki_upsert_page。
#      适合大文档（切片多，避免超上下文）。
#   C. Refine（增量修订）：kb_read_doc → script 分批 → loop：
#      每批 {script 拼「已有摘要 + 本批新内容」→ kb_llm_chat 更新摘要 →
#      script 写回 running_summary}，最后一批落页。摘要逐批滚动，只读每批一次。
# 三种模板输入 bindings 一致：kb_id / document_id；可选用 summary_* 覆盖默认。
# 产物一致：wiki 摘要页（page_type=summary，slug 带模式后缀）+ bindings.final_summary。
#
# ⚠️ 设计约束（引擎语义）：loop 的全部下游节点都是循环体、每轮都跑——所以
#    「合并」必须放循环体内、用 script 判断最后一批才拼合并 prompt +
#    kb_llm_chat(skip_empty) 空 prompt 跳过 + wiki_upsert_page(skip_empty)
#    空 title 跳过。script 改 bindings 通过 run_node 的 context.bindings 合并
#    持久化到 run（跨轮累积 running_summary / sub_summaries 由此实现）。

SUMMARY_PROMPT_TPL = (
    "你是专业的文档摘要助手。请阅读用户提供的文档内容，生成一段简洁、准确的中文摘要："
    "先一句话概括文档主题，再用分点列出核心要点与关键结论。"
    "控制在 150~250 字，只输出摘要正文，不要输出多余解释。"
    "不要输出任何标题（不要以『# 文档摘要』或『《文档名》文档摘要』等开头），直接输出摘要正文。"
)

SUMMARY_STUFF_TAPE: dict = {
    "exec_params": {"kb_id": "", "document_id": "", "max_chars": "60000"},
    "nodes": [
        {
            "id": "n1", "type": "custom",
            "data": {"label": "读文档切片", "stepInst": "kb_read_doc",
                     "config": {"document_id": "{{document_id}}", "output_var": "chunks"}},
        },
        {
            "id": "n2", "type": "custom",
            "data": {"label": "拼全文Prompt", "stepInst": "script",
                     "config": {"code": (
                         "chunks = bindings.get('chunks') or []\n"
                         "parts = []\n"
                         "for c in chunks:\n"
                         "    text = (c.get('content') or '').strip()\n"
                         "    if len(text) > 4000:\n"
                         "        text = text[:4000] + '…'\n"
                         "    if text:\n"
                         "        parts.append(text)\n"
                         "limit = int(bindings.get('max_chars') or 60000)\n"
                         "content = '\\n\\n'.join(parts)[:limit]\n"
                         "bindings['prompt'] = ('" + SUMMARY_PROMPT_TPL +
                         "\\n\\n文档内容：\\n' + content)\n"
                     )}},
        },
        {
            "id": "n3", "type": "custom",
            "data": {"label": "LLM生成摘要", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{prompt}}", "output_var": "summary_text",
                                "model": "{{summary_model_id}}",
                                "temperature": "0.3", "max_tokens": "1024"}},
        },
        {
            "id": "n4", "type": "custom",
            "data": {"label": "摘要整理", "stepInst": "script",
                     "config": {"code": (
                         "import re as _re\n"
                         "s = str(bindings.get('summary_text') or '').strip()\n"
                         "META = _re.compile(\n"
                         "    r'(需要|要求|我们|计数|草稿|数一下|大概|大约|内心|确保|复述|无?需|'\n"
                         "    r'先一[句.]|再分点|只输出|注意|检查|控制|符合|输出.{0,8}(摘要|正文)|'\n"
                         "    r'字数|字符|chars|chinese|we |need|bullets?|theme|'\n"
                         "    r'^#{1,6}\\s|《[^》]{1,80}》\\s*文档摘要)', _re.I)\n"
                         "lines = []\n"
                         "for _ln in s.split('\\\\n'):\n"
                         "    t = _ln.strip().lstrip('。').strip()\n"
                         "    if not t:\n"
                         "        continue\n"
                         "    if META.search(t):\n"
                         "        continue\n"
                         "    lines.append(t)\n"
                         "bindings['final_summary'] = '\\\\n'.join(lines).strip()\n"
                     )}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "写文档摘要", "stepInst": "kb_write_doc_summary",
                     "config": {"document_id": "{{document_id}}", "summary_var": "final_summary",
                                "skip_empty": True}},
        },
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
    ],
}

SUMMARY_MAPREDUCE_TAPE: dict = {
    "exec_params": {"kb_id": "", "document_id": "", "batch_size": "5", "chunk_cap": "2000"},
    "nodes": [
        {
            "id": "n1", "type": "custom",
            "data": {"label": "读文档切片", "stepInst": "kb_read_doc",
                     "config": {"document_id": "{{document_id}}", "output_var": "chunks"}},
        },
        {
            "id": "n2", "type": "custom",
            "data": {"label": "分批", "stepInst": "script",
                     "config": {"code": (
                         "chunks = bindings.get('chunks') or []\n"
                         "batch_size = int(bindings.get('batch_size') or 5)\n"
                         "cap = int(bindings.get('chunk_cap') or 2000)\n"
                         "batches = []\n"
                         "for i in range(0, len(chunks), batch_size):\n"
                         "    texts = []\n"
                         "    for c in chunks[i:i + batch_size]:\n"
                         "        t = (c.get('content') or '').strip()\n"
                         "        if len(t) > cap:\n"
                         "            t = t[:cap] + '…'\n"
                         "        if t:\n"
                         "            texts.append(t)\n"
                         "    if texts:\n"
                         "        batches.append({'idx': len(batches), 'text': '\\n\\n'.join(texts)})\n"
                         "bindings['batches'] = batches\n"
                         "bindings['_total'] = len(batches)\n"
                         "bindings['sub_summaries'] = []\n"
                     )}},
        },
        {
            "id": "n3", "type": "custom",
            "data": {"label": "逐批循环", "stepInst": "loop",
                     "config": {"item_var": "batch", "collection": "bindings.get('batches') or []"}},
        },
        {
            "id": "n4", "type": "custom",
            "data": {"label": "拼子摘要Prompt", "stepInst": "script",
                     "config": {"code": (
                         "batch = bindings.get('batch') or {}\n"
                         "idx = int(batch.get('idx') or 0)\n"
                         "total = int(bindings.get('_total') or 1)\n"
                         "bindings['is_last'] = (idx >= total - 1)\n"
                         "bindings['map_prompt'] = (\n"
                         "    '以下是文档的第 %d/%d 个片段，请生成 80~150 字中文子摘要，'\n"
                         "    '只输出摘要正文，不要多余解释：\\n\\n' % (idx + 1, total))\n"
                         "text = str(batch.get('text') or '')\n"
                         "bindings['map_prompt'] += text\n"
                     )}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "LLM子摘要", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{map_prompt}}", "output_var": "sub_summary",
                                "model": "{{summary_model_id}}",
                                "temperature": "0.3", "max_tokens": "512"}},
        },
        {
            "id": "n6", "type": "custom",
            "data": {"label": "累积+拼合并Prompt", "stepInst": "script",
                     "config": {"code": (
                         "subs = list(bindings.get('sub_summaries') or [])\n"
                         "s = str(bindings.get('sub_summary') or '').strip()\n"
                         "if s:\n"
                         "    subs.append(s)\n"
                         "bindings['sub_summaries'] = subs\n"
                         "if bindings.get('is_last') and subs:\n"
                         "    body = '\\n\\n'.join('[子摘要 %d] %s' % (i + 1, t) for i, t in enumerate(subs))\n"
                         "    bindings['reduce_prompt'] = (\n"
                         "        '以下是对同一文档各分段的子摘要，请合并为一份完整连贯的中文文档摘要'\n"
                         "        '（150~250 字，先一句话概括主题，再分点列出核心要点，'\n"
                         "        '不要输出任何标题（不要以# 文档摘要或《文档名》文档摘要开头），'\n"
                         "        '只输出摘要正文）：\\n\\n' + body)\n"
                         "    bindings['merge_ready'] = True\n"
                         "else:\n"
                         "    bindings['reduce_prompt'] = ''\n"
                         "    bindings['merge_ready'] = False\n"
                     )}},
        },
        {
            "id": "n7", "type": "custom",
            "data": {"label": "LLM合并", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{reduce_prompt}}", "output_var": "merged_summary",
                                "model": "{{summary_model_id}}",
                                "temperature": "0.3", "max_tokens": "1024", "skip_empty": True}},
        },
        {
            "id": "n8", "type": "custom",
            "data": {"label": "摘要整理", "stepInst": "script",
                     "config": {"code": (
                         "import re as _re\n"
                         "if bindings.get('merge_ready'):\n"
                         "    s = str(bindings.get('merged_summary') or '').strip()\n"
                         "    META = _re.compile(\n"
                         "        r'(需要|要求|我们|计数|草稿|数一下|大概|大约|内心|确保|复述|无?需|'\n"
                         "        r'先一[句.]|再分点|只输出|注意|检查|控制|符合|输出.{0,8}(摘要|正文)|'\n"
                         "        r'字数|字符|chars|chinese|we |need|bullets?|theme|'\n"
                         "        r'^#{1,6}\\s|《[^》]{1,80}》\\s*文档摘要)', _re.I)\n"
                         "    lines = []\n"
                         "    for _ln in s.split('\\\\n'):\n"
                         "        t = _ln.strip().lstrip('。').strip()\n"
                         "        if not t:\n"
                         "            continue\n"
                         "        if META.search(t):\n"
                         "            continue\n"
                         "        lines.append(t)\n"
                         "    bindings['final_summary'] = '\\\\n'.join(lines).strip()\n"
                         "else:\n"
                         "    bindings['final_summary'] = ''\n"
                     )}},
        },
        {
            "id": "n9", "type": "custom",
            "data": {"label": "写文档摘要", "stepInst": "kb_write_doc_summary",
                     "config": {"document_id": "{{document_id}}", "summary_var": "final_summary",
                                "skip_empty": True}},
        },
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
        {"source": "n5", "target": "n6"},
        {"source": "n6", "target": "n7"},
        {"source": "n7", "target": "n8"},
        {"source": "n8", "target": "n9"},
    ],
}

# ── 题材分析编排（2026-10-16）：自动选择领域构建编排 ──────────────
# 未选构建引擎的文档：摘要完成后跑本编排，LLM 基于摘要 + 平台已有领域
# wiki 构建编排的划分，判定文档属于哪个领域，输出 resolved_tape_id，
# 上层链路（doc_process._enqueue_domain_guided_build）据此启动对应编排。
# 产物 bindings：domain（领域名）/ resolved_tape_id（编排 id）/ resolved_tape_name。
DOMAIN_RESOLVE_TAPE: dict = {
    "exec_params": {"kb_id": "", "document_id": ""},
    "nodes": [
        {
            "id": "n1", "type": "custom",
            "data": {"label": "读取领域编排清单", "stepInst": "script",
                     "config": {"code": (
                         "# 题材分析-第一步：读平台已有的领域知识构建编排（kb_tape 按 category 去重）"
                         "与文档摘要，拼领域判定 prompt\n"
                         "from api.db import get_sessionmaker as _gsm\n"
                         "from api.models.orchestration import Tape as _Tape\n"
                         "from api.models.knowledge import KbDocument as _KbDoc\n"
                         "from sqlalchemy import or_ as _or, select as _select\n"
                         "doc_id = str(bindings.get('document_id') or bindings.get('doc_id') or '')\n"
                         "_db = _gsm()()\n"
                         "try:\n"
                         "    _rows = _db.execute(_select(_Tape.id, _Tape.tape_name, _Tape.category).where(\n"
                         "        _Tape.status != 'offline', _or(_Tape.tape_type == 'kb_build', _Tape.tape_name.like('wiki构建-%'))\n"
                         "    ).order_by(_Tape.tape_name)).all()\n"
                         "finally:\n"
                         "    _db.close()\n"
                         "_by_cat = {}\n"
                         "for _tid, _tname, _cat in _rows:\n"
                         "    _cat = (_cat or '通用').strip()\n"
                         "    if _cat and _cat not in _by_cat:\n"
                         "        _by_cat[_cat] = {'tape_id': _tid, 'tape_name': _tname}\n"
                         "bindings['domain_cats'] = sorted(_by_cat)\n"
                         "bindings['domain_map'] = _by_cat\n"
                         "bindings['doc_summary'] = ''\n"
                         "if doc_id:\n"
                         "    _db2 = _gsm()()\n"
                         "    try:\n"
                         "        _sum = _db2.execute(_select(_KbDoc.summary).where(_KbDoc.id == doc_id)).scalar()\n"
                         "        bindings['doc_summary'] = (_sum or '').strip()\n"
                         "    finally:\n"
                         "        _db2.close()\n"
                         "if not _by_cat:\n"
                         "    bindings['domain_ready'] = False\n"
                         "    bindings['domain_prompt'] = ''\n"
                         "else:\n"
                         "    _lines = ['%s（对应编排：%s）' % (c, _by_cat[c]['tape_name'])\n"
                         "              for c in sorted(_by_cat)]\n"
                         "    bindings['domain_prompt'] = (\n"
                         "        '以下是知识库平台已有的全部领域构建编排分类，请判断文档属于哪个领域：\\n'\n"
                         "        + '\\n'.join('- ' + l for l in _lines)\n"
                         "        + '\\n\\n只输出一个领域名称，必须原样取自上面列表，不要输出任何其它内容。\\n'\n"
                         "        + '文档摘要：\\n' + (bindings.get('doc_summary') or '（无摘要）')\n"
                         "    )\n"
                         "    bindings['domain_ready'] = True\n"
                     )}},
        },
        {
            "id": "n2", "type": "custom",
            "data": {"label": "LLM领域判定", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{domain_prompt}}", "output_var": "domain_raw",
                                "model": "{{summary_model_id}}", "temperature": "0.2",
                                "max_tokens": "128", "skip_empty": True,
                                "thinking": "disabled",
                                "system": (
                                    "你是文档题材分类专家。只输出领域名称本身，"
                                    "禁止输出解释、序号、引号、标点或任何多余文字。"
                                )}},
        },
        {
            "id": "n3", "type": "custom",
            "data": {"label": "映射为构建编排", "stepInst": "script",
                     "config": {"code": (
                         "import re as _re\n"
                         "raw = str(bindings.get('domain_raw') or '').strip()\n"
                         "raw = _re.sub(r'[\\\"\\\'。、，《》（）\\[\\]\\n\\s]', '', raw).strip()\n"
                         "cats = bindings.get('domain_cats') or []\n"
                         "dmap = bindings.get('domain_map') or {}\n"
                         "chosen = ''\n"
                         "for c in cats:\n"
                         "    if raw == c or (raw and (raw in c or c in raw)):\n"
                         "        chosen = c\n"
                         "        break\n"
                         "if not chosen:\n"
                         "    # 兜底：部分领域名含连接词（与/和），去掉后子串匹配\n"
                         "    for c in cats:\n"
                         "        _key = c.replace('与', '').replace('和', '')\n"
                         "        if _key and _key in raw:\n"
                         "            chosen = c\n"
                         "            break\n"
                         "if chosen and chosen in dmap:\n"
                         "    bindings['domain'] = chosen\n"
                         "    bindings['resolved_tape_id'] = dmap[chosen]['tape_id']\n"
                         "    bindings['resolved_tape_name'] = dmap[chosen]['tape_name']\n"
                         "else:\n"
                         "    bindings['domain'] = ''\n"
                         "    bindings['resolved_tape_id'] = ''\n"
                         "    bindings['resolved_tape_name'] = ''\n"
                     )}},
        },
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
    ],
}


SUMMARY_REFINE_TAPE: dict = {
    "exec_params": {"kb_id": "", "document_id": "", "batch_size": "3", "chunk_cap": "3000"},
    "nodes": [
        {
            "id": "n1", "type": "custom",
            "data": {"label": "读文档切片", "stepInst": "kb_read_doc",
                     "config": {"document_id": "{{document_id}}", "output_var": "chunks"}},
        },
        {
            "id": "n2", "type": "custom",
            "data": {"label": "分批+初始化", "stepInst": "script",
                     "config": {"code": (
                         "chunks = bindings.get('chunks') or []\n"
                         "batch_size = int(bindings.get('batch_size') or 3)\n"
                         "cap = int(bindings.get('chunk_cap') or 3000)\n"
                         "batches = []\n"
                         "for i in range(0, len(chunks), batch_size):\n"
                         "    texts = []\n"
                         "    for c in chunks[i:i + batch_size]:\n"
                         "        t = (c.get('content') or '').strip()\n"
                         "        if len(t) > cap:\n"
                         "            t = t[:cap] + '…'\n"
                         "        if t:\n"
                         "            texts.append(t)\n"
                         "    if texts:\n"
                         "        batches.append({'idx': len(batches), 'text': '\\n\\n'.join(texts)})\n"
                         "bindings['batches'] = batches\n"
                         "bindings['_total'] = len(batches)\n"
                         "bindings['running_summary'] = ''\n"
                     )}},
        },
        {
            "id": "n3", "type": "custom",
            "data": {"label": "逐批循环", "stepInst": "loop",
                     "config": {"item_var": "batch", "collection": "bindings.get('batches') or []"}},
        },
        {
            "id": "n4", "type": "custom",
            "data": {"label": "拼增量Prompt", "stepInst": "script",
                     "config": {"code": (
                         "batch = bindings.get('batch') or {}\n"
                         "idx = int(batch.get('idx') or 0)\n"
                         "total = int(bindings.get('_total') or 1)\n"
                         "bindings['is_last'] = (idx >= total - 1)\n"
                         "prev = str(bindings.get('running_summary') or '').strip()\n"
                         "cur = str(batch.get('text') or '')\n"
                         "if prev:\n"
                         "    bindings['refine_prompt'] = (\n"
                         "        '已有摘要：\\n' + prev +\n"
                         "        '\\n\\n新内容（第 %d/%d 片段）：\\n' % (idx + 1, total) + cur +\n"
                         "        '\\n\\n请基于「已有摘要 + 新内容」输出更新后的完整文档摘要'\n"
                         "        '（150~250 字，保留已有要点并融入新要点，'\n"
                         "        '不要输出任何标题（不要以# 文档摘要或《文档名》文档摘要开头），'\n"
                         "        '只输出摘要正文）：')\n"
                         "else:\n"
                         "    bindings['refine_prompt'] = (\n"
                         "        '根据以下文档内容生成中文摘要（150~250 字，先一句话概括主题，'\n"
                         "        '再分点列出核心要点，不要输出任何标题'\n"
                         "        '（不要以# 文档摘要或《文档名》文档摘要开头），只输出摘要正文）：\\n\\n' + cur)\n"
                     )}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "LLM更新摘要", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{refine_prompt}}", "output_var": "new_summary",
                                "model": "{{summary_model_id}}",
                                "temperature": "0.3", "max_tokens": "1024"}},
        },
        {
            "id": "n6", "type": "custom",
            "data": {"label": "回写+摘要整理", "stepInst": "script",
                     "config": {"code": (
                         "import re as _re\n"
                         "s = str(bindings.get('new_summary') or '').strip()\n"
                         "META = _re.compile(\n"
                         "    r'(需要|要求|我们|计数|草稿|数一下|大概|大约|内心|确保|复述|无?需|'\n"
                         "    r'先一[句.]|再分点|只输出|注意|检查|控制|符合|输出.{0,8}(摘要|正文)|'\n"
                         "    r'字数|字符|chars|chinese|we |need|bullets?|theme|'\n"
                         "    r'^#{1,6}\\s|《[^》]{1,80}》\\s*文档摘要)', _re.I)\n"
                         "lines = []\n"
                         "for _ln in s.split('\\\\n'):\n"
                         "    t = _ln.strip().lstrip('。').strip()\n"
                         "    if not t:\n"
                         "        continue\n"
                         "    if META.search(t):\n"
                         "        continue\n"
                         "    lines.append(t)\n"
                         "s = '\\\\n'.join(lines).strip()\n"
                         "bindings['running_summary'] = s\n"
                         "if bindings.get('is_last'):\n"
                         "    bindings['final_summary'] = s\n"
                         "else:\n"
                         "    bindings['final_summary'] = ''\n"
                     )}},
        },
        {
            "id": "n7", "type": "custom",
            "data": {"label": "写文档摘要", "stepInst": "kb_write_doc_summary",
                     "config": {"document_id": "{{document_id}}", "summary_var": "final_summary",
                                "skip_empty": True}},
        },
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
        {"source": "n5", "target": "n6"},
        {"source": "n6", "target": "n7"},
    ],
}


# ── 质量评估编排（2026-10-15 v2 语义评估版）────────────────────────
# 文档构建质量评估：切片/wiki/图数据三维度程序性检查（内联，不再依赖 quality_*.py）
# + 语义评估（wiki 单文档/wiki-原文对比/图数据三层抽样，LLM 驱动）。
# 健康分：每维度 程序性×0.6 + 语义×0.4，综合分=三维度平均。
# 语义节点 LLM 失败自动降级（不拖垮 run），quality_id 传空则汇总自建行。
QC_CODE = """

import json
from api.db import get_sessionmaker
from sqlalchemy import text

SHORT = 50
LONG = 2000
TRUNC_MARKS = ("…", "...", "（节选）", "[截断]")

_kb = bindings.get("kb_id") or ""
_docs = [d.strip() for d in str(bindings.get("doc_ids") or "").split(",") if d.strip()] or None
_db = get_sessionmaker()()
try:
    if _docs:
        _cond = "kb_id=:kb AND document_id IN :ids"
        _params = {"kb": _kb, "ids": tuple(_docs)}
    else:
        _cond = "kb_id=:kb"
        _params = {"kb": _kb}
    _rows = _db.execute(
        text(f"SELECT document_id, seq, content, meta FROM doc_chunk WHERE {_cond} AND enabled=1 ORDER BY document_id, seq"),
        _params,
    ).all()
finally:
    _db.close()

if not _rows:
    _r = {"metrics": {"docs": 0, "chunks": 0, "avg_len": 0},
          "issues": [{"dim": "chunk", "issue_type": "no_chunks", "detail": "无切片数据(文档未解析或无切片)"}],
          "attribution": [{"dim": "chunk", "cause": "文档未进入切片阶段或解析失败", "suggestion": "检查 parse_state / 重跑解析"}]}
else:
    _by_doc = {}
    for _rid, _seq, _content, _meta in _rows:
        _by_doc.setdefault(str(_rid), []).append((int(_seq or 0), str(_content or ""), _meta))
    _issues = []
    _totals = {"docs": len(_by_doc), "chunks": 0, "short": 0, "long": 0, "truncated": 0, "dup": 0}
    _per_doc = {}
    for _doc_id, _chunks in _by_doc.items():
        _lens = [len(c) for _, c, _ in _chunks]
        _n = len(_chunks)
        _totals["chunks"] += _n
        _short_n = sum(1 for L in _lens if L < SHORT)
        _long_n = sum(1 for L in _lens if L > LONG)
        _trunc_n = sum(1 for _, c, _ in _chunks if any(m in c for m in TRUNC_MARKS))
        _seen = set()
        _dup_n = 0
        for _, c, _ in _chunks:
            _h = __import__("hashlib").md5(c.strip()[:200].encode("utf-8")).hexdigest()
            if _h in _seen:
                _dup_n += 1
            _seen.add(_h)
        _totals["short"] += _short_n
        _totals["long"] += _long_n
        _totals["truncated"] += _trunc_n
        _totals["dup"] += _dup_n
        _per_doc[_doc_id] = {"doc_id": _doc_id, "chunks": _n, "avg_len": round(sum(_lens) / _n, 1) if _n else 0,
                             "short": _short_n, "long": _long_n, "truncated": _trunc_n, "dup": _dup_n}
        if _short_n / max(_n, 1) > 0.3:
            _issues.append({"dim": "chunk", "doc_id": _doc_id, "issue_type": "too_many_short_chunks", "detail": f"{_short_n}/{_n} 切片过短(<{SHORT}字),占比 {_short_n/_n:.0%}"})
        if _long_n / max(_n, 1) > 0.1:
            _issues.append({"dim": "chunk", "doc_id": _doc_id, "issue_type": "too_many_long_chunks", "detail": f"{_long_n}/{_n} 切片过长(>{LONG}字),占比 {_long_n/_n:.0%}"})
        if _trunc_n > 0:
            _issues.append({"dim": "chunk", "doc_id": _doc_id, "issue_type": "truncated_chunks", "detail": f"{_trunc_n} 个切片含截断标记"})
        if _dup_n / max(_n, 1) > 0.2:
            _issues.append({"dim": "chunk", "doc_id": _doc_id, "issue_type": "duplicate_chunks", "detail": f"{_dup_n}/{_n} 切片疑似近似重复"})
    _short_r = _totals["short"] / max(_totals["chunks"], 1)
    _long_r = _totals["long"] / max(_totals["chunks"], 1)
    _trunc_r = _totals["truncated"] / max(_totals["chunks"], 1)
    _score = max(0, int(100.0 - _short_r * 60 - _long_r * 60 - _trunc_r * 80))
    _metrics = {**_totals, "short_ratio": round(_short_r, 4), "long_ratio": round(_long_r, 4),
                "health_score": _score, "per_doc": _per_doc}
    _attr = []
    if _metrics.get("short_ratio", 0) > 0.3:
        _attr.append({"dim": "chunk", "cause": "过短切片占比高 → 切分粒度偏细/按词切分", "suggestion": "调大 chunk_size 或按段落合并相邻短块"})
    if _metrics.get("long_ratio", 0) > 0.1:
        _attr.append({"dim": "chunk", "cause": "过长切片占比高 → 切分边界未生效(表/长句整块保留)", "suggestion": "对长表/长句二次切分,或提高 max_chars 后按语义边界再分"})
    if _metrics.get("truncated", 0) > 0:
        _attr.append({"dim": "chunk", "cause": "切片带截断标记 → 切分在硬长度处截断而非语义边界", "suggestion": "开启 overlap/按句号段号切分,减少硬截断"})
    if _metrics.get("dup", 0) > 0:
        _attr.append({"dim": "chunk", "cause": "近似重复切片 → 重叠窗口/段落重复扫描", "suggestion": "关闭 overlap 或调小重叠比例,去重后再入库"})
    _r = {"metrics": _metrics, "issues": _issues, "attribution": _attr}

bindings["chunk_r"] = _r
print(f"[n02] 切片质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}")

"""

QW_CODE = """

import re
from api.db import get_sessionmaker
from sqlalchemy import text

PLACEHOLDER = "*由 LLM 生成*"
_SOURCE_MARKS = ("出处", "来源制度", "原文关联", "来源")

_kb = bindings.get("kb_id") or ""
_db = get_sessionmaker()()
try:
    _pages = _db.execute(
        text("SELECT id, slug, title, page_type, content, folder_id, folder_ids FROM wiki_page WHERE kb_id=:kb AND status='active'"),
        {"kb": _kb},
    ).all()
    _links = _db.execute(
        text("SELECT from_page_id, to_page_id FROM wiki_link WHERE kb_id=:kb"),
        {"kb": _kb},
    ).all()
finally:
    _db.close()

if not _pages:
    _r = {"metrics": {"pages": 0, "health_score": 0},
          "issues": [{"dim": "wiki", "issue_type": "no_pages", "detail": "无 wiki 页面(未构建或构建失败)"}],
          "attribution": [{"dim": "wiki", "cause": "wiki 构建未执行或全部失败", "suggestion": "检查构建编排 run 状态 / 重跑构建"}]}
else:
    _by_id = {str(p[0]): p for p in _pages}
    _slug_by_id = {str(p[0]): str(p[1]) for p in _pages}
    _linked_ids = {str(l[0]) for l in _links} | {str(l[1]) for l in _links}
    _issues = []
    _empty = [p for p in _pages if not (p[4] or "").strip()]
    for _p in _empty:
        _issues.append({"dim": "wiki", "slug": str(_p[1]), "page_type": str(_p[3]), "issue_type": "empty_content", "detail": "页面内容为空"})
    _orphans = [p for p in _pages if str(p[0]) not in _linked_ids]
    for _p in _orphans:
        _issues.append({"dim": "wiki", "slug": str(_p[1]), "page_type": str(_p[3]), "issue_type": "orphan", "detail": "无任何双向链接"})
    _broken = 0
    _broken_slugs = set()
    for _p in _pages:
        _targets = re.findall(r"\[\[([^\]|]+)", _p[4] or "")
        for _t in _targets:
            _t = _t.strip()
            if _t and _t not in _slug_by_id.values():
                _broken += 1
                _broken_slugs.add(_t)
    if _broken:
        _issues.append({"dim": "wiki", "issue_type": "broken_links", "detail": f"{_broken} 处断链,目标示例: {list(_broken_slugs)[:5]}"})
    _placeholder = [p for p in _pages if PLACEHOLDER in (p[4] or "")]
    for _p in _placeholder:
        _issues.append({"dim": "wiki", "slug": str(_p[1]), "page_type": str(_p[3]), "issue_type": "llm_placeholder", "detail": "LLM 解读占位未填充"})
    _no_source = [p for p in _pages if not any(m in (p[4] or "") for m in _SOURCE_MARKS)]
    for _p in _no_source:
        _issues.append({"dim": "wiki", "slug": str(_p[1]), "page_type": str(_p[3]), "issue_type": "missing_source", "detail": "页面无出处/原文引用标记"})
    _no_folder = [p for p in _pages if not (p[5] or p[6] or "")]
    for _p in _no_folder:
        _issues.append({"dim": "wiki", "slug": str(_p[1]), "page_type": str(_p[3]), "issue_type": "no_folder", "detail": "页面未挂载任何目录"})
    _n = len(_pages)
    _s = 100.0
    _s -= (len(_empty) / _n) * 100 + (len(_orphans) / _n) * 60
    _s -= min(_broken, _n) * 0.5
    _s -= (len(_placeholder) / _n) * 80 + (len(_no_source) / _n) * 40
    _metrics = {"pages": _n, "empty": len(_empty), "orphan": len(_orphans), "broken_links": _broken,
                "placeholder": len(_placeholder), "missing_source": len(_no_source), "no_folder": len(_no_folder),
                "empty_ratio": round(len(_empty) / _n, 4), "orphan_ratio": round(len(_orphans) / _n, 4),
                "health_score": max(0, int(_s))}
    _attr = []
    if _metrics.get("empty_ratio", 0) > 0.05:
        _attr.append({"dim": "wiki", "cause": "空内容页占比高 → 建页流程在生成内容前中断/LLM 输出为空", "suggestion": "检查构建日志中该页的生成步骤;对空页重跑补写"})
    if _metrics.get("orphan_ratio", 0) > 0.3:
        _attr.append({"dim": "wiki", "cause": "孤立页占比高 → 实体抽取过泛/页面间未建立互链", "suggestion": "收敛实体抽取范围,构建尾部执行 rebuild_links"})
    if _metrics.get("broken_links", 0) > 0:
        _attr.append({"dim": "wiki", "cause": "断链 = 引用了未创建的页面/实体(抽取了未建的实体名)", "suggestion": "构建前先建全部实体页再补引用,或对断链目标补建页面"})
    if _metrics.get("placeholder", 0) > 0:
        _attr.append({"dim": "wiki", "cause": "LLM 占位未填充 → 解读步骤未跑/供应商失败中断", "suggestion": "重跑 LLM 解读补位(断点续传),或修供应商可达性"})
    if _metrics.get("missing_source", 0) > 0:
        _attr.append({"dim": "wiki", "cause": "页面缺出处标记 → 渲染模板未带原文出处章节", "suggestion": "渲染器补「出处」行,保证证据链可回原文"})
    _r = {"metrics": _metrics, "issues": _issues, "attribution": _attr}

bindings["wiki_r"] = _r
print(f"[n03] Wiki质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}")

"""

QG_CODE = """

import os
from api.db import get_sessionmaker
from sqlalchemy import text
from neo4j import GraphDatabase

_STABLE_REL_TYPES = ("约束", "主体", "客体", "条件", "出自", "依据")

_kb = bindings.get("kb_id") or ""
_label = "ENTITY" + _kb.replace("-", "_")
_uri = os.getenv("NEO4J_URI", "bolt://kb-neo4j:7687")
_user = os.getenv("NEO4J_USERNAME", "neo4j")
_pwd = os.getenv("NEO4J_PASSWORD", "")
_driver = GraphDatabase.driver(_uri, auth=(_user, _pwd))
try:
    with _driver.session() as _sess:
        _nodes = _sess.run(f"MATCH (n:`{_label}`) RETURN count(n) AS c").single()["c"]
        _rels = _sess.run(f"MATCH (a:`{_label}`)-[r]-(b:`{_label}`) RETURN count(r) AS c").single()["c"]
        _dumb = _sess.run(f"MATCH (n:`{_label}`) WHERE n.attributes IS NULL OR size(n.attributes)=0 RETURN count(n) AS c").single()["c"]
        _rel_rows = _sess.run(f"MATCH (a:`{_label}`)-[r]-(b:`{_label}`) RETURN type(r) AS t, count(*) AS c ORDER BY c DESC").data()
        _rel_types = {_r["t"]: _r["c"] for _r in _rel_rows}
        _untraceable = _sess.run(f"MATCH (a:`{_label}`)-[r]-(b:`{_label}`) WHERE (r.rule_slug IS NULL OR r.rule_slug='') AND (r.note IS NULL OR r.note='') RETURN count(r) AS c").single()["c"]
        _node_slugs = _sess.run(f"MATCH (n:`{_label}`) WHERE n.page_slug IS NOT NULL AND n.page_slug<>'' RETURN collect(DISTINCT n.page_slug) AS slugs").single()["slugs"] or []
finally:
    _driver.close()

_issues = []
if _dumb > 0:
    _issues.append({"dim": "graph", "issue_type": "dumb_nodes", "detail": f"{_dumb} 个哑节点(无 attributes),占 {_dumb/max(_nodes,1):.0%}"})
_bad_types = {t: c for t, c in _rel_types.items() if t not in _STABLE_REL_TYPES}
if _bad_types:
    _issues.append({"dim": "graph", "issue_type": "illegal_rel_types", "detail": f"非稳定边类型: {_bad_types}"})
if _untraceable > 0:
    _issues.append({"dim": "graph", "issue_type": "untraceable_rels", "detail": f"{_untraceable} 条边无 rule_slug/note,无法回溯规则卡/原文"})

_db = get_sessionmaker()()
try:
    if _node_slugs:
        _existing = _db.execute(text("SELECT slug FROM wiki_page WHERE kb_id=:kb AND slug IN :slugs AND status='active'"), {"kb": _kb, "slugs": tuple(_node_slugs)}).scalars().all()
        _missing_slugs = [s for s in _node_slugs if s not in set(_existing)]
        if _missing_slugs:
            _issues.append({"dim": "graph", "issue_type": "wiki_missing_nodes", "detail": f"{len(_missing_slugs)} 个图节点对应 wiki 页不存在,示例: {_missing_slugs[:5]}"})
    _wiki_pages = _db.execute(text("SELECT count(*) FROM wiki_page WHERE kb_id=:kb AND status='active'"), {"kb": _kb}).scalar() or 0
finally:
    _db.close()

_health = 100.0
if _nodes:
    _health -= (_dumb / _nodes) * 80
    _health -= (_untraceable / max(_rels, 1)) * 60
if _bad_types:
    _health -= 40
if _issues:
    _health -= 10 * len(_issues)
_metrics = {"nodes": _nodes, "rels": _rels, "dumb_nodes": _dumb, "rel_types": _rel_types,
            "untraceable_rels": _untraceable, "wiki_pages": _wiki_pages,
            "dumb_ratio": round(_dumb / max(_nodes, 1), 4) if _nodes else 0,
            "health_score": max(0, int(_health))}
_attr = []
if _metrics.get("dumb_nodes", 0) > 0:
    _attr.append({"dim": "graph", "cause": "哑节点 = 写入方未补 attributes(仅 name+kg)", "suggestion": "图谱写入脚本收尾兜底补 attributes(定义/来源)"})
if _bad_types:
    _attr.append({"dim": "graph", "cause": "存在非稳定边类型 → 写入方未收敛到 6 种稳定边", "suggestion": "关系类型映射归一(约束/主体/客体/条件/出自/依据)"})
if _metrics.get("untraceable_rels", 0) > 0:
    _attr.append({"dim": "graph", "cause": "边缺 rule_slug/note → 无法回溯规则卡/原文证据", "suggestion": "写入时给每条边带 rule_slug+note(原文句子)"})
_r = {"metrics": _metrics, "issues": _issues, "attribution": _attr}

bindings["graph_r"] = _r
print(f"[n04] 图数据质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}")

"""

WIKI_SEM_CODE = """

import json
from api.db import get_sessionmaker
from sqlalchemy import text


def _llm(prompt, system="", temperature=0.2, max_tokens=2048, json_mode=True):
    from api.services.chat import ChatClient, load_chat_config, ChatMessage
    from api.db import get_sessionmaker
    import time as _t
    _db = get_sessionmaker()()
    try:
        cfg = load_chat_config(_db, model_id=None)
        client = ChatClient(cfg)
        msgs = []
        if system:
            msgs.append(ChatMessage(role="system", content=system))
        msgs.append(ChatMessage(role="user", content=prompt))
        last = None
        for attempt in range(4):
            try:
                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)
                if json_mode:
                    text_ = (raw or "").strip()
                    if text_.startswith("```"):
                        text_ = text_.strip("`")
                        if text_.startswith("json"):
                            text_ = text_[4:].strip()
                    return __import__("json").loads(text_)
                return raw or ""
            except Exception as e:
                last = e
                resp = getattr(e, "response", None)
                code = getattr(resp, "status_code", None) if resp is not None else None
                if code == 429:
                    _t.sleep(min(15 * (attempt + 1), 45))
                else:
                    _t.sleep(3)
        raise last
    finally:
        _db.close()


_kb = bindings.get("kb_id") or ""
_db = get_sessionmaker()()
try:
    _pages = _db.execute(
        text("SELECT slug, title, content FROM wiki_page WHERE kb_id=:kb AND status='active' AND content IS NOT NULL ORDER BY updated_at DESC LIMIT 15"),
        {"kb": _kb},
    ).all()
finally:
    _db.close()

if not _pages:
    bindings["wiki_sem_r"] = {"metrics": {"health_score": 0, "pages_eval": 0}, "issues": [], "attribution": []}
    print("[n05] wiki 单文档语义: 无页面, 跳过")
else:
    _items = []
    for _p in _pages:
        _items.append({"slug": str(_p[0]), "title": str(_p[1] or ""), "content": str(_p[2] or "")[:1500]})
    _prompt = (
        "你是知识库质量评估专家。以下是从 wiki 中抽出的若干页面（含 slug/title/content）。\n"
        "对每个页面从**单文档视角**评估:\n"
        "1. 是否能让读者独立看出这页在讲什么（主题明确、开场即点题）\n"
        "2. 内容是否完整可理解（有上下文、有结论/定义，不是碎片或只有标题）\n"
        "3. 是否有明显占位/未完成/LLM 残留\n"
        "输出 JSON 数组，每个元素: {slug, score(0-100, 主题清晰度与完整度的综合分), issue(null 或简短问题描述)}\n"
        "只输出 JSON。页面列表:\n" + json.dumps(_items, ensure_ascii=False)
    )
    try:
        _out = _llm(_prompt, system="你是严谨的知识库质量评估器。只输出 JSON，不输出其他内容。")
    except Exception as _e:
        _r = {"metrics": {"health_score": None, "pages_eval": 0, "error": str(_e)[:200]},
              "issues": [{"issue_type": "sem_eval_error", "detail": "LLM 语义评估失败: " + str(_e)}], "attribution": []}
        bindings["wiki_sem_r"] = _r
        print("[n05] wiki 单文档语义: LLM 失败, 降级 -> " + str(_e))
    else:
        _rows = _out if isinstance(_out, list) else (_out.get("items") or _out.get("results") or [])
        _issues = []
        _scores = []
        for _row in _rows if isinstance(_rows, list) else []:
            _slug = str(_row.get("slug") or "")
            _sc = _row.get("score")
            if isinstance(_sc, (int, float)):
                _scores.append(float(_sc))
            _iss = str(_row.get("issue") or "").strip()
            if _iss and _iss.lower() not in ("null", "none", "无", "no"):
                _issues.append({"dim": "wiki", "slug": _slug, "issue_type": "wiki_sem_unclear",
                                "detail": _iss[:200]})
        _avg = round(sum(_scores) / len(_scores), 1) if _scores else None
        _r = {"metrics": {"health_score": _avg, "pages_eval": len(_rows), "semantic": "single_doc"},
              "issues": _issues,
              "attribution": ([{"dim": "wiki", "cause": "存在单文档视角看不明白的页面", "suggestion": "重写页面开场点题,补充背景与结论"}] if _issues else [])}
        bindings["wiki_sem_r"] = _r
        print("[n05] wiki 单文档语义: 均分 " + str(_avg) + " 问题 " + str(len(_issues)))

"""

WIKI_CMP_CODE = """

import json
from api.db import get_sessionmaker
from sqlalchemy import text


def _llm(prompt, system="", temperature=0.2, max_tokens=2048, json_mode=True):
    from api.services.chat import ChatClient, load_chat_config, ChatMessage
    from api.db import get_sessionmaker
    import time as _t
    _db = get_sessionmaker()()
    try:
        cfg = load_chat_config(_db, model_id=None)
        client = ChatClient(cfg)
        msgs = []
        if system:
            msgs.append(ChatMessage(role="system", content=system))
        msgs.append(ChatMessage(role="user", content=prompt))
        last = None
        for attempt in range(4):
            try:
                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)
                if json_mode:
                    text_ = (raw or "").strip()
                    if text_.startswith("```"):
                        text_ = text_.strip("`")
                        if text_.startswith("json"):
                            text_ = text_[4:].strip()
                    return __import__("json").loads(text_)
                return raw or ""
            except Exception as e:
                last = e
                resp = getattr(e, "response", None)
                code = getattr(resp, "status_code", None) if resp is not None else None
                if code == 429:
                    _t.sleep(min(15 * (attempt + 1), 45))
                else:
                    _t.sleep(3)
        raise last
    finally:
        _db.close()


_kb = bindings.get("kb_id") or ""
_db = get_sessionmaker()()
try:
    _pages = _db.execute(
        text("SELECT slug, title, content, source_refs FROM wiki_page WHERE kb_id=:kb AND status='active' AND content IS NOT NULL ORDER BY updated_at DESC LIMIT 10"),
        {"kb": _kb},
    ).all()
finally:
    _db.close()

if not _pages:
    bindings["wiki_cmp_r"] = {"metrics": {"health_score": 0, "pages_cmp": 0}, "issues": [], "attribution": []}
    print("[n06] wiki-原文对比: 无页面, 跳过")
else:
    _items = []
    for _p in _pages:
        _refs = _p[3] or []
        _doc_ids = []
        for _r in _refs if isinstance(_refs, list) else []:
            if isinstance(_r, dict) and _r.get("doc"):
                _doc_ids.append(str(_r["doc"]))
        _src = ""
        if _doc_ids:
            _db2 = get_sessionmaker()()
            try:
                _chunks = _db2.execute(
                    text("SELECT document_id, seq, content FROM doc_chunk WHERE kb_id=:kb AND document_id IN :ids ORDER BY document_id, seq LIMIT 25"),
                    {"kb": _kb, "ids": tuple(_doc_ids)},
                ).all()
                _src = "\n".join("[" + str(c[0])[:8] + "#" + str(c[1]) + "] " + str(c[2] or "")[:400] for c in _chunks)
            finally:
                _db2.close()
        _items.append({"slug": str(_p[0]), "title": str(_p[1] or ""),
                       "wiki_content": str(_p[2] or "")[:1800],
                       "source_excerpt": _src[:2500]})
    _prompt = (
        "你是知识库质量评估专家，负责**核对 wiki 页面与原始文档是否一致**。\n"
        "对每个页面，把 wiki 内容与提供的原文摘录（source_excerpt，可能不完整）对比，找出**明显错误**：\n"
        "1. 事实性错误（数字/日期/主体/结论与原文明说矛盾）\n"
        "2. 编造/无中生有（原文没有的内容被 wiki 写成事实）\n"
        "3. 张冠李戴/移花接木（把别处的定义安到这页）\n"
        "注意：source_excerpt 若为空或明显不完整，标注 insufficient 而不是编造错误。\n"
        "输出 JSON 数组，每个元素: {slug, has_error(true/false), errors: [简短错误描述], confidence(low/medium/high)}\n"
        "只输出 JSON。\n页面列表: " + json.dumps(_items, ensure_ascii=False)
    )
    try:
        _out = _llm(_prompt, system="你是严谨的知识库质量评估器，只依据给定原文判断，不确定就标注 insufficient。只输出 JSON。")
    except Exception as _e:
        _r = {"metrics": {"health_score": None, "pages_cmp": 0, "error": str(_e)[:200]},
              "issues": [{"issue_type": "sem_eval_error", "detail": "LLM 语义评估失败: " + str(_e)}], "attribution": []}
        bindings["wiki_cmp_r"] = _r
        print("[n06] wiki-原文对比: LLM 失败, 降级 -> " + str(_e))
    else:
        _rows = _out if isinstance(_out, list) else (_out.get("items") or _out.get("results") or [])
        _issues = []
        _error_pages = 0
        for _row in _rows if isinstance(_rows, list) else []:
            _slug = str(_row.get("slug") or "")
            if _row.get("has_error") is True or _row.get("has_error") in (1, "true", "True", "yes"):
                _errs = _row.get("errors") or []
                if isinstance(_errs, str):
                    _errs = [_errs]
                for _e2 in _errs[:3]:
                    _issues.append({"dim": "wiki", "slug": _slug, "issue_type": "wiki_sem_error",
                                    "detail": str(_e2)[:200]})
                _error_pages += 1
        _n = len(_rows) or 1
        _score = max(0, int(100 - (_error_pages / _n) * 100))
        _r = {"metrics": {"health_score": _score, "pages_cmp": len(_rows), "error_pages": _error_pages,
                          "semantic": "doc_compare"},
              "issues": _issues,
              "attribution": ([{"dim": "wiki", "cause": "wiki 与原文存在不一致/编造内容", "suggestion": "以原文为准修正 wiki 页面,构建时加强 LLM 事实约束"}] if _issues else [])}
        bindings["wiki_cmp_r"] = _r
        print("[n06] wiki-原文对比: 得分 " + str(_score) + " 错误页 " + str(_error_pages) + "/" + str(len(_rows)))

"""

GRAPH_SEM_CODE = """

import json
import os
from neo4j import GraphDatabase
from api.db import get_sessionmaker
from sqlalchemy import text


def _llm(prompt, system="", temperature=0.2, max_tokens=2048, json_mode=True):
    from api.services.chat import ChatClient, load_chat_config, ChatMessage
    from api.db import get_sessionmaker
    import time as _t
    _db = get_sessionmaker()()
    try:
        cfg = load_chat_config(_db, model_id=None)
        client = ChatClient(cfg)
        msgs = []
        if system:
            msgs.append(ChatMessage(role="system", content=system))
        msgs.append(ChatMessage(role="user", content=prompt))
        last = None
        for attempt in range(4):
            try:
                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)
                if json_mode:
                    text_ = (raw or "").strip()
                    if text_.startswith("```"):
                        text_ = text_.strip("`")
                        if text_.startswith("json"):
                            text_ = text_[4:].strip()
                    return __import__("json").loads(text_)
                return raw or ""
            except Exception as e:
                last = e
                resp = getattr(e, "response", None)
                code = getattr(resp, "status_code", None) if resp is not None else None
                if code == 429:
                    _t.sleep(min(15 * (attempt + 1), 45))
                else:
                    _t.sleep(3)
        raise last
    finally:
        _db.close()


_kb = bindings.get("kb_id") or ""
_label = "ENTITY" + _kb.replace("-", "_")
_uri = os.getenv("NEO4J_URI", "bolt://kb-neo4j:7687")
_user = os.getenv("NEO4J_USERNAME", "neo4j")
_pwd = os.getenv("NEO4J_PASSWORD", "")
_driver = GraphDatabase.driver(_uri, auth=(_user, _pwd))
try:
    with _driver.session() as _sess:
        _sample_nodes = _sess.run(
            "MATCH (n:`" + _label + "`) WHERE n.name IS NOT NULL RETURN n.name AS name, n.entity_type AS et LIMIT 12"
        ).data()
        _sample_rels = _sess.run(
            "MATCH (a:`" + _label + "`)-[r]->(b:`" + _label + "`) RETURN a.name AS src, type(r) AS rel, b.name AS dst, r.note AS note LIMIT 12"
        ).data()
        _total_nodes = _sess.run("MATCH (n:`" + _label + "`) RETURN count(n) AS c").single()["c"] or 0
finally:
    _driver.close()

if not _sample_nodes and not _sample_rels:
    bindings["graph_sem_r"] = {"metrics": {"health_score": 0, "nodes_sampled": 0, "rels_sampled": 0}, "issues": [], "attribution": []}
    print("[n07] 图数据语义: 图无数据, 跳过")
else:
    _prompt = (
        "你是知识库图谱质量评估专家。以下是 Neo4j 语义图中抽样的**节点**与**关系**。\n"
        "**节点层**（评估实体名是否有意义）：" + json.dumps(_sample_nodes, ensure_ascii=False) +
        "\n**边层**（评估三元组 A-关系-B 语义是否正确：关系类型是否符合两端实体、note 是否支撑该关系）：" +
        json.dumps(_sample_rels, ensure_ascii=False) +
        "\n请输出 JSON 对象: {node_issues: [{name, problem}], rel_issues: [{src, rel, dst, problem}], "
        "overall_score: 0-100(图整体可理解性与语义正确性综合分), summary: 一句话总评}。\n"
        "问题包括: 实体名无意义/乱码/太泛、关系类型与实体不匹配、note 与关系矛盾或缺失、三元组明显错误。\n只输出 JSON。"
    )
    try:
        _out = _llm(_prompt, system="你是严谨的知识库图谱质量评估器。只输出 JSON。")
    except Exception as _e:
        _r = {"metrics": {"health_score": None, "nodes_sampled": 0, "rels_sampled": 0, "error": str(_e)[:200]},
              "issues": [{"issue_type": "sem_eval_error", "detail": "LLM 语义评估失败: " + str(_e)}], "attribution": []}
        bindings["graph_sem_r"] = _r
        print("[n07] 图数据语义: LLM 失败, 降级 -> " + str(_e))
    else:
        _n_issues = (_out.get("node_issues") or []) if isinstance(_out, dict) else []
        _r_issues = (_out.get("rel_issues") or []) if isinstance(_out, dict) else []
        _issues = []
        for _it in _n_issues[:8] if isinstance(_n_issues, list) else []:
            _issues.append({"dim": "graph", "issue_type": "graph_sem_node",
                            "detail": ("节点 [" + str(_it.get("name", "")) + "] " + str(_it.get("problem", ""))).strip()[:200]})
        for _it in _r_issues[:8] if isinstance(_r_issues, list) else []:
            _issues.append({"dim": "graph", "issue_type": "graph_sem_rel",
                            "detail": ("边 [" + str(_it.get("src", "")) + "-" + str(_it.get("rel", "")) + "->" + str(_it.get("dst", "")) + "] " + str(_it.get("problem", ""))).strip()[:200]})
        _sc = _out.get("overall_score") if isinstance(_out, dict) else None
        _score = int(_sc) if isinstance(_sc, (int, float)) else None
        _r = {"metrics": {"health_score": _score, "nodes_sampled": len(_sample_nodes), "rels_sampled": len(_sample_rels),
                          "total_nodes": _total_nodes, "semantic": "graph_3layer"},
              "issues": _issues,
              "attribution": ([{"dim": "graph", "cause": "图谱存在语义问题(实体名/关系/note)", "suggestion": "检查实体抽取与关系写入口径"}] if _issues else [])}
        bindings["graph_sem_r"] = _r
        print("[n07] 图数据语义: 得分 " + str(_score) + " 问题 " + str(len(_issues)))

"""

SUMMARY_CODE = """

import json
import uuid
from api.db import get_sessionmaker
from sqlalchemy import text

_kb = bindings.get("kb_id") or ""
_docs = [d.strip() for d in str(bindings.get("doc_ids") or "").split(",") if d.strip()] or None
_scope = str(bindings.get("scope") or "all")
_qid = str(bindings.get("quality_id") or "").strip()  # quality.py 传入的占位行 id

_chunk_r = bindings.get("chunk_r") or {}
_wiki_r = bindings.get("wiki_r") or {}
_graph_r = bindings.get("graph_r") or {}
_wiki_sem_r = bindings.get("wiki_sem_r") or {}
_wiki_cmp_r = bindings.get("wiki_cmp_r") or {}
_graph_sem_r = bindings.get("graph_sem_r") or {}

# 每维度指标：程序性 health_score + 语义 semantic_score
def _m(r):
    return r.get("metrics") or {}

_chunk_m = _m(_chunk_r)
_wiki_m = _m(_wiki_r)
_graph_m = _m(_graph_r)
# wiki 语义分 = 单文档 + 原文对比 两路平均（None 则用另一路）
_wiki_sem_scores = [s for s in (_m(_wiki_sem_r).get("health_score"), _m(_wiki_cmp_r).get("health_score")) if isinstance(s, (int, float))]
_wiki_semantic = round(sum(_wiki_sem_scores) / len(_wiki_sem_scores), 1) if _wiki_sem_scores else None
# 图语义分
_graph_semantic = _m(_graph_sem_r).get("health_score")
_graph_semantic = int(_graph_semantic) if isinstance(_graph_semantic, (int, float)) else None

# 综合分：每维度 程序×0.6 + 语义×0.4（无语义分则该维直接用程序分）
def _dim_score(prog, sem_score):
    if not isinstance(prog, (int, float)):
        return None
    if isinstance(sem_score, (int, float)):
        return round(prog * 0.6 + sem_score * 0.4, 1)
    return round(float(prog), 1)

_dims = {
    "chunk": {"prog": _chunk_m.get("health_score"), "semantic": None},
    "wiki": {"prog": _wiki_m.get("health_score"), "semantic": _wiki_semantic},
    "graph": {"prog": _graph_m.get("health_score"), "semantic": _graph_semantic},
}
_scores = {d: _dim_score(v["prog"], v["semantic"]) for d, v in _dims.items()}
_valid = [s for s in _scores.values() if isinstance(s, (int, float))]
_overall = round(sum(_valid) / len(_valid), 1) if _valid else 0

# issues / attribution 合并（程序性 + 语义）
_issues = []
_attribution = []
for r in (_chunk_r, _wiki_r, _graph_r, _wiki_sem_r, _wiki_cmp_r, _graph_sem_r):
    for it in (r.get("issues") or []):
        _issues.append(it)
    for at in (r.get("attribution") or []):
        _attribution.append(at)

_metrics_map = {
    "chunk": {**{k: v for k, v in _chunk_m.items() if k != "per_doc"},
              "semantic_score": None, "final_score": _scores.get("chunk")},
    "wiki": {**_wiki_m, "semantic_score": _wiki_semantic, "final_score": _scores.get("wiki")},
    "graph": {**_graph_m, "semantic_score": _graph_semantic, "final_score": _scores.get("graph")},
}
_summary = (
    f"评估完成: 综合 {_overall} | 切片 {_scores.get('chunk','-')}(程序 {_chunk_m.get('health_score','-')}) "
    f"| wiki {_scores.get('wiki','-')}(程序 {_wiki_m.get('health_score','-')}/语义 {_wiki_semantic or '-'}) "
    f"| 图数据 {_scores.get('graph','-')}(程序 {_graph_m.get('health_score','-')}/语义 {_graph_semantic or '-'}); "
    f"问题 {len(_issues)} 条, 归因建议 {len(_attribution)} 条"
)

_db = get_sessionmaker()()
try:
    if _qid:
        # quality.py 已建 queued 占位行 → 更新它（状态流转 queued→success）
        _db.execute(
            text("UPDATE kb_quality_run SET status='success', chunk_metrics=:cm, wiki_metrics=:wm, "
                 "graph_metrics=:gm, issues=:iss, attribution=:att, summary=:sum, updated_at=CURRENT_TIMESTAMP "
                 "WHERE id=:rid"),
            {"rid": _qid, "cm": json.dumps(_metrics_map["chunk"], ensure_ascii=False),
             "wm": json.dumps(_metrics_map["wiki"], ensure_ascii=False),
             "gm": json.dumps(_metrics_map["graph"], ensure_ascii=False),
             "iss": json.dumps(_issues, ensure_ascii=False),
             "att": json.dumps(_attribution, ensure_ascii=False),
             "sum": _summary},
        )
    else:
        # 兼容独立运行：自建行
        _rid = uuid.uuid4().hex[:32]
        _db.execute(
            text("INSERT INTO kb_quality_run (id, kb_id, scope, doc_ids, status, chunk_metrics, wiki_metrics, "
                 "graph_metrics, issues, attribution, summary, updated_at) VALUES "
                 "(:id, :kb, :scope, :docs, 'success', :cm, :wm, :gm, :iss, :att, :sum, CURRENT_TIMESTAMP)"),
            {"id": _rid, "kb": _kb, "scope": _scope, "docs": json.dumps(_docs or [], ensure_ascii=False),
             "cm": json.dumps(_metrics_map["chunk"], ensure_ascii=False),
             "wm": json.dumps(_metrics_map["wiki"], ensure_ascii=False),
             "gm": json.dumps(_metrics_map["graph"], ensure_ascii=False),
             "iss": json.dumps(_issues, ensure_ascii=False),
             "att": json.dumps(_attribution, ensure_ascii=False),
             "sum": _summary},
        )
    # kb_quality_report 每维度一条（含语义分）
    _db.execute(text("DELETE FROM kb_quality_report WHERE run_id=:rid"), {"rid": _qid or _rid})
    for _dim, _r in (("chunk", _chunk_r), ("wiki", _wiki_r), ("graph", _graph_r)):
        _db.execute(
            text("INSERT INTO kb_quality_report (id, run_id, kb_id, doc_id, dim, metrics, issues) "
                 "VALUES (:id, :rid, :kb, :doc, :dim, :m, :i)"),
            {"id": uuid.uuid4().hex[:32], "rid": _qid or _rid, "kb": _kb,
             "doc": _docs[0] if _docs and _scope == "single" else None,
             "dim": _dim,
             "m": json.dumps(_metrics_map[_dim], ensure_ascii=False),
             "i": json.dumps([x for x in _issues if x.get("dim") == _dim], ensure_ascii=False)},
        )
    _db.commit()
except Exception as _e:
    _db.rollback()
    try:
        _db.execute(text("UPDATE kb_quality_run SET status='failed', error=:e, updated_at=CURRENT_TIMESTAMP WHERE id=:rid"),
                    {"e": str(_e)[:500], "rid": _qid or (_rid if '_rid' in dir() else "")})
        _db.commit()
    except Exception:
        pass
    raise
finally:
    _db.close()

bindings["quality_summary"] = _summary
print("[n08] " + _summary)

"""

QUALITY_TAPE: dict = {
    "exec_params": {
        "kb_id": "",
        "doc_ids": "",
        "scope": "all",
        "quality_id": ""
    },
    "nodes": [
        {
            "id": "n01",
            "type": "custom",
            "data": {
                "label": "输入定义",
                "stepInst": "def",
                "config": {
                    "queue": "build",
                    "assignments": "[{\"variable\": \"kb_id\", \"expression\": \"bindings.get('kb_id', '')\"}, {\"variable\": \"doc_ids\", \"expression\": \"bindings.get('doc_ids', '')\"}, {\"variable\": \"scope\", \"expression\": \"bindings.get('scope', 'all')\"}, {\"variable\": \"quality_id\", \"expression\": \"bindings.get('quality_id', '')\"}]"
                }
            }
        },
        {
            "id": "n02",
            "type": "custom",
            "data": {
                "label": "切片质量检查",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport json\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\n\nSHORT = 50\nLONG = 2000\nTRUNC_MARKS = (\"…\", \"...\", \"（节选）\", \"[截断]\")\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_docs = [d.strip() for d in str(bindings.get(\"doc_ids\") or \"\").split(\",\") if d.strip()] or None\n_db = get_sessionmaker()()\ntry:\n    if _docs:\n        _cond = \"kb_id=:kb AND document_id IN :ids\"\n        _params = {\"kb\": _kb, \"ids\": tuple(_docs)}\n    else:\n        _cond = \"kb_id=:kb\"\n        _params = {\"kb\": _kb}\n    _rows = _db.execute(\n        text(f\"SELECT document_id, seq, content, meta FROM doc_chunk WHERE {_cond} AND enabled=1 ORDER BY document_id, seq\"),\n        _params,\n    ).all()\nfinally:\n    _db.close()\n\nif not _rows:\n    _r = {\"metrics\": {\"docs\": 0, \"chunks\": 0, \"avg_len\": 0},\n          \"issues\": [{\"dim\": \"chunk\", \"issue_type\": \"no_chunks\", \"detail\": \"无切片数据(文档未解析或无切片)\"}],\n          \"attribution\": [{\"dim\": \"chunk\", \"cause\": \"文档未进入切片阶段或解析失败\", \"suggestion\": \"检查 parse_state / 重跑解析\"}]}\nelse:\n    _by_doc = {}\n    for _rid, _seq, _content, _meta in _rows:\n        _by_doc.setdefault(str(_rid), []).append((int(_seq or 0), str(_content or \"\"), _meta))\n    _issues = []\n    _totals = {\"docs\": len(_by_doc), \"chunks\": 0, \"short\": 0, \"long\": 0, \"truncated\": 0, \"dup\": 0}\n    _per_doc = {}\n    for _doc_id, _chunks in _by_doc.items():\n        _lens = [len(c) for _, c, _ in _chunks]\n        _n = len(_chunks)\n        _totals[\"chunks\"] += _n\n        _short_n = sum(1 for L in _lens if L < SHORT)\n        _long_n = sum(1 for L in _lens if L > LONG)\n        _trunc_n = sum(1 for _, c, _ in _chunks if any(m in c for m in TRUNC_MARKS))\n        _seen = set()\n        _dup_n = 0\n        for _, c, _ in _chunks:\n            _h = __import__(\"hashlib\").md5(c.strip()[:200].encode(\"utf-8\")).hexdigest()\n            if _h in _seen:\n                _dup_n += 1\n            _seen.add(_h)\n        _totals[\"short\"] += _short_n\n        _totals[\"long\"] += _long_n\n        _totals[\"truncated\"] += _trunc_n\n        _totals[\"dup\"] += _dup_n\n        _per_doc[_doc_id] = {\"doc_id\": _doc_id, \"chunks\": _n, \"avg_len\": round(sum(_lens) / _n, 1) if _n else 0,\n                             \"short\": _short_n, \"long\": _long_n, \"truncated\": _trunc_n, \"dup\": _dup_n}\n        if _short_n / max(_n, 1) > 0.3:\n            _issues.append({\"dim\": \"chunk\", \"doc_id\": _doc_id, \"issue_type\": \"too_many_short_chunks\", \"detail\": f\"{_short_n}/{_n} 切片过短(<{SHORT}字),占比 {_short_n/_n:.0%}\"})\n        if _long_n / max(_n, 1) > 0.1:\n            _issues.append({\"dim\": \"chunk\", \"doc_id\": _doc_id, \"issue_type\": \"too_many_long_chunks\", \"detail\": f\"{_long_n}/{_n} 切片过长(>{LONG}字),占比 {_long_n/_n:.0%}\"})\n        if _trunc_n > 0:\n            _issues.append({\"dim\": \"chunk\", \"doc_id\": _doc_id, \"issue_type\": \"truncated_chunks\", \"detail\": f\"{_trunc_n} 个切片含截断标记\"})\n        if _dup_n / max(_n, 1) > 0.2:\n            _issues.append({\"dim\": \"chunk\", \"doc_id\": _doc_id, \"issue_type\": \"duplicate_chunks\", \"detail\": f\"{_dup_n}/{_n} 切片疑似近似重复\"})\n    _short_r = _totals[\"short\"] / max(_totals[\"chunks\"], 1)\n    _long_r = _totals[\"long\"] / max(_totals[\"chunks\"], 1)\n    _trunc_r = _totals[\"truncated\"] / max(_totals[\"chunks\"], 1)\n    _score = max(0, int(100.0 - _short_r * 60 - _long_r * 60 - _trunc_r * 80))\n    _metrics = {**_totals, \"short_ratio\": round(_short_r, 4), \"long_ratio\": round(_long_r, 4),\n                \"health_score\": _score, \"per_doc\": _per_doc}\n    _attr = []\n    if _metrics.get(\"short_ratio\", 0) > 0.3:\n        _attr.append({\"dim\": \"chunk\", \"cause\": \"过短切片占比高 → 切分粒度偏细/按词切分\", \"suggestion\": \"调大 chunk_size 或按段落合并相邻短块\"})\n    if _metrics.get(\"long_ratio\", 0) > 0.1:\n        _attr.append({\"dim\": \"chunk\", \"cause\": \"过长切片占比高 → 切分边界未生效(表/长句整块保留)\", \"suggestion\": \"对长表/长句二次切分,或提高 max_chars 后按语义边界再分\"})\n    if _metrics.get(\"truncated\", 0) > 0:\n        _attr.append({\"dim\": \"chunk\", \"cause\": \"切片带截断标记 → 切分在硬长度处截断而非语义边界\", \"suggestion\": \"开启 overlap/按句号段号切分,减少硬截断\"})\n    if _metrics.get(\"dup\", 0) > 0:\n        _attr.append({\"dim\": \"chunk\", \"cause\": \"近似重复切片 → 重叠窗口/段落重复扫描\", \"suggestion\": \"关闭 overlap 或调小重叠比例,去重后再入库\"})\n    _r = {\"metrics\": _metrics, \"issues\": _issues, \"attribution\": _attr}\n\nbindings[\"chunk_r\"] = _r\nprint(f\"[n02] 切片质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}\")\n"
                }
            }
        },
        {
            "id": "n03",
            "type": "custom",
            "data": {
                "label": "Wiki质量检查",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport re\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\n\nPLACEHOLDER = \"*由 LLM 生成*\"\n_SOURCE_MARKS = (\"出处\", \"来源制度\", \"原文关联\", \"来源\")\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_db = get_sessionmaker()()\ntry:\n    _pages = _db.execute(\n        text(\"SELECT id, slug, title, page_type, content, folder_id, folder_ids FROM wiki_page WHERE kb_id=:kb AND status='active'\"),\n        {\"kb\": _kb},\n    ).all()\n    _links = _db.execute(\n        text(\"SELECT from_page_id, to_page_id FROM wiki_link WHERE kb_id=:kb\"),\n        {\"kb\": _kb},\n    ).all()\nfinally:\n    _db.close()\n\nif not _pages:\n    _r = {\"metrics\": {\"pages\": 0, \"health_score\": 0},\n          \"issues\": [{\"dim\": \"wiki\", \"issue_type\": \"no_pages\", \"detail\": \"无 wiki 页面(未构建或构建失败)\"}],\n          \"attribution\": [{\"dim\": \"wiki\", \"cause\": \"wiki 构建未执行或全部失败\", \"suggestion\": \"检查构建编排 run 状态 / 重跑构建\"}]}\nelse:\n    _by_id = {str(p[0]): p for p in _pages}\n    _slug_by_id = {str(p[0]): str(p[1]) for p in _pages}\n    _linked_ids = {str(l[0]) for l in _links} | {str(l[1]) for l in _links}\n    _issues = []\n    _empty = [p for p in _pages if not (p[4] or \"\").strip()]\n    for _p in _empty:\n        _issues.append({\"dim\": \"wiki\", \"slug\": str(_p[1]), \"page_type\": str(_p[3]), \"issue_type\": \"empty_content\", \"detail\": \"页面内容为空\"})\n    _orphans = [p for p in _pages if str(p[0]) not in _linked_ids]\n    for _p in _orphans:\n        _issues.append({\"dim\": \"wiki\", \"slug\": str(_p[1]), \"page_type\": str(_p[3]), \"issue_type\": \"orphan\", \"detail\": \"无任何双向链接\"})\n    _broken = 0\n    _broken_slugs = set()\n    for _p in _pages:\n        _targets = re.findall(r\"\\[\\[([^\\]|]+)\", _p[4] or \"\")\n        for _t in _targets:\n            _t = _t.strip()\n            if _t and _t not in _slug_by_id.values():\n                _broken += 1\n                _broken_slugs.add(_t)\n    if _broken:\n        _issues.append({\"dim\": \"wiki\", \"issue_type\": \"broken_links\", \"detail\": f\"{_broken} 处断链,目标示例: {list(_broken_slugs)[:5]}\"})\n    _placeholder = [p for p in _pages if PLACEHOLDER in (p[4] or \"\")]\n    for _p in _placeholder:\n        _issues.append({\"dim\": \"wiki\", \"slug\": str(_p[1]), \"page_type\": str(_p[3]), \"issue_type\": \"llm_placeholder\", \"detail\": \"LLM 解读占位未填充\"})\n    _no_source = [p for p in _pages if not any(m in (p[4] or \"\") for m in _SOURCE_MARKS)]\n    for _p in _no_source:\n        _issues.append({\"dim\": \"wiki\", \"slug\": str(_p[1]), \"page_type\": str(_p[3]), \"issue_type\": \"missing_source\", \"detail\": \"页面无出处/原文引用标记\"})\n    _no_folder = [p for p in _pages if not (p[5] or p[6] or \"\")]\n    for _p in _no_folder:\n        _issues.append({\"dim\": \"wiki\", \"slug\": str(_p[1]), \"page_type\": str(_p[3]), \"issue_type\": \"no_folder\", \"detail\": \"页面未挂载任何目录\"})\n    _n = len(_pages)\n    _s = 100.0\n    _s -= (len(_empty) / _n) * 100 + (len(_orphans) / _n) * 60\n    _s -= min(_broken, _n) * 0.5\n    _s -= (len(_placeholder) / _n) * 80 + (len(_no_source) / _n) * 40\n    _metrics = {\"pages\": _n, \"empty\": len(_empty), \"orphan\": len(_orphans), \"broken_links\": _broken,\n                \"placeholder\": len(_placeholder), \"missing_source\": len(_no_source), \"no_folder\": len(_no_folder),\n                \"empty_ratio\": round(len(_empty) / _n, 4), \"orphan_ratio\": round(len(_orphans) / _n, 4),\n                \"health_score\": max(0, int(_s))}\n    _attr = []\n    if _metrics.get(\"empty_ratio\", 0) > 0.05:\n        _attr.append({\"dim\": \"wiki\", \"cause\": \"空内容页占比高 → 建页流程在生成内容前中断/LLM 输出为空\", \"suggestion\": \"检查构建日志中该页的生成步骤;对空页重跑补写\"})\n    if _metrics.get(\"orphan_ratio\", 0) > 0.3:\n        _attr.append({\"dim\": \"wiki\", \"cause\": \"孤立页占比高 → 实体抽取过泛/页面间未建立互链\", \"suggestion\": \"收敛实体抽取范围,构建尾部执行 rebuild_links\"})\n    if _metrics.get(\"broken_links\", 0) > 0:\n        _attr.append({\"dim\": \"wiki\", \"cause\": \"断链 = 引用了未创建的页面/实体(抽取了未建的实体名)\", \"suggestion\": \"构建前先建全部实体页再补引用,或对断链目标补建页面\"})\n    if _metrics.get(\"placeholder\", 0) > 0:\n        _attr.append({\"dim\": \"wiki\", \"cause\": \"LLM 占位未填充 → 解读步骤未跑/供应商失败中断\", \"suggestion\": \"重跑 LLM 解读补位(断点续传),或修供应商可达性\"})\n    if _metrics.get(\"missing_source\", 0) > 0:\n        _attr.append({\"dim\": \"wiki\", \"cause\": \"页面缺出处标记 → 渲染模板未带原文出处章节\", \"suggestion\": \"渲染器补「出处」行,保证证据链可回原文\"})\n    _r = {\"metrics\": _metrics, \"issues\": _issues, \"attribution\": _attr}\n\nbindings[\"wiki_r\"] = _r\nprint(f\"[n03] Wiki质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}\")\n"
                }
            }
        },
        {
            "id": "n04",
            "type": "custom",
            "data": {
                "label": "图数据质量检查",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport os\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\nfrom neo4j import GraphDatabase\n\n_STABLE_REL_TYPES = (\"约束\", \"主体\", \"客体\", \"条件\", \"出自\", \"依据\")\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_label = \"ENTITY\" + _kb.replace(\"-\", \"_\")\n_uri = os.getenv(\"NEO4J_URI\", \"bolt://kb-neo4j:7687\")\n_user = os.getenv(\"NEO4J_USERNAME\", \"neo4j\")\n_pwd = os.getenv(\"NEO4J_PASSWORD\", \"\")\n_driver = GraphDatabase.driver(_uri, auth=(_user, _pwd))\ntry:\n    with _driver.session() as _sess:\n        _nodes = _sess.run(f\"MATCH (n:`{_label}`) RETURN count(n) AS c\").single()[\"c\"]\n        _rels = _sess.run(f\"MATCH (a:`{_label}`)-[r]-(b:`{_label}`) RETURN count(r) AS c\").single()[\"c\"]\n        _dumb = _sess.run(f\"MATCH (n:`{_label}`) WHERE n.attributes IS NULL OR size(n.attributes)=0 RETURN count(n) AS c\").single()[\"c\"]\n        _rel_rows = _sess.run(f\"MATCH (a:`{_label}`)-[r]-(b:`{_label}`) RETURN type(r) AS t, count(*) AS c ORDER BY c DESC\").data()\n        _rel_types = {_r[\"t\"]: _r[\"c\"] for _r in _rel_rows}\n        _untraceable = _sess.run(f\"MATCH (a:`{_label}`)-[r]-(b:`{_label}`) WHERE (r.rule_slug IS NULL OR r.rule_slug='') AND (r.note IS NULL OR r.note='') RETURN count(r) AS c\").single()[\"c\"]\n        _node_slugs = _sess.run(f\"MATCH (n:`{_label}`) WHERE n.page_slug IS NOT NULL AND n.page_slug<>'' RETURN collect(DISTINCT n.page_slug) AS slugs\").single()[\"slugs\"] or []\nfinally:\n    _driver.close()\n\n_issues = []\nif _dumb > 0:\n    _issues.append({\"dim\": \"graph\", \"issue_type\": \"dumb_nodes\", \"detail\": f\"{_dumb} 个哑节点(无 attributes),占 {_dumb/max(_nodes,1):.0%}\"})\n_bad_types = {t: c for t, c in _rel_types.items() if t not in _STABLE_REL_TYPES}\nif _bad_types:\n    _issues.append({\"dim\": \"graph\", \"issue_type\": \"illegal_rel_types\", \"detail\": f\"非稳定边类型: {_bad_types}\"})\nif _untraceable > 0:\n    _issues.append({\"dim\": \"graph\", \"issue_type\": \"untraceable_rels\", \"detail\": f\"{_untraceable} 条边无 rule_slug/note,无法回溯规则卡/原文\"})\n\n_db = get_sessionmaker()()\ntry:\n    if _node_slugs:\n        _existing = _db.execute(text(\"SELECT slug FROM wiki_page WHERE kb_id=:kb AND slug IN :slugs AND status='active'\"), {\"kb\": _kb, \"slugs\": tuple(_node_slugs)}).scalars().all()\n        _missing_slugs = [s for s in _node_slugs if s not in set(_existing)]\n        if _missing_slugs:\n            _issues.append({\"dim\": \"graph\", \"issue_type\": \"wiki_missing_nodes\", \"detail\": f\"{len(_missing_slugs)} 个图节点对应 wiki 页不存在,示例: {_missing_slugs[:5]}\"})\n    _wiki_pages = _db.execute(text(\"SELECT count(*) FROM wiki_page WHERE kb_id=:kb AND status='active'\"), {\"kb\": _kb}).scalar() or 0\nfinally:\n    _db.close()\n\n_health = 100.0\nif _nodes:\n    _health -= (_dumb / _nodes) * 80\n    _health -= (_untraceable / max(_rels, 1)) * 60\nif _bad_types:\n    _health -= 40\nif _issues:\n    _health -= 10 * len(_issues)\n_metrics = {\"nodes\": _nodes, \"rels\": _rels, \"dumb_nodes\": _dumb, \"rel_types\": _rel_types,\n            \"untraceable_rels\": _untraceable, \"wiki_pages\": _wiki_pages,\n            \"dumb_ratio\": round(_dumb / max(_nodes, 1), 4) if _nodes else 0,\n            \"health_score\": max(0, int(_health))}\n_attr = []\nif _metrics.get(\"dumb_nodes\", 0) > 0:\n    _attr.append({\"dim\": \"graph\", \"cause\": \"哑节点 = 写入方未补 attributes(仅 name+kg)\", \"suggestion\": \"图谱写入脚本收尾兜底补 attributes(定义/来源)\"})\nif _bad_types:\n    _attr.append({\"dim\": \"graph\", \"cause\": \"存在非稳定边类型 → 写入方未收敛到 6 种稳定边\", \"suggestion\": \"关系类型映射归一(约束/主体/客体/条件/出自/依据)\"})\nif _metrics.get(\"untraceable_rels\", 0) > 0:\n    _attr.append({\"dim\": \"graph\", \"cause\": \"边缺 rule_slug/note → 无法回溯规则卡/原文证据\", \"suggestion\": \"写入时给每条边带 rule_slug+note(原文句子)\"})\n_r = {\"metrics\": _metrics, \"issues\": _issues, \"attribution\": _attr}\n\nbindings[\"graph_r\"] = _r\nprint(f\"[n04] 图数据质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}\")\n"
                }
            }
        },
        {
            "id": "n05",
            "type": "custom",
            "data": {
                "label": "Wiki单文档语义评估",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport json\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\n\n\ndef _llm(prompt, system=\"\", temperature=0.2, max_tokens=2048, json_mode=True):\n    from api.services.chat import ChatClient, load_chat_config, ChatMessage\n    from api.db import get_sessionmaker\n    import time as _t\n    _db = get_sessionmaker()()\n    try:\n        cfg = load_chat_config(_db, model_id=None)\n        client = ChatClient(cfg)\n        msgs = []\n        if system:\n            msgs.append(ChatMessage(role=\"system\", content=system))\n        msgs.append(ChatMessage(role=\"user\", content=prompt))\n        last = None\n        for attempt in range(4):\n            try:\n                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)\n                if json_mode:\n                    text_ = (raw or \"\").strip()\n                    if text_.startswith(\"```\"):\n                        text_ = text_.strip(\"`\")\n                        if text_.startswith(\"json\"):\n                            text_ = text_[4:].strip()\n                    return __import__(\"json\").loads(text_)\n                return raw or \"\"\n            except Exception as e:\n                last = e\n                resp = getattr(e, \"response\", None)\n                code = getattr(resp, \"status_code\", None) if resp is not None else None\n                if code == 429:\n                    _t.sleep(min(15 * (attempt + 1), 45))\n                else:\n                    _t.sleep(3)\n        raise last\n    finally:\n        _db.close()\n\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_db = get_sessionmaker()()\ntry:\n    _pages = _db.execute(\n        text(\"SELECT slug, title, content FROM wiki_page WHERE kb_id=:kb AND status='active' AND content IS NOT NULL ORDER BY updated_at DESC LIMIT 15\"),\n        {\"kb\": _kb},\n    ).all()\nfinally:\n    _db.close()\n\nif not _pages:\n    bindings[\"wiki_sem_r\"] = {\"metrics\": {\"health_score\": 0, \"pages_eval\": 0}, \"issues\": [], \"attribution\": []}\n    print(\"[n05] wiki 单文档语义: 无页面, 跳过\")\nelse:\n    _items = []\n    for _p in _pages:\n        _items.append({\"slug\": str(_p[0]), \"title\": str(_p[1] or \"\"), \"content\": str(_p[2] or \"\")[:1500]})\n    _prompt = (\n        \"你是知识库质量评估专家。以下是从 wiki 中抽出的若干页面（含 slug/title/content）。\\n\"\n        \"对每个页面从**单文档视角**评估:\\n\"\n        \"1. 是否能让读者独立看出这页在讲什么（主题明确、开场即点题）\\n\"\n        \"2. 内容是否完整可理解（有上下文、有结论/定义，不是碎片或只有标题）\\n\"\n        \"3. 是否有明显占位/未完成/LLM 残留\\n\"\n        \"输出 JSON 数组，每个元素: {slug, score(0-100, 主题清晰度与完整度的综合分), issue(null 或简短问题描述)}\\n\"\n        \"只输出 JSON。页面列表:\\n\" + json.dumps(_items, ensure_ascii=False)\n    )\n    try:\n        _out = _llm(_prompt, system=\"你是严谨的知识库质量评估器。只输出 JSON，不输出其他内容。\")\n    except Exception as _e:\n        _r = {\"metrics\": {\"health_score\": None, \"pages_eval\": 0, \"error\": str(_e)[:200]},\n              \"issues\": [{\"issue_type\": \"sem_eval_error\", \"detail\": \"LLM 语义评估失败: \" + str(_e)}], \"attribution\": []}\n        bindings[\"wiki_sem_r\"] = _r\n        print(\"[n05] wiki 单文档语义: LLM 失败, 降级 -> \" + str(_e))\n    else:\n        _rows = _out if isinstance(_out, list) else (_out.get(\"items\") or _out.get(\"results\") or [])\n        _issues = []\n        _scores = []\n        for _row in _rows if isinstance(_rows, list) else []:\n            _slug = str(_row.get(\"slug\") or \"\")\n            _sc = _row.get(\"score\")\n            if isinstance(_sc, (int, float)):\n                _scores.append(float(_sc))\n            _iss = str(_row.get(\"issue\") or \"\").strip()\n            if _iss and _iss.lower() not in (\"null\", \"none\", \"无\", \"no\"):\n                _issues.append({\"dim\": \"wiki\", \"slug\": _slug, \"issue_type\": \"wiki_sem_unclear\",\n                                \"detail\": _iss[:200]})\n        _avg = round(sum(_scores) / len(_scores), 1) if _scores else None\n        _r = {\"metrics\": {\"health_score\": _avg, \"pages_eval\": len(_rows), \"semantic\": \"single_doc\"},\n              \"issues\": _issues,\n              \"attribution\": ([{\"dim\": \"wiki\", \"cause\": \"存在单文档视角看不明白的页面\", \"suggestion\": \"重写页面开场点题,补充背景与结论\"}] if _issues else [])}\n        bindings[\"wiki_sem_r\"] = _r\n        print(\"[n05] wiki 单文档语义: 均分 \" + str(_avg) + \" 问题 \" + str(len(_issues)))\n"
                }
            }
        },
        {
            "id": "n06",
            "type": "custom",
            "data": {
                "label": "Wiki-原文对比评估",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport json\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\n\n\ndef _llm(prompt, system=\"\", temperature=0.2, max_tokens=2048, json_mode=True):\n    from api.services.chat import ChatClient, load_chat_config, ChatMessage\n    from api.db import get_sessionmaker\n    import time as _t\n    _db = get_sessionmaker()()\n    try:\n        cfg = load_chat_config(_db, model_id=None)\n        client = ChatClient(cfg)\n        msgs = []\n        if system:\n            msgs.append(ChatMessage(role=\"system\", content=system))\n        msgs.append(ChatMessage(role=\"user\", content=prompt))\n        last = None\n        for attempt in range(4):\n            try:\n                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)\n                if json_mode:\n                    text_ = (raw or \"\").strip()\n                    if text_.startswith(\"```\"):\n                        text_ = text_.strip(\"`\")\n                        if text_.startswith(\"json\"):\n                            text_ = text_[4:].strip()\n                    return __import__(\"json\").loads(text_)\n                return raw or \"\"\n            except Exception as e:\n                last = e\n                resp = getattr(e, \"response\", None)\n                code = getattr(resp, \"status_code\", None) if resp is not None else None\n                if code == 429:\n                    _t.sleep(min(15 * (attempt + 1), 45))\n                else:\n                    _t.sleep(3)\n        raise last\n    finally:\n        _db.close()\n\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_db = get_sessionmaker()()\ntry:\n    _pages = _db.execute(\n        text(\"SELECT slug, title, content, source_refs FROM wiki_page WHERE kb_id=:kb AND status='active' AND content IS NOT NULL ORDER BY updated_at DESC LIMIT 10\"),\n        {\"kb\": _kb},\n    ).all()\nfinally:\n    _db.close()\n\nif not _pages:\n    bindings[\"wiki_cmp_r\"] = {\"metrics\": {\"health_score\": 0, \"pages_cmp\": 0}, \"issues\": [], \"attribution\": []}\n    print(\"[n06] wiki-原文对比: 无页面, 跳过\")\nelse:\n    _items = []\n    for _p in _pages:\n        _refs = _p[3] or []\n        _doc_ids = []\n        for _r in _refs if isinstance(_refs, list) else []:\n            if isinstance(_r, dict) and _r.get(\"doc\"):\n                _doc_ids.append(str(_r[\"doc\"]))\n        _src = \"\"\n        if _doc_ids:\n            _db2 = get_sessionmaker()()\n            try:\n                _chunks = _db2.execute(\n                    text(\"SELECT document_id, seq, content FROM doc_chunk WHERE kb_id=:kb AND document_id IN :ids ORDER BY document_id, seq LIMIT 25\"),\n                    {\"kb\": _kb, \"ids\": tuple(_doc_ids)},\n                ).all()\n                _src = \"\\n\".join(\"[\" + str(c[0])[:8] + \"#\" + str(c[1]) + \"] \" + str(c[2] or \"\")[:400] for c in _chunks)\n            finally:\n                _db2.close()\n        _items.append({\"slug\": str(_p[0]), \"title\": str(_p[1] or \"\"),\n                       \"wiki_content\": str(_p[2] or \"\")[:1800],\n                       \"source_excerpt\": _src[:2500]})\n    _prompt = (\n        \"你是知识库质量评估专家，负责**核对 wiki 页面与原始文档是否一致**。\\n\"\n        \"对每个页面，把 wiki 内容与提供的原文摘录（source_excerpt，可能不完整）对比，找出**明显错误**：\\n\"\n        \"1. 事实性错误（数字/日期/主体/结论与原文明说矛盾）\\n\"\n        \"2. 编造/无中生有（原文没有的内容被 wiki 写成事实）\\n\"\n        \"3. 张冠李戴/移花接木（把别处的定义安到这页）\\n\"\n        \"注意：source_excerpt 若为空或明显不完整，标注 insufficient 而不是编造错误。\\n\"\n        \"输出 JSON 数组，每个元素: {slug, has_error(true/false), errors: [简短错误描述], confidence(low/medium/high)}\\n\"\n        \"只输出 JSON。\\n页面列表: \" + json.dumps(_items, ensure_ascii=False)\n    )\n    try:\n        _out = _llm(_prompt, system=\"你是严谨的知识库质量评估器，只依据给定原文判断，不确定就标注 insufficient。只输出 JSON。\")\n    except Exception as _e:\n        _r = {\"metrics\": {\"health_score\": None, \"pages_cmp\": 0, \"error\": str(_e)[:200]},\n              \"issues\": [{\"issue_type\": \"sem_eval_error\", \"detail\": \"LLM 语义评估失败: \" + str(_e)}], \"attribution\": []}\n        bindings[\"wiki_cmp_r\"] = _r\n        print(\"[n06] wiki-原文对比: LLM 失败, 降级 -> \" + str(_e))\n    else:\n        _rows = _out if isinstance(_out, list) else (_out.get(\"items\") or _out.get(\"results\") or [])\n        _issues = []\n        _error_pages = 0\n        for _row in _rows if isinstance(_rows, list) else []:\n            _slug = str(_row.get(\"slug\") or \"\")\n            if _row.get(\"has_error\") is True or _row.get(\"has_error\") in (1, \"true\", \"True\", \"yes\"):\n                _errs = _row.get(\"errors\") or []\n                if isinstance(_errs, str):\n                    _errs = [_errs]\n                for _e2 in _errs[:3]:\n                    _issues.append({\"dim\": \"wiki\", \"slug\": _slug, \"issue_type\": \"wiki_sem_error\",\n                                    \"detail\": str(_e2)[:200]})\n                _error_pages += 1\n        _n = len(_rows) or 1\n        _score = max(0, int(100 - (_error_pages / _n) * 100))\n        _r = {\"metrics\": {\"health_score\": _score, \"pages_cmp\": len(_rows), \"error_pages\": _error_pages,\n                          \"semantic\": \"doc_compare\"},\n              \"issues\": _issues,\n              \"attribution\": ([{\"dim\": \"wiki\", \"cause\": \"wiki 与原文存在不一致/编造内容\", \"suggestion\": \"以原文为准修正 wiki 页面,构建时加强 LLM 事实约束\"}] if _issues else [])}\n        bindings[\"wiki_cmp_r\"] = _r\n        print(\"[n06] wiki-原文对比: 得分 \" + str(_score) + \" 错误页 \" + str(_error_pages) + \"/\" + str(len(_rows)))\n"
                }
            }
        },
        {
            "id": "n07",
            "type": "custom",
            "data": {
                "label": "图数据语义评估",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport json\nimport os\nfrom neo4j import GraphDatabase\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\n\n\ndef _llm(prompt, system=\"\", temperature=0.2, max_tokens=2048, json_mode=True):\n    from api.services.chat import ChatClient, load_chat_config, ChatMessage\n    from api.db import get_sessionmaker\n    import time as _t\n    _db = get_sessionmaker()()\n    try:\n        cfg = load_chat_config(_db, model_id=None)\n        client = ChatClient(cfg)\n        msgs = []\n        if system:\n            msgs.append(ChatMessage(role=\"system\", content=system))\n        msgs.append(ChatMessage(role=\"user\", content=prompt))\n        last = None\n        for attempt in range(4):\n            try:\n                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)\n                if json_mode:\n                    text_ = (raw or \"\").strip()\n                    if text_.startswith(\"```\"):\n                        text_ = text_.strip(\"`\")\n                        if text_.startswith(\"json\"):\n                            text_ = text_[4:].strip()\n                    return __import__(\"json\").loads(text_)\n                return raw or \"\"\n            except Exception as e:\n                last = e\n                resp = getattr(e, \"response\", None)\n                code = getattr(resp, \"status_code\", None) if resp is not None else None\n                if code == 429:\n                    _t.sleep(min(15 * (attempt + 1), 45))\n                else:\n                    _t.sleep(3)\n        raise last\n    finally:\n        _db.close()\n\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_label = \"ENTITY\" + _kb.replace(\"-\", \"_\")\n_uri = os.getenv(\"NEO4J_URI\", \"bolt://kb-neo4j:7687\")\n_user = os.getenv(\"NEO4J_USERNAME\", \"neo4j\")\n_pwd = os.getenv(\"NEO4J_PASSWORD\", \"\")\n_driver = GraphDatabase.driver(_uri, auth=(_user, _pwd))\ntry:\n    with _driver.session() as _sess:\n        _sample_nodes = _sess.run(\n            \"MATCH (n:`\" + _label + \"`) WHERE n.name IS NOT NULL RETURN n.name AS name, n.entity_type AS et LIMIT 12\"\n        ).data()\n        _sample_rels = _sess.run(\n            \"MATCH (a:`\" + _label + \"`)-[r]->(b:`\" + _label + \"`) RETURN a.name AS src, type(r) AS rel, b.name AS dst, r.note AS note LIMIT 12\"\n        ).data()\n        _total_nodes = _sess.run(\"MATCH (n:`\" + _label + \"`) RETURN count(n) AS c\").single()[\"c\"] or 0\nfinally:\n    _driver.close()\n\nif not _sample_nodes and not _sample_rels:\n    bindings[\"graph_sem_r\"] = {\"metrics\": {\"health_score\": 0, \"nodes_sampled\": 0, \"rels_sampled\": 0}, \"issues\": [], \"attribution\": []}\n    print(\"[n07] 图数据语义: 图无数据, 跳过\")\nelse:\n    _prompt = (\n        \"你是知识库图谱质量评估专家。以下是 Neo4j 语义图中抽样的**节点**与**关系**。\\n\"\n        \"**节点层**（评估实体名是否有意义）：\" + json.dumps(_sample_nodes, ensure_ascii=False) +\n        \"\\n**边层**（评估三元组 A-关系-B 语义是否正确：关系类型是否符合两端实体、note 是否支撑该关系）：\" +\n        json.dumps(_sample_rels, ensure_ascii=False) +\n        \"\\n请输出 JSON 对象: {node_issues: [{name, problem}], rel_issues: [{src, rel, dst, problem}], \"\n        \"overall_score: 0-100(图整体可理解性与语义正确性综合分), summary: 一句话总评}。\\n\"\n        \"问题包括: 实体名无意义/乱码/太泛、关系类型与实体不匹配、note 与关系矛盾或缺失、三元组明显错误。\\n只输出 JSON。\"\n    )\n    try:\n        _out = _llm(_prompt, system=\"你是严谨的知识库图谱质量评估器。只输出 JSON。\")\n    except Exception as _e:\n        _r = {\"metrics\": {\"health_score\": None, \"nodes_sampled\": 0, \"rels_sampled\": 0, \"error\": str(_e)[:200]},\n              \"issues\": [{\"issue_type\": \"sem_eval_error\", \"detail\": \"LLM 语义评估失败: \" + str(_e)}], \"attribution\": []}\n        bindings[\"graph_sem_r\"] = _r\n        print(\"[n07] 图数据语义: LLM 失败, 降级 -> \" + str(_e))\n    else:\n        _n_issues = (_out.get(\"node_issues\") or []) if isinstance(_out, dict) else []\n        _r_issues = (_out.get(\"rel_issues\") or []) if isinstance(_out, dict) else []\n        _issues = []\n        for _it in _n_issues[:8] if isinstance(_n_issues, list) else []:\n            _issues.append({\"dim\": \"graph\", \"issue_type\": \"graph_sem_node\",\n                            \"detail\": (\"节点 [\" + str(_it.get(\"name\", \"\")) + \"] \" + str(_it.get(\"problem\", \"\"))).strip()[:200]})\n        for _it in _r_issues[:8] if isinstance(_r_issues, list) else []:\n            _issues.append({\"dim\": \"graph\", \"issue_type\": \"graph_sem_rel\",\n                            \"detail\": (\"边 [\" + str(_it.get(\"src\", \"\")) + \"-\" + str(_it.get(\"rel\", \"\")) + \"->\" + str(_it.get(\"dst\", \"\")) + \"] \" + str(_it.get(\"problem\", \"\"))).strip()[:200]})\n        _sc = _out.get(\"overall_score\") if isinstance(_out, dict) else None\n        _score = int(_sc) if isinstance(_sc, (int, float)) else None\n        _r = {\"metrics\": {\"health_score\": _score, \"nodes_sampled\": len(_sample_nodes), \"rels_sampled\": len(_sample_rels),\n                          \"total_nodes\": _total_nodes, \"semantic\": \"graph_3layer\"},\n              \"issues\": _issues,\n              \"attribution\": ([{\"dim\": \"graph\", \"cause\": \"图谱存在语义问题(实体名/关系/note)\", \"suggestion\": \"检查实体抽取与关系写入口径\"}] if _issues else [])}\n        bindings[\"graph_sem_r\"] = _r\n        print(\"[n07] 图数据语义: 得分 \" + str(_score) + \" 问题 \" + str(len(_issues)))\n"
                }
            }
        },
        {
            "id": "n08",
            "type": "custom",
            "data": {
                "label": "汇总+落库",
                "stepInst": "script",
                "config": {
                    "queue": "build",
                    "code": "\nimport json\nimport uuid\nfrom api.db import get_sessionmaker\nfrom sqlalchemy import text\n\n_kb = bindings.get(\"kb_id\") or \"\"\n_docs = [d.strip() for d in str(bindings.get(\"doc_ids\") or \"\").split(\",\") if d.strip()] or None\n_scope = str(bindings.get(\"scope\") or \"all\")\n_qid = str(bindings.get(\"quality_id\") or \"\").strip()  # quality.py 传入的占位行 id\n\n_chunk_r = bindings.get(\"chunk_r\") or {}\n_wiki_r = bindings.get(\"wiki_r\") or {}\n_graph_r = bindings.get(\"graph_r\") or {}\n_wiki_sem_r = bindings.get(\"wiki_sem_r\") or {}\n_wiki_cmp_r = bindings.get(\"wiki_cmp_r\") or {}\n_graph_sem_r = bindings.get(\"graph_sem_r\") or {}\n\n# 每维度指标：程序性 health_score + 语义 semantic_score\ndef _m(r):\n    return r.get(\"metrics\") or {}\n\n_chunk_m = _m(_chunk_r)\n_wiki_m = _m(_wiki_r)\n_graph_m = _m(_graph_r)\n# wiki 语义分 = 单文档 + 原文对比 两路平均（None 则用另一路）\n_wiki_sem_scores = [s for s in (_m(_wiki_sem_r).get(\"health_score\"), _m(_wiki_cmp_r).get(\"health_score\")) if isinstance(s, (int, float))]\n_wiki_semantic = round(sum(_wiki_sem_scores) / len(_wiki_sem_scores), 1) if _wiki_sem_scores else None\n# 图语义分\n_graph_semantic = _m(_graph_sem_r).get(\"health_score\")\n_graph_semantic = int(_graph_semantic) if isinstance(_graph_semantic, (int, float)) else None\n\n# 综合分：每维度 程序×0.6 + 语义×0.4（无语义分则该维直接用程序分）\ndef _dim_score(prog, sem_score):\n    if not isinstance(prog, (int, float)):\n        return None\n    if isinstance(sem_score, (int, float)):\n        return round(prog * 0.6 + sem_score * 0.4, 1)\n    return round(float(prog), 1)\n\n_dims = {\n    \"chunk\": {\"prog\": _chunk_m.get(\"health_score\"), \"semantic\": None},\n    \"wiki\": {\"prog\": _wiki_m.get(\"health_score\"), \"semantic\": _wiki_semantic},\n    \"graph\": {\"prog\": _graph_m.get(\"health_score\"), \"semantic\": _graph_semantic},\n}\n_scores = {d: _dim_score(v[\"prog\"], v[\"semantic\"]) for d, v in _dims.items()}\n_valid = [s for s in _scores.values() if isinstance(s, (int, float))]\n_overall = round(sum(_valid) / len(_valid), 1) if _valid else 0\n\n# issues / attribution 合并（程序性 + 语义）\n_issues = []\n_attribution = []\nfor r in (_chunk_r, _wiki_r, _graph_r, _wiki_sem_r, _wiki_cmp_r, _graph_sem_r):\n    for it in (r.get(\"issues\") or []):\n        _issues.append(it)\n    for at in (r.get(\"attribution\") or []):\n        _attribution.append(at)\n\n_metrics_map = {\n    \"chunk\": {**{k: v for k, v in _chunk_m.items() if k != \"per_doc\"},\n              \"semantic_score\": None, \"final_score\": _scores.get(\"chunk\")},\n    \"wiki\": {**_wiki_m, \"semantic_score\": _wiki_semantic, \"final_score\": _scores.get(\"wiki\")},\n    \"graph\": {**_graph_m, \"semantic_score\": _graph_semantic, \"final_score\": _scores.get(\"graph\")},\n}\n_summary = (\n    f\"评估完成: 综合 {_overall} | 切片 {_scores.get('chunk','-')}(程序 {_chunk_m.get('health_score','-')}) \"\n    f\"| wiki {_scores.get('wiki','-')}(程序 {_wiki_m.get('health_score','-')}/语义 {_wiki_semantic or '-'}) \"\n    f\"| 图数据 {_scores.get('graph','-')}(程序 {_graph_m.get('health_score','-')}/语义 {_graph_semantic or '-'}); \"\n    f\"问题 {len(_issues)} 条, 归因建议 {len(_attribution)} 条\"\n)\n\n_db = get_sessionmaker()()\ntry:\n    if _qid:\n        # quality.py 已建 queued 占位行 → 更新它（状态流转 queued→success）\n        _db.execute(\n            text(\"UPDATE kb_quality_run SET status='success', chunk_metrics=:cm, wiki_metrics=:wm, \"\n                 \"graph_metrics=:gm, issues=:iss, attribution=:att, summary=:sum, updated_at=CURRENT_TIMESTAMP \"\n                 \"WHERE id=:rid\"),\n            {\"rid\": _qid, \"cm\": json.dumps(_metrics_map[\"chunk\"], ensure_ascii=False),\n             \"wm\": json.dumps(_metrics_map[\"wiki\"], ensure_ascii=False),\n             \"gm\": json.dumps(_metrics_map[\"graph\"], ensure_ascii=False),\n             \"iss\": json.dumps(_issues, ensure_ascii=False),\n             \"att\": json.dumps(_attribution, ensure_ascii=False),\n             \"sum\": _summary},\n        )\n    else:\n        # 兼容独立运行：自建行\n        _rid = uuid.uuid4().hex[:32]\n        _db.execute(\n            text(\"INSERT INTO kb_quality_run (id, kb_id, scope, doc_ids, status, chunk_metrics, wiki_metrics, \"\n                 \"graph_metrics, issues, attribution, summary, updated_at) VALUES \"\n                 \"(:id, :kb, :scope, :docs, 'success', :cm, :wm, :gm, :iss, :att, :sum, CURRENT_TIMESTAMP)\"),\n            {\"id\": _rid, \"kb\": _kb, \"scope\": _scope, \"docs\": json.dumps(_docs or [], ensure_ascii=False),\n             \"cm\": json.dumps(_metrics_map[\"chunk\"], ensure_ascii=False),\n             \"wm\": json.dumps(_metrics_map[\"wiki\"], ensure_ascii=False),\n             \"gm\": json.dumps(_metrics_map[\"graph\"], ensure_ascii=False),\n             \"iss\": json.dumps(_issues, ensure_ascii=False),\n             \"att\": json.dumps(_attribution, ensure_ascii=False),\n             \"sum\": _summary},\n        )\n    # kb_quality_report 每维度一条（含语义分）\n    _db.execute(text(\"DELETE FROM kb_quality_report WHERE run_id=:rid\"), {\"rid\": _qid or _rid})\n    for _dim, _r in ((\"chunk\", _chunk_r), (\"wiki\", _wiki_r), (\"graph\", _graph_r)):\n        _db.execute(\n            text(\"INSERT INTO kb_quality_report (id, run_id, kb_id, doc_id, dim, metrics, issues) \"\n                 \"VALUES (:id, :rid, :kb, :doc, :dim, :m, :i)\"),\n            {\"id\": uuid.uuid4().hex[:32], \"rid\": _qid or _rid, \"kb\": _kb,\n             \"doc\": _docs[0] if _docs and _scope == \"single\" else None,\n             \"dim\": _dim,\n             \"m\": json.dumps(_metrics_map[_dim], ensure_ascii=False),\n             \"i\": json.dumps([x for x in _issues if x.get(\"dim\") == _dim], ensure_ascii=False)},\n        )\n    _db.commit()\nexcept Exception as _e:\n    _db.rollback()\n    try:\n        _db.execute(text(\"UPDATE kb_quality_run SET status='failed', error=:e, updated_at=CURRENT_TIMESTAMP WHERE id=:rid\"),\n                    {\"e\": str(_e)[:500], \"rid\": _qid or (_rid if '_rid' in dir() else \"\")})\n        _db.commit()\n    except Exception:\n        pass\n    raise\nfinally:\n    _db.close()\n\nbindings[\"quality_summary\"] = _summary\nprint(\"[n08] \" + _summary)\n"
                }
            }
        },
        {
            "id": "n09",
            "type": "custom",
            "data": {
                "label": "完成汇总",
                "stepInst": "print",
                "config": {
                    "text": "{{quality_summary}}",
                    "queue": "build"
                }
            }
        }
    ],
    "edges": [
        {
            "source": "n01",
            "target": "n02"
        },
        {
            "source": "n01",
            "target": "n03"
        },
        {
            "source": "n01",
            "target": "n04"
        },
        {
            "source": "n01",
            "target": "n05"
        },
        {
            "source": "n01",
            "target": "n06"
        },
        {
            "source": "n01",
            "target": "n07"
        },
        {
            "source": "n02",
            "target": "n08"
        },
        {
            "source": "n03",
            "target": "n08"
        },
        {
            "source": "n04",
            "target": "n08"
        },
        {
            "source": "n05",
            "target": "n08"
        },
        {
            "source": "n06",
            "target": "n08"
        },
        {
            "source": "n07",
            "target": "n08"
        },
        {
            "source": "n08",
            "target": "n09"
        }
    ]
}



def _seed_quality_tape(db) -> None:
    """幂等建「质量评估-QA」编排模板（存在则 skip；内容变更用 seed 重跑覆盖字段不删步骤）。"""
    from api.models.orchestration import Tape

    TAPE_NAME = "质量评估-QA"
    existing = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
    if existing:
        print(f"[13/13] 种子编排「{TAPE_NAME}」已存在 (skip)")
        return

    tape = Tape(
        id=uuid.uuid4().hex[:32],
        tape_name=TAPE_NAME,
        tape_label="文档构建质量评估（切片/wiki/图数据三维度）",
        tape_descr=(
            "对文档构建产物做质量评估：切片质量(chunk 长度分布/截断/重复)、wiki 质量"
            "(空页/孤立/断链/占位/证据链)、图数据质量(哑节点/边类型/可回溯性)。"
            "输入 bindings：kb_id / doc_ids(逗号分隔,空=整库) / scope(single|multi|all)。"
            "结果落 kb_quality_run + kb_quality_report,供质量评估页面查询。"
        ),
        tape_type="general",
        status="draft",
        nodes=list(QUALITY_TAPE["nodes"]),
        edges=list(QUALITY_TAPE["edges"]),
        exec_params=QUALITY_TAPE.get("exec_params") or {},
        create_user=None,
    )
    db.add(tape)
    db.commit()
    from api.services.orchestration import save_tape_design

    save_tape_design(db, tape.id, QUALITY_TAPE["nodes"], QUALITY_TAPE["edges"], QUALITY_TAPE.get("exec_params") or {})
    print(f"[13/13] 种子编排「{TAPE_NAME}」OK: id={tape.id[:12]}… 节点 {len(QUALITY_TAPE['nodes'])} 边 {len(QUALITY_TAPE['edges'])}（status=draft，页面点执行即可用）")


def _seed_summary_tapes(db) -> None:
    """幂等建三个文档摘要编排模板（存在则 skip；改内容需删 Tape+TapeStep 行重建）。"""
    from api.models.orchestration import Tape

    tapes = [
        ("摘要-Stuff（全文直塞）", "文档摘要（Stuff：全文一次直塞 LLM，适合中小文档）",
         "业界经典模式一：整篇文档拼接后一次性交给 LLM 生成摘要。适合切片总长不超 "
         "上下文窗口的文档；可配 max_chars 控制输入上限。产物=写 kb_document.summary 字段。",
         SUMMARY_STUFF_TAPE),
        ("摘要-Map-Reduce（分块合并）", "文档摘要（Map-Reduce：分块子摘要再合并，适合大文档）",
         "业界经典模式二：按 batch_size 把切片分批，每批 LLM 生成子摘要，最后一批后把全部 "
         "子摘要合并出最终摘要。切片多的大文档用它，每批独立调用避免超上下文。"
         "产物=写 kb_document.summary 字段。",
         SUMMARY_MAPREDUCE_TAPE),
        ("摘要-Refine（增量修订）", "文档摘要（Refine：摘要随批次滚动更新，适合大文档）",
         "业界经典模式三：摘要草稿随每批新内容滚动修订（先基于第一批生成初始摘要，之后每批 "
         "带着已有摘要 + 新内容更新），每批只读一次，输出持续累积。"
         "产物=写 kb_document.summary 字段。",
         SUMMARY_REFINE_TAPE),
    ]
    from api.services.orchestration import save_tape_design

    for i, (name, label, descr, tape_dict) in enumerate(tapes, start=10):
        if name in SKIP_SEED:
            print(f"[seed-tapes] 编排「{name}」属按需应用（apply_code_repo_tape.py），跳过播种")
            continue
        existing = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
        if existing:
            print(f"[{i}/12] 种子编排「{name}」已存在 (skip)")
            continue
        tape = Tape(
            id=uuid.uuid4().hex[:32],
            tape_name=name,
            tape_label=label,
            tape_descr=descr,
            tape_type="summary",  # 2026-10-15 功能标签：摘要（本循环三个均为文档摘要编排）
            status="draft",
            nodes=list(tape_dict["nodes"]),
            edges=list(tape_dict["edges"]),
            exec_params=tape_dict.get("exec_params") or {},
            create_user=None,
        )
        db.add(tape)
        db.commit()
        save_tape_design(db, tape.id, tape_dict["nodes"], tape_dict["edges"], tape_dict.get("exec_params") or {})
        print(f"[{i}/12] 种子编排「{name}」OK: id={tape.id[:12]}… 节点 {len(tape_dict['nodes'])} 边 {len(tape_dict['edges'])}（status=draft，页面点执行即可用）")


def _seed_domain_resolve_tape(db) -> None:
    """幂等建「题材分析-领域判定」编排（存在则 skip；改内容需删 Tape+TapeStep 行重建）。

    用途：知识库/上传均未选构建引擎时，摘要完成后由本编排基于文档摘要 +
    平台已有领域 wiki 构建编排的划分，自动判定使用哪个编排继续构建。
    参数联动：modo_dim(SYSTEM_CONFIG).DOMAIN_RESOLVE_TAPE = 本编排 id（留空
    = 上传链回退原 skill 链构建，不启用自动领域分发）。
    """
    from api.models.orchestration import Tape
    from api.services.orchestration import ensure_tape_category, save_tape_design

    name = "题材分析-领域判定"
    existing = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
    if existing:
        print(f"[seed-resolve] 编排「{name}」已存在 (skip) id={existing.id[:12]}…")
        return
    ensure_tape_category(db, "通用", cat_type="tape")
    tape = Tape(
        id=uuid.uuid4().hex[:32],
        tape_name=name,
        tape_label="题材分析（自动选择领域构建编排）",
        tape_descr=(
            "根据文档摘要与平台已有领域构建编排的划分，自动判定该文档应使用哪个编排"
            "进行后续 wiki 构建。产物：resolved_tape_id（上传链据此启动对应编排）。"
        ),
        tape_type="general",
        category="通用",
        status="draft",
        nodes=list(DOMAIN_RESOLVE_TAPE["nodes"]),
        edges=list(DOMAIN_RESOLVE_TAPE["edges"]),
        exec_params=DOMAIN_RESOLVE_TAPE.get("exec_params") or {},
        create_user=None,
    )
    db.add(tape)
    db.commit()
    save_tape_design(
        db, tape.id,
        DOMAIN_RESOLVE_TAPE["nodes"], DOMAIN_RESOLVE_TAPE["edges"],
        DOMAIN_RESOLVE_TAPE.get("exec_params") or {},
    )
    print(f"[seed-resolve] 编排「{name}」OK: id={tape.id[:12]}… 节点 {len(DOMAIN_RESOLVE_TAPE['nodes'])} 边 {len(DOMAIN_RESOLVE_TAPE['edges'])}（status=draft）")
    print(f"[seed-resolve] 提示：把该 id 写入 modo_dim(SYSTEM_CONFIG).DOMAIN_RESOLVE_TAPE 即启用自动领域分发")


def _restore_tape_steps(db, tape_id: str, steps: list) -> None:
    """按导出的执行层 steps 原样重建 kb_tape_step（不经过画布推导）。

    step id 沿用 save_tape_design 的 "{tape_id[:12]}::{node_id}" 约定；pre/next
    引用按新 tape 前缀重映射（导出值可能带旧前缀或裸 node id，两者都兼容）。
    """
    from api.models.orchestration import TapeStep

    prefix = tape_id[:12]

    def _sid(nid) -> str:
        bare = str(nid or "").rsplit("::", 1)[-1]
        return f"{prefix}::{bare}" if bare else ""

    db.query(TapeStep).filter(TapeStep.tape_id == tape_id).delete()
    db.commit()
    for st in sorted(steps, key=lambda s: int(s.get("step_seq") or 0)):
        sid = _sid(st.get("step_id") or "")
        db.add(
            TapeStep(
                id=sid or uuid.uuid4().hex[:64],
                tape_id=tape_id,
                step_inst=str(st.get("step_inst") or "def"),
                step_label=str(st.get("step_label") or "")[:128],
                step_config=st.get("step_config") or {},
                step_seq=int(st.get("step_seq") or 0),
                pre_step_ids=[x for x in (_sid(i) for i in (st.get("pre_step_ids") or [])) if x],
                next_step_ids=[x for x in (_sid(i) for i in (st.get("next_step_ids") or [])) if x],
                queue_name=str(st.get("queue_name") or ""),
            )
        )
    db.commit()


def _seed_tapes_from_exports(db) -> None:
    """幂等建全部种子编排（数据源 scripts/seed_tape_data/*.json，2026-10-17 由编排管理数据统一固化）。

    每个 JSON 为导出草稿（type=kb.orchestration.draft；draft.tapeName/tapeLabel/
    tapeDescr/tapeType/category/status/nodes/edges/execParams[/steps]）。**该目录
    是编排数据的唯一真源**：由 scripts/export_orchestrations_to_seed.py 从线上
    kb_tape/kb_tape_step 导出，改动线上编排后重跑该脚本即可让种子与线上一致。

    编排集合（2026-10-21 与线上编排管理一致，共 27 条）：21 个领域 wiki 构建 +
    表构建Wiki-基础原子版 + 摘要三模式（Stuff/Map-Reduce/Refine）+ 题材分析-领域判定
    + 质量评估-QA。

    幂等：按 tape_name 判重，已存在则跳过（不改动已有编排，不覆盖线上设计）。
    缺字段兜底：category 按「wiki构建-<领域>」推导、status=draft、tapeType=general。
    draft.steps 存在时按执行层原样重建 kb_tape_step（画布 nodes 与 kb_tape_step
    历史上会脱节——2026-10-15 实测三个摘要编排画布仍是远古版，此路径避免播种时
    把执行层修复覆盖回画布旧版）；否则用 save_tape_design 由画布推导步骤。
    """
    import json
    from pathlib import Path

    from api.models.orchestration import Tape
    from api.services.orchestration import ensure_tape_category, save_tape_design

    data_dir = Path(__file__).resolve().parent / "seed_tape_data"
    if not data_dir.is_dir():
        print("[seed-tapes] 无 seed_tape_data/ 目录，跳过编排种子")
        return
    files = sorted(data_dir.glob("*.json"))
    # 2026-10-18：这些种子文件不参与自动播种。
    #   wiki构建-代码仓库：DB 里没有该编排，文件仅供 scripts/apply_code_repo_tape.py
    #   在需要时手动应用（代码仓库知识库专用），播种会自动建出不该有的空壳编排。
    SKIP_SEED = {"wiki构建-代码仓库"}
    created = skipped = 0
    resolve_tape_id = ""
    for fp in files:
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[seed-tapes] 读取失败 {fp.name}: {e}")
            continue
        d = doc.get("draft") or doc
        name = str(d.get("tapeName") or "").strip()
        if not name:
            print(f"[seed-tapes] 无 tapeName: {fp.name}")
            continue
        existing = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
        if existing:
            skipped += 1
            print(f"[seed-tapes] 编排「{name}」已存在 (skip)")
            continue
        nodes = list(d.get("nodes") or [])
        edges = list(d.get("edges") or [])
        exec_params = d.get("execParams")
        category = str(d.get("category") or "").strip() or (name.replace("wiki构建-", "", 1) or "通用")
        ensure_tape_category(db, category, cat_type="tape")
        tape = Tape(
            id=uuid.uuid4().hex[:32],
            tape_name=name,
            tape_label=str(d.get("tapeLabel") or name),
            tape_descr=str(d.get("tapeDescr") or ""),
            tape_type=str(d.get("tapeType") or "general") or "general",
            category=category,
            status=str(d.get("status") or "draft") or "draft",
            nodes=nodes,
            edges=edges,
            exec_params=exec_params,
            create_user=None,
        )
        db.add(tape)
        db.commit()
        steps = d.get("steps") or []
        if steps:
            _restore_tape_steps(db, tape.id, steps)
        else:
            save_tape_design(db, tape.id, nodes, edges, exec_params)
        created += 1
        if name == "题材分析-领域判定":
            resolve_tape_id = tape.id
        print(
            f"[seed-tapes] 编排「{name}」OK: id={tape.id[:12]}… 分类「{category}」 "
            f"{len(nodes)} 节点 / {len(steps) or len(nodes)} 步骤 status={tape.status}"
        )
    # 题材分析编排建完自动接线：新环境开箱即用「摘要驱动自动领域构建」
    # （线上等价：modo_dim(SYSTEM_CONFIG).DOMAIN_RESOLVE_TAPE = 该编排 id，留空=不启用）
    if resolve_tape_id:
        dim = db.execute(
            select(Dim).where(
                Dim.dim_group == "SYSTEM_CONFIG", Dim.dim_code == "DOMAIN_RESOLVE_TAPE"
            )
        ).scalars().first()
        if dim is not None and not (dim.dim_value or "").strip():
            dim.dim_value = resolve_tape_id
            db.commit()
            print(f"[seed-tapes] 参数 DOMAIN_RESOLVE_TAPE 已指向新编排 {resolve_tape_id[:12]}…（启用自动领域分发）")
        elif dim is None:
            print("[seed-tapes] 参数 DOMAIN_RESOLVE_TAPE 不存在，跳过自动接线（需先播系统参数种子）")
    print(f"[seed-tapes] 完成: 新建 {created} / 跳过 {skipped}")


def _seed_ontology_schemas(db) -> None:
    """幂等建本体 Schema（25 套领域/业务本体，数据 scripts/seed_ontology_data/*.json，2026-10-21 固化）。

    每 JSON 为 schema export（schema_name/label/desc + 4 列 LLM 配置 + items[] 分类行）。
    存在则 skip；KB↔schema 绑定（kb_ontology_schema）按 kb_id 属环境数据不固化，
    新环境在前端「绑定知识库」选择即可。
    """
    import json
    from pathlib import Path

    from api.models.ontology import OntologySchema

    data_dir = Path(__file__).resolve().parent / "seed_ontology_data"
    if not data_dir.is_dir():
        print("[seed-onto] 无 seed_ontology_data/ 目录，跳过本体 Schema 种子")
        return
    files = sorted(data_dir.glob("*.json"))
    created = skipped = 0
    for fp in files:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[seed-onto] 读取失败 {fp.name}: {e}")
            continue
        name = str(d.get("schema_name") or "").strip()
        if not name:
            continue
        exists = db.execute(select(OntologySchema).where(OntologySchema.schema_name == name).limit(1)).scalars().first()
        if exists:
            skipped += 1
            print(f"[seed-onto] Schema「{name}」已存在 (skip)")
            continue
        items = d.get("items") or []
        for it in items:
            dim = str(it.get("dimension") or "")
            if dim not in ("business", "rule"):
                continue
            cat_no = int(it.get("cat_no") or 0)
            cat_name = str(it.get("cat_name") or "").strip()
            if not cat_name:
                continue
            db.add(OntologySchema(
                id=uuid.uuid4().hex[:24],
                schema_name=name,
                schema_label=str(d.get("schema_label") or name),
                schema_desc=d.get("schema_desc"),
                dimension=dim,
                cat_no=cat_no,
                cat_name=cat_name,
                cat_label=str(it.get("cat_label") or f"{cat_no}-{cat_name}"),
                prompt_hint=it.get("prompt_hint"),
                neo4j_edge=it.get("neo4j_edge"),
                expert_role=d.get("expert_role"),
                extract_rules=d.get("extract_rules"),
                summary_config=d.get("summary_config"),
                term_filter_config=d.get("term_filter_config"),
                state=str(it.get("state") or "1") or "1",
                sort=cat_no,
            ))
        db.commit()
        created += 1
        print(f"[seed-onto] Schema「{name}」OK: {len(items)} 分类行")
    print(f"[seed-onto] 完成: 新建 {created} / 跳过 {skipped}")


if __name__ == "__main__":
    main()