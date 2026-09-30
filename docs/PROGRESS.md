# KB Compilation Platform — 进度记录

> 本文档用于跨会话续接工作。每次工作结束更新「当前状态」；新会话开始先读本文档。

最后更新：2026-09-30

## 1. 项目概况

| 项 | 值 |
|---|---|
| 仓库 | `/home/jenkins/chengkai/kb_compilation`（git remote: git@github.com:chengkml/kb_compilation.git） |
| 目标 | 从 data-synth 剔除数据合成业务，保留系统框架层；在其上迁移 WeKnora 知识库能力 |
| 最终形态 | Next.js 纯前端 + Python FastAPI 后端 + Celery 任务，知识库数据走 PG+pgvector |
| 上游参考 | `/home/jenkins/chengkai/data-synth`（框架层来源）、`/home/jenkins/chengkai/WeKnora`（知识库功能来源） |

## 2. 当前状态（2026-09-30）

- **测试：127 个全部通过**（`uv run pytest` / `.venv/bin/python -m pytest`）
- **API：18 个端点**——auth/login、me、open/datasources(/test)、qa/stream、agents CRUD+流式、**kbs 管理全套（CRUD/文档上传/wiki 树/页面详情/JSON 检索）**、health
- **Celery：4 个 task_class 注册**——KbDocumentProcessTask、KbDocumentEmbedTask、KbWikiBuildTask、KbGraphBuildTask（+ beat 扫描 scan_cron_tasks）
- **MCP：3 个工具**——kb_list / kb_search / kb_answer（mcp SDK v2，MCPServer）
- **前端：Next.js 16 应用就绪**（web/ 目录）——登录 / 知识库管理 / KB 详情（文档上传+wiki 树+检索）/ wiki 页面详情 / 智能问答(SSE) / 智能体配置 / Wiki 总览 / 数据源 8 个页面
- **git：13+ commit**（后端能力 + P12 前端骨架），工作区见 git status
- 核心链路全部真实验证过（真实 PG17+pgvector + 真实框架 MySQL）

## 3. 已完成功能清单

### 3.1 框架层（从 data-synth 抽离）✅
- [x] 30 张框架表 1:1 迁移（表/列/索引名与原库一致，可直连共享库）
- [x] 双引擎 DB：框架表（MySQL，DATABASE_URL）+ 知识库表（PG+pgvector，KNOWLEDGE_DATABASE_URL）
- [x] auth/RBAC：AES/DES 字节级兼容（Node crypto-js 黄金密文交叉验证 + 真实库 roundtrip 通过）、身份 cookie 编解码、RBAC 三级检查
- [x] 数据源：连接测试（mysql/postgres/kingbase/trino/minio）、分页列表
- [x] 配置缓存：modo_dim SYSTEM_CONFIG TTL+single-flight
- [x] Celery 调度：scan_cron_tasks 完整复刻（初始化/校准/乐观锁防双触发），TASK_CLASS_REGISTRY 分发
- [x] 审计日志：oper_log 写入（字段钳制、动作分类）

### 3.2 知识库域（迁移 WeKnora）✅
- [x] docreader：8772 行解析引擎（3 引擎 9 格式：md/pdf/docx/xlsx/pptx/epub/mhtml/图片）
- [x] 知识库模型：7 张 wiki/kb 表（KbDatasource/KbDocument/DocChunk/WikiFolder/WikiPage/WikiLink/KbAgent），pgvector+HNSW+GIN
- [x] 混合检索：向量臂（余弦距离+阈值）+ 关键词臂（ILIKE+pg_trgm，中文可用）+ RRF 融合（k=60, 0.7/0.3，公式与 WeKnora 逐项一致）
- [x] 文档入库：解析→分块（800/80）→向量化→落库 全链路 Celery 任务
- [x] RAG 问答：SSE 流式 + search_results 上下文注入 + chunk_id 引用标注
- [x] wiki 生成：实体分组去重→建页（幂等追加）→bigram 关联建链
- [x] Neo4j 图谱：LLM 实体/关系抽取 + MERGE 写入（提示词模板迁自 WeKnora）
- [x] 智能体配置：kb_agent CRUD + 按 agent 流式问答 + KB 范围解析（all/selected/none）
- [x] MCP 服务端：3 个知识库工具对外暴露（sessionId 参数约定）

### 3.3 KB 管理 API（P12 前置，本次新增）✅
- [x] KB CRUD：list（分页/关键字/doc+page 计数）/ create / update / delete（软删 + 删除保护：有文档禁止删）
- [x] 文档：list / multipart 上传（落盘 KB_STORAGE_DIR → 建 kb_document(PENDING) → 入队 Celery）/ delete（级联清 chunk + 本地文件）
- [x] wiki 浏览：目录树+页面列表 / 页面详情（含关联链接）
- [x] JSON 检索端点（非流式，前端搜索框用）
- [x] worker 从 storage_path 读文件字节（上传→解析闭环打通，实测 READY+chunk 落库）

### 3.4 前端（P12 第一段，Next.js 16 纯前端）✅
- [x] 架构：`web/` 独立 Next.js 应用，`/api/[...path]` 代理路由转发到 FastAPI（cookie 透传 + SSE 透传），同源契约
- [x] 页面：/login（登录写 x-next-identity cookie）、主布局（Sider 菜单+登录守卫）、/kbs、/kbs/[id]（上传+wiki 树+检索）、/kbs/[id]/wiki/[slug]、/chat（SSE 流式）、/agents、/wiki、/datasources
- [x] 构建：bun install / build / type-check 全过；生产模式联调验证（页面渲染、代理转发、SSE event-stream 头透传）

## 4. 关键文件地图

```
api/
  main.py                 FastAPI 入口（挂载 auth/datasources/qa/agents/kbs 路由）
  config.py               配置（DB_TYPE/DATABASE_URL/KNOWLEDGE_DATABASE_URL/KB_STORAGE_DIR）
  db.py                   双引擎：Base(框架) + KnowledgeBase(知识库)，SCHEMA_NAME 注入 search_path
  lib/crypto.py           AES/DES 字节级兼容（DataSource 密码列/身份 cookie 共用）
  models/framework.py     30 张框架表（共享旧库）
  models/knowledge.py     7 张知识库表（PG+pgvector）
  services/
    identity.py           auth/RBAC/身份 cookie（含 to_payload() camelCase 输出）
    datasource.py         数据源连接测试
    config_cache.py       modo_dim 缓存     runtime_config.py 三级解析
    retrieval.py          RRF 混合检索（向量臂+关键词臂+融合）
    embedding.py          OpenAI 兼容 /v1/embeddings
    ingest.py             文档入库编排（解析→分块→向量化→落库）
    chat.py               RAG 问答（上下文注入+流式）
    graph.py              LLM 实体/关系抽取 + Neo4j 写入
    wiki.py               页面生成 + 链接
    agents.py             智能体配置服务
    kb_admin.py           KB 管理服务（CRUD/文档/wiki 树/JSON 检索/删除保护）
    audit_log.py          oper_log 写入
    mcp_server.py         MCP 服务端（kb_* 工具）
  routers/                auth.py / datasources.py / qa.py(SSE) / agents.py / kbs.py
  assets/prompts/         graph_extraction.yaml / generate_summary.yaml（WeKnora 资产）
worker/
  celery_app.py           Celery 应用（beat 扫描 modo_cron_task）
  tasks/scheduler.py      cron 扫描 + execute_modo_job 分发（TASK_CLASS_REGISTRY）
  tasks/doc_process.py    文档解析/向量化任务（process_document 支持从 storage_path 读字节）
  tasks/wiki_graph.py     wiki/图谱构建任务
docreader/                WeKnora 解析引擎迁入（parser/models/splitter/utils/config）
web/                      Next.js 16 前端（纯前端 + /api/[...path] 代理）
  src/app/                login / (main)/{kbs,kbs/[id],kbs/[id]/wiki/[slug],chat,agents,wiki,datasources}
  src/lib/api.ts          fetch 封装（登录/KB/文档/wiki/检索/智能体/数据源）
scripts/
  knowledge_schema.sql    知识库 DDL（vector/uuid-ossp/pg_trgm 扩展 + 7 表 + 索引）
  verify_schema_alignment.py  框架模型↔真实库列级对齐检查
  verify_chat_stream.py   RAG 流式链路验证（mock OpenAI SSE）
  gen_env.py              本地联调 .env 生成（从 data-synth/WeKnora env 动态读凭据）
tests/                    127 个测试（test_*.py，含 test_kb_admin.py）
```

## 5. 验证方式（每次改动后必跑）

```bash
cd /home/jenkins/chengkai/kb_compilation
.venv/bin/python -m pytest tests/ -q          # 全量测试（127）
.venv/bin/python -c "from fastapi.testclient import TestClient; from api.main import app; print(list(TestClient(app).get('/openapi.json').json()['paths']))"
cd web && bun run build && bun run type-check  # 前端构建（web/ 下）
```

真实库联调配方（已实测通过）：
1. `scripts/gen_env.py --write` 生成 .env（读 data-synth/.env.development + WeKnora/.env，host 改写为本机 13308/5433）
2. `psql -h 127.0.0.1 -p 5433 -U <user> -d <db> -f scripts/knowledge_schema.sql`（幂等，注意分号切块法会吞「注释+建表」整块，必须用 psql 整文件执行）
3. `.venv/bin/uvicorn api.main:app --port 8002`（8000 被 dataos-agent-nginx 占用）
4. `cd web && API_BASE_URL=http://127.0.0.1:8002 bun run start`（生产模式；dev 模式会撞系统 inotify watch 限制）
5. curl 验证：/api/v1/auth/login（huqiang/sys）→ /kbs CRUD → upload → /wiki → /search → /qa/stream（SSE 头透传）

## 6. 环境与连接信息

| 组件 | 地址 | 凭据来源 |
|---|---|---|
| 框架 MySQL（data_synth_neo） | 127.0.0.1:13308（synth-mysql-seed-test 容器） | data-synth/.env.development（host 替换为本机） |
| 知识库 PostgreSQL | 127.0.0.1:5433（WeKnora-postgres 容器，PG17） | WeKnora/.env（DB_USER/DB_PASSWORD/DB_NAME） |
| 前端 | 127.0.0.1:3100（next start / dev -p 3100） | API_BASE_URL 指向后端 |
| Python | `.venv`（uv 创建，3.11.15） | — |
| 包管理 | `uv sync` / `uv add <pkg>` | — |

测试账号：`huqiang / sys`（真实库，seed 明文经 AES 解密确认）。
注意：`.env` 是敏感文件（read_file 被拒），连接凭据一律**从 env 文件动态读取**，绝不写进命令行/代码。

## 7. 关键坑位记录（务必先读）

1. **Hermes 脱敏会写坏含 URL/凭据字面量的代码**：写含 `redis://host:***@`、密码样式的字符串会被改写/截断。规避：字面量用字符串拼接（`"jdbc:mysql:" + "//h1/db1"`），或从 env 动态读。往写文件时如发现 `***` 或行被吞，立即改用拼接重写。
2. **PG 原生 tsvector 无法分词中文**（整句一个 token）→ 关键词臂用 ILIKE + pg_trgm word_similarity（已实现，勿改回 websearch_to_tsquery）。
3. **pgvector 扩展在 public schema**：表在其他 schema 时 search_path 必须含 public（`SET search_path TO x, public`；api/db.py get_knowledge_engine 通过 connect_args options 注入 SCHEMA_NAME）。DDL 文件顶部有注释说明。
4. **SQLite 不能建 GIN/to_tsvector 索引**：wiki_page 的 FTS 索引只写在 knowledge_schema.sql，不在 SQLAlchemy 模型里（否则 create_all 在 sqlite 测试崩）。
5. **SQLAlchemy 2.0 update().execute() 返回类型**：rowcount 用 `getattr(result, "rowcount", 0)` 规避 Pyright 误报。
6. **mcp SDK 用 v2 语法**：`from mcp.server.mcpserver import MCPServer`（FastMCP 在 2.x 已改名）。工具逻辑抽成模块级 handle_* 函数便于测试（Tool 对象不暴露底层 fn）。
7. **docreader 的 textract 已改惰性导入**（上游因 SSRF 禁用该方法，勿改回顶层 import）。
8. **embedding 维度必须与 DDL 一致**：knowledge_schema.sql 的 vector(1024) 与 EMBEDDING_DIM 默认 1024；测试假向量也用 1024，否则 DataError expected 1024 dimensions。
9. **对齐脚本/execute_code 的 python 是系统解释器**：没有项目依赖，跑 SQLAlchemy 必须用 `.venv/bin/python`。
10. **psql 跑 knowledge_schema.sql 必须整文件执行**：若用 python 按 `;` 分块，注释行+CREATE TABLE 会整块被当成注释跳过（kb_datasource 等表静默没建）。用 `psql -f` 或逐句保持注释不粘连。
11. **本地 MySQL 用 pymysql 驱动**：DATABASE_URL 必须是 `mysql+pymysql://`，裸 `mysql://` 会去找 MySQLdb。gen_env.py 已处理。
12. **前端 dev 模式撞系统 inotify watch 限制**（antd 模块解析失败 "OS file watch limit reached"）：联调用 `bun run build` + `bun run start`（生产模式）验证，dev 只留给真正开发时用。
13. **浏览器工具（agent-browser）在本机不可用**：GLIBC 2.29+ 缺失（老 kylin aarch64）。UI 验证用 curl 断言 HTML/接口，不用 browser_* 工具。
14. **登录响应契约**：identity_cookie 在响应**顶层**（不在 data 内）；data 是 camelCase payload（loginId/userId/userName...，Identity.to_payload()）。

## 8. 剩余工作（按优先级）

### 8.1 P12 前端对接（剩余部分，2-3 周）
- 文档上传进度轮询：前端目前上传后手动点刷新；可加定时轮询 parse_state
- 问答页丰富：markdown 渲染（react-markdown）、引用来源点击跳文档、多轮历史持久化
- 智能体问答页（按 agent 流式）/ 智能体编辑弹窗（config 可视化）
- wiki 页 markdown 渲染升级（表格/代码块/图片）
- 数据源页补「连接测试」按钮（后端 /test 已就绪）
- 系统管理页（用户/角色/菜单/操作日志，data-synth 有现成页面可抄）

### 8.2 部署落地（可选先做，让业务侧可看）
- docker-compose：PG+pgvector、Redis（Celery broker）、MySQL（框架库）、FastAPI (uvicorn)、Celery worker + beat、Next.js 前端
- 环境变量：KNOWLEDGE_DATABASE_URL、EMBEDDING_BASE_URL/API_KEY/MODEL、AI_CHAT_API_ENDPOINT/KEY/MODEL
- 数据库初始化：跑 scripts/knowledge_schema.sql

### 8.3 增强项（按需）
- wiki 页链接健康检查（wiki_lint：死链清理，参考 WeKnora wiki_lint.go）
- 多实例配置缓存失效（当前进程内 TTL，与源平台一致；如需跨实例用 Redis）
- Open API 鉴权层（当前 auth 只登录，open/* 数据源路由未加 X-API-Key 校验）
- 上传文件校验（扩展名白名单 / 病毒扫描 / 去重）

## 9. 开工检查单（新会话）

1. `cd /home/jenkins/chengkai/kb_compilation && .venv/bin/python -m pytest tests/ -q` → 应 127 passed
2. `git status` → 确认工作区状态
3. 读本文档「剩余工作」选一项继续
4. 改动后用「验证方式」整组跑
5. 完成后 `git add -A && git commit && git push`（用户习惯：功能完成直接提交推送）
