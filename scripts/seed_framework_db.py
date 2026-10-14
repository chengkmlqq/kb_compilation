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

from sqlalchemy import delete, select

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
# 2026-10-14 调整（方案J）：新增「新版问答」页面（/chat-next，assistant-ui 试验页，
#   与现有「智能问答」/chat 并存；顶级 sort_num 顺延）
# 结构：Header 顶级 = 知识库管理/文件管理/切片核对/智能问答/智能体配置/联网搜索/
#         数据源/数据查询/运维监控(目录)/配置管理(目录)/系统管理。
#   - route 留空 = 纯目录分组（只展开不跳转）
#   - route 有值且带子级 = 分组兼页面（点击自身跳该路由）
KB_MENUS: list[tuple[str, str, str, str | None, str | None, str, int]] = [
    # ---- 顶级页面 ----
    ("chat", "chat", "智能问答", "/chat", None, "CommentOutlined", 1),
    ("chat_next", "chat_next", "新版问答", "/chat-next", None, "MessageOutlined", 2),
    ("kbs", "kbs", "知识库", "/kbs", None, "AppstoreOutlined", 3),
    ("agents", "agents", "智能体", "/agents", None, "RobotOutlined", 4),
    # ---- 运维监控（顶级目录：任务监控/任务管理/主机监控/操作日志/切片核对/元数据采集） ----
    ("grp_ops", "grp_ops", "运维监控", None, None, "FundOutlined", 5),
    ("jobs", "jobs", "任务监控", "/jobs", "grp_ops", "DashboardOutlined", 1),
    ("4cd17410ab29495aa131cdf763fc3549", "cron", "任务管理", "/cron", "grp_ops", "ScheduleOutlined", 2),
    ("b2d585de4d824b96bfed2a7798ad6880", "workers", "主机监控", "/workers", "grp_ops", "CloudServerOutlined", 3),
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
    # ---- 配置管理（顶级目录：数据源管理/模型/MCP/技能/参数/联网搜索） ----
    ("grp_config", "grp_config", "配置管理", None, None, "ToolOutlined", 6),
    ("datasources", "datasources", "数据源管理", "/datasources", "grp_config", "DatabaseOutlined", 1),
    ("models", "models", "模型配置", "/models", "grp_config", "CloudServerOutlined", 2),
    ("8829900dd6be4c45bc584df804ba0d4a", "mcps", "MCP 管理", "/mcps", "grp_config", "ApiOutlined", 3),
    ("256b44596e6e43f5a847e0c3ae7b2ba0", "skills", "技能管理", "/skills", "grp_config", "ToolOutlined", 4),
    ("sys_dims", "sys_dims", "参数管理", "/system/dims", "grp_config", "DatabaseOutlined", 5),
    ("d83aee0d1d8a4fce8a6bd121b6efc5fa", "websearch", "联网搜索", "/websearch", "grp_config", "SearchOutlined", 6),
    # ---- 系统管理（分组兼页面，顶级：点击自身跳 /system → 重定向到 /system/users） ----
    ("system", "system", "系统管理", "/system", None, "SettingOutlined", 7),
    ("sys_users", "sys_users", "用户管理", "/system/users", "system", "TeamOutlined", 1),
    ("sys_roles", "sys_roles", "角色管理", "/system/roles", "system", "SafetyOutlined", 2),
    ("sys_teams", "sys_teams", "团队管理", "/system/teams", "system", "PartitionOutlined", 3),
    ("sys_menus", "sys_menus", "菜单管理", "/system/menus", "system", "MenuOutlined", 4),
    # ---- 调试工具（顶级目录：本体Schema/编排管理/编排模板/编排组件/文件管理/数据查询） ----
    ("grp_debug", "grp_debug", "调试工具", None, None, "BugOutlined", 8),
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
    ("orch_tapes", "orch_tapes", "编排管理", "/orchestrations", "grp_debug", "ApartmentOutlined", 3),
    ("orch_templates", "orch_templates", "编排模板", "/tape-templates", "grp_debug", "AppstoreOutlined", 4),
    ("orch_defines", "orch_defines", "编排组件", "/orchestrations/steps", "grp_debug", "BlockOutlined", 5),
    ("ac9b271c90b6450c92bd14e9681da520", "files", "文件管理", "/files", "grp_debug", "FileOutlined", 6),
    ("datagrid", "datagrid", "数据查询", "/datagrid", "grp_debug", "TableOutlined", 7),
    ("quality", "quality", "质量评估", "/quality", "grp_debug", "CheckCircleOutlined", 8),
]

# 除种子角色外，这些角色（若存在）同样授权全量 KB 菜单，
# 保证管理员/普通用户登录后侧栏都是完整菜单。
SEED_GRANT_ROLES = (SEED_ROLE, "plat-mgr", "normal_user")

# 系统参数种子（modo_dim, dim_group=SYSTEM_CONFIG）
# 2026-10-12: 参数表清理后仅保留代码确实读取的 9 个参数，此处固化进种子——
#   全新部署跑本脚本即建好这 9 行，已部署环境已有则跳过（不覆盖已有配置值）。
# dim_value 默认留空 = 走容器 env / 代码默认（与线上实际生效路径一致）；
# 敏感字段（*_API_KEY）不写默认值。seq 与已部署库一致，保证「参数管理」列表顺序稳定。
# (dim_code, dim_value, dim_desc, seq)
SYSTEM_CONFIG_DIMS: list[tuple[str, str, str, int]] = [
    ("EMBEDDING_BASE_URL", "", "向量模型端点（OpenAI 兼容 /v1；留空=容器 env EMBEDDING_BASE_URL）", 0),
    ("EMBEDDING_API_KEY", "", "向量模型密钥（留空=容器 env EMBEDDING_API_KEY）", 1),
    ("EMBEDDING_MODEL", "", "向量模型，如 BAAI/bge-m3（留空=容器 env EMBEDDING_MODEL）", 2),
    ("EMBEDDING_DIM", "", "向量维度，如 1024（留空=容器 env EMBEDDING_DIM）", 3),
    ("ai_chat_api_endpoint", "", "问答模型端点（OpenAI 兼容 /v1；留空=容器 env AI_CHAT_API_ENDPOINT）", 4),
    ("ai_chat_api_key", "", "问答模型密钥（留空=容器 env AI_CHAT_API_KEY）", 5),
    ("ai_chat_model", "", "问答模型名，如 deepseek-v4-pro（留空=容器 env AI_CHAT_MODEL）", 6),
    ("metadata_full_collection_enabled", "true", "元数据全量采集开关（true/false）", 9),
    ("dify_chat_api_endpoint", "", "兼容端点回退（问答/向量的兜底项；留空=不使用）", 37),
    # 2026-10-13 上传链摘要：三个标准摘要编排的 tape_id（Stuff/Map-Reduce/Refine）。
    # 留空 = 上传不触发摘要（即使 KB summary_strategy=auto 也静默跳过）；
    # 写入后 KB 上传按策略自动路由。运维换编排（自建复制）改这里即可，无需改代码。
    ("SUMMARY_TAPE_STUFF", "", "上传摘要编排-Stuff全文直塞（kb_tape.id，留空=不触发）", 38),
    ("SUMMARY_TAPE_MAPREDUCE", "", "上传摘要编排-Map-Reduce分块合并（kb_tape.id，留空=不触发）", 39),
    ("SUMMARY_TAPE_REFINE", "", "上传摘要编排-Refine增量修订（kb_tape.id，留空=不触发）", 40),
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
    """
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

        # 6b) 系统参数种子（modo_dim SYSTEM_CONFIG，幂等：缺失才建，不覆盖已有值）
        dims_created = _seed_system_config_dims(db)
        print(
            f"[6b/7] 系统参数种子 OK: {len(SYSTEM_CONFIG_DIMS)} 个 "
            f"[新建 {dims_created} / 已有跳过 {len(SYSTEM_CONFIG_DIMS) - dims_created}]"
        )

        # 7) 编排内置组件定义（幂等 upsert）
        d_created, d_updated = _seed_step_defines(db)
        print(f"[7/7] 编排组件定义种子 OK: 内置 {len(ORCH_STEP_DEFINES)} 个 [新建 {d_created} / 修正 {d_updated}]")

        # 8) 种子编排模板：wiki 构建（细粒度基础原子组合，2026-10-12）
        #    幂等：按 tape_name 查存在则 skip。编排自己实现 wiki 构建逻辑
        #    （读切片→LLM→解析→摘要页→实体页循环→互链），节点全是可复用基础原子。
        _seed_wiki_build_tape(db)

        # 9) 种子编排模板：表→wiki 构建（2026-10-12）
        #    幂等：按 tape_name 查存在则 skip。编排自己实现表 wiki 构建逻辑
        #    （kb_read_table 取数→script 构 md→LLM 表用途→script 拼正文→
        #    wiki_upsert_page 写表主页→wiki_rebuild_links），节点全是可复用基础原子。
        _seed_table_wiki_tape(db)

        # 10-12) 种子编排模板：文档摘要三模式（Stuff / Map-Reduce / Refine，2026-10-12）
        #    业界经典摘要模式，全部用基础原子组装（读切片→分批→LLM→合并/修订→写摘要页）。
        _seed_summary_tapes(db)

        # 13) 种子编排模板：质量评估（2026-10-13）
        #    文档构建质量评估：切片/wiki/图数据三维度检查 + 规则归因汇总落库。
        _seed_quality_tape(db)

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
        tape_type="general",
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
        tape_type="general",
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
# 业界大文档摘要三种经典模式，全部用细粒度基础原子组装：
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
                                "temperature": "0.3", "max_tokens": "1024"}},
        },
        {
            "id": "n4", "type": "custom",
            "data": {"label": "落页准备", "stepInst": "script",
                     "config": {"code": (
                         "import hashlib\n"
                         "s = str(bindings.get('summary_text') or '').strip()\n"
                         "doc_id = str(bindings.get('document_id') or '')\n"
                         "bindings['final_summary'] = s\n"
                         "bindings['page_title'] = '文档摘要（Stuff 全文直塞）' if s else ''\n"
                         "bindings['page_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10] + '-stuff'\n"
                         "bindings['page_content'] = ('# 文档摘要（Stuff 全文直塞）\\n\\n' + s) if s else ''\n"
                     )}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "写摘要页", "stepInst": "wiki_upsert_page",
                     "config": {"kb_id": "{{kb_id}}", "title": "{{page_title}}",
                                "content": "{{page_content}}", "slug": "{{page_slug}}",
                                "page_type": "summary", "skip_empty": True}},
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
                         "        '（150~250 字，先一句话概括主题，再分点列出核心要点，只输出摘要正文）：\\n\\n' + body)\n"
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
                                "temperature": "0.3", "max_tokens": "1024", "skip_empty": True}},
        },
        {
            "id": "n8", "type": "custom",
            "data": {"label": "落页准备", "stepInst": "script",
                     "config": {"code": (
                         "import hashlib\n"
                         "if bindings.get('merge_ready'):\n"
                         "    doc_id = str(bindings.get('document_id') or '')\n"
                         "    s = str(bindings.get('merged_summary') or '').strip()\n"
                         "    bindings['final_summary'] = s\n"
                         "    bindings['page_title'] = '文档摘要（Map-Reduce 分块合并）' if s else ''\n"
                         "    bindings['page_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10] + '-mapreduce'\n"
                         "    bindings['page_content'] = ('# 文档摘要（Map-Reduce 分块合并）\\n\\n' + s) if s else ''\n"
                         "else:\n"
                         "    bindings['page_title'] = ''\n"
                         "    bindings['page_slug'] = ''\n"
                         "    bindings['page_content'] = ''\n"
                     )}},
        },
        {
            "id": "n9", "type": "custom",
            "data": {"label": "写摘要页", "stepInst": "wiki_upsert_page",
                     "config": {"kb_id": "{{kb_id}}", "title": "{{page_title}}",
                                "content": "{{page_content}}", "slug": "{{page_slug}}",
                                "page_type": "summary", "skip_empty": True}},
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
                         "        '（150~250 字，保留已有要点并融入新要点，只输出摘要正文）：')\n"
                         "else:\n"
                         "    bindings['refine_prompt'] = (\n"
                         "        '根据以下文档内容生成中文摘要（150~250 字，先一句话概括主题，'\n"
                         "        '再分点列出核心要点，只输出摘要正文）：\\n\\n' + cur)\n"
                     )}},
        },
        {
            "id": "n5", "type": "custom",
            "data": {"label": "LLM更新摘要", "stepInst": "kb_llm_chat",
                     "config": {"prompt": "{{refine_prompt}}", "output_var": "new_summary",
                                "temperature": "0.3", "max_tokens": "1024"}},
        },
        {
            "id": "n6", "type": "custom",
            "data": {"label": "回写+落页准备", "stepInst": "script",
                     "config": {"code": (
                         "import hashlib\n"
                         "s = str(bindings.get('new_summary') or '').strip()\n"
                         "bindings['running_summary'] = s\n"
                         "if bindings.get('is_last') and s:\n"
                         "    doc_id = str(bindings.get('document_id') or '')\n"
                         "    bindings['final_summary'] = s\n"
                         "    bindings['page_title'] = '文档摘要（Refine 增量修订）'\n"
                         "    bindings['page_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10] + '-refine'\n"
                         "    bindings['page_content'] = '# 文档摘要（Refine 增量修订）\\n\\n' + s\n"
                         "else:\n"
                         "    bindings['final_summary'] = s\n"
                         "    bindings['page_title'] = ''\n"
                         "    bindings['page_slug'] = ''\n"
                         "    bindings['page_content'] = ''\n"
                     )}},
        },
        {
            "id": "n7", "type": "custom",
            "data": {"label": "写摘要页", "stepInst": "wiki_upsert_page",
                     "config": {"kb_id": "{{kb_id}}", "title": "{{page_title}}",
                                "content": "{{page_content}}", "slug": "{{page_slug}}",
                                "page_type": "summary", "skip_empty": True}},
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


# ── 质量评估编排（2026-10-13 v1）──────────────────────────────
# 文档构建质量评估：切片/wiki/图数据三维度检查（并行）+ 规则归因汇总落库。
# 脚本节点 import worker/builtin_engine/code_rev_src 下的 quality_*.py。
QUALITY_TAPE: dict = {
    "exec_params": {"kb_id": "", "doc_ids": "", "scope": "all"},
    "nodes": [
        {
            "id": "n01", "type": "custom",
            "data": {"label": "输入定义", "stepInst": "def",
                     "config": {"queue": "build", "assignments": (
                         "[{\"variable\": \"kb_id\", \"expression\": \"bindings.get('kb_id', '')\"},"
                         "{\"variable\": \"doc_ids\", \"expression\": \"bindings.get('doc_ids', '')\"},"
                         "{\"variable\": \"scope\", \"expression\": \"bindings.get('scope', 'all')\"}]"
                     )}},
        },
        {
            "id": "n02", "type": "custom",
            "data": {"label": "切片质量检查", "stepInst": "script",
                     "config": {"queue": "build", "code": (
                         "import sys, os, json\n"
                         "sys.path.insert(0, '/srv/kb/worker/builtin_engine/code_rev_src')\n"
                         "import quality_chunk\n"
                         "_kb = bindings.get('kb_id') or ''\n"
                         "_docs = [d.strip() for d in str(bindings.get('doc_ids') or '').split(',') if d.strip()] or None\n"
                         "r = quality_chunk.run(_kb, _docs)\n"
                         "bindings['chunk_r'] = r\n"
                         "print(f\"[n02] 切片质量: 健康分 {r['metrics'].get('health_score')} 问题 {len(r['issues'])}\")\n"
                     )}},
        },
        {
            "id": "n03", "type": "custom",
            "data": {"label": "Wiki质量检查", "stepInst": "script",
                     "config": {"queue": "build", "code": (
                         "import sys, os, json\n"
                         "sys.path.insert(0, '/srv/kb/worker/builtin_engine/code_rev_src')\n"
                         "import quality_wiki\n"
                         "_kb = bindings.get('kb_id') or ''\n"
                         "r = quality_wiki.run(_kb)\n"
                         "bindings['wiki_r'] = r\n"
                         "print(f\"[n03] Wiki质量: 健康分 {r['metrics'].get('health_score')} 问题 {len(r['issues'])}\")\n"
                     )}},
        },
        {
            "id": "n04", "type": "custom",
            "data": {"label": "图数据质量检查", "stepInst": "script",
                     "config": {"queue": "build", "code": (
                         "import sys, os, json\n"
                         "sys.path.insert(0, '/srv/kb/worker/builtin_engine/code_rev_src')\n"
                         "import quality_graph\n"
                         "_kb = bindings.get('kb_id') or ''\n"
                         "r = quality_graph.run(_kb)\n"
                         "bindings['graph_r'] = r\n"
                         "print(f\"[n04] 图数据质量: 健康分 {r['metrics'].get('health_score')} 问题 {len(r['issues'])}\")\n"
                     )}},
        },
        {
            "id": "n05", "type": "custom",
            "data": {"label": "汇总+落库", "stepInst": "script",
                     "config": {"queue": "build", "code": (
                         "import sys, os, json\n"
                         "sys.path.insert(0, '/srv/kb/worker/builtin_engine/code_rev_src')\n"
                         "import quality_summary\n"
                         "_kb = bindings.get('kb_id') or ''\n"
                         "_docs = [d.strip() for d in str(bindings.get('doc_ids') or '').split(',') if d.strip()] or None\n"
                         "_scope = str(bindings.get('scope') or 'all')\n"
                         "r = quality_summary.run(_kb, '', _scope, _docs,\n"
                         "                          bindings.get('chunk_r'), bindings.get('wiki_r'), bindings.get('graph_r'))\n"
                         "bindings['quality_summary'] = r\n"
                         "print(f\"[n05] {r.get('summary', '')}\")\n"
                     )}},
        },
        {
            "id": "n06", "type": "custom",
            "data": {"label": "完成汇总", "stepInst": "print",
                     "config": {"text": "{{quality_summary}}", "queue": "build"}},
        },
    ],
    "edges": [
        {"source": "n01", "target": "n02"},
        {"source": "n01", "target": "n03"},
        {"source": "n01", "target": "n04"},
        {"source": "n02", "target": "n05"},
        {"source": "n03", "target": "n05"},
        {"source": "n04", "target": "n05"},
        {"source": "n05", "target": "n06"},
    ],
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
         "上下文窗口的文档；可配 max_chars 控制输入上限。产物=wiki 摘要页 + bindings.final_summary。",
         SUMMARY_STUFF_TAPE),
        ("摘要-Map-Reduce（分块合并）", "文档摘要（Map-Reduce：分块子摘要再合并，适合大文档）",
         "业界经典模式二：按 batch_size 把切片分批，每批 LLM 生成子摘要，最后一批后把全部 "
         "子摘要合并出最终摘要。切片多的大文档用它，每批独立调用避免超上下文。"
         "产物=wiki 摘要页 + bindings.final_summary。",
         SUMMARY_MAPREDUCE_TAPE),
        ("摘要-Refine（增量修订）", "文档摘要（Refine：摘要随批次滚动更新，适合大文档）",
         "业界经典模式三：摘要草稿随每批新内容滚动修订（先基于第一批生成初始摘要，之后每批 "
         "带着已有摘要 + 新内容更新），每批只读一次，输出持续累积。"
         "产物=wiki 摘要页 + bindings.final_summary。",
         SUMMARY_REFINE_TAPE),
    ]
    from api.services.orchestration import save_tape_design

    for i, (name, label, descr, tape_dict) in enumerate(tapes, start=10):
        existing = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
        if existing:
            print(f"[{i}/12] 种子编排「{name}」已存在 (skip)")
            continue
        tape = Tape(
            id=uuid.uuid4().hex[:32],
            tape_name=name,
            tape_label=label,
            tape_descr=descr,
            tape_type="general",
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


if __name__ == "__main__":
    main()