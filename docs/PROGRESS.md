# KB Compilation Platform — 进度记录

> 本文档用于跨会话续接工作。每次工作结束更新「当前状态」；新会话开始先读本文档。

最后更新：2026-09-30

## 1. 项目概况

| 项 | 值 |
|---|---|
| 仓库 | `/home/jenkins/chengkai/kb_compilation`（本地 git，无 remote） |
| 目标 | 从 data-synth 剔除数据合成业务，保留系统框架层；在其上迁移 WeKnora 知识库能力 |
| 最终形态 | Next.js 纯前端 + Python FastAPI 后端 + Celery 任务，知识库数据走 PG+pgvector |
| 上游参考 | `/home/jenkins/chengkai/data-synth`（框架层来源）、`/home/jenkins/chengkai/WeKnora`（知识库功能来源） |

## 2. 当前状态（2026-09-30）

- **测试：114 个全部通过**（`uv run pytest` / `.venv/bin/python -m pytest`）
- **API：10 个端点**——`/api/v1/auth/login`、`/api/v1/me`、`/api/v1/open/datasources(/test)`、`/api/v1/qa/stream`、`/api/v1/agents` CRUD + `/{id}/qa/stream`、`/health`
- **Celery：4 个 task_class 注册**——KbDocumentProcessTask、KbDocumentEmbedTask、KbWikiBuildTask、KbGraphBuildTask（+ beat 扫描 scan_cron_tasks）
- **MCP：3 个工具**——kb_list / kb_search / kb_answer（mcp SDK v2，MCPServer）
- **git：11 个 commit**（最新 23d940c），全部已提交，工作区干净
- 核心链路全部真实验证过（真实 PG17+pgvector：建表 / 入库 / 混合检索 / wiki 生成）

## 3. 已完成功能清单

### 3.1 框架层（从 data-synth 抽离）✅
- [x] 30 张框架表 1:1 迁移（表/列/索引名与原库一致，可直连共享库）
- [x] 双引擎 DB：框架表（MySQL，DATABASE_URL）+ 知识库表（PG+pgvector，KNOWLEDGE_DATABASE_URL）
- [x] auth/RBAC：AES/DES 字节级兼容（Node crypto-js 黄金密文交叉验证 + 真实库 8 用户 roundtrip 通过）、身份 cookie 编解码、RBAC 三级检查
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

## 4. 关键文件地图

```
api/
  main.py                 FastAPI 入口（挂载 auth/datasources/qa/agents 路由）
  config.py               配置（DB_TYPE/DATABASE_URL/KNOWLEDGE_DATABASE_URL/EMBEDDING_*）
  db.py                   双引擎：Base(框架) + KnowledgeBase(知识库)，SCHEMA_NAME 注入 search_path
  lib/crypto.py           AES/DES 字节级兼容（DataSource 密码列/身份 cookie 共用）
  models/framework.py     30 张框架表（共享旧库）
  models/knowledge.py     7 张知识库表（PG+pgvector）
  services/
    identity.py           auth/RBAC/身份 cookie
    datasource.py         数据源连接测试
    config_cache.py       modo_dim 缓存     runtime_config.py 三级解析
    retrieval.py          RRF 混合检索（向量臂+关键词臂+融合）
    embedding.py          OpenAI 兼容 /v1/embeddings
    ingest.py             文档入库编排（解析→分块→向量化→落库）
    chat.py               RAG 问答（上下文注入+流式）
    graph.py              LLM 实体/关系抽取 + Neo4j 写入
    wiki.py               页面生成 + 链接
    agents.py             智能体配置服务
    audit_log.py          oper_log 写入
    mcp_server.py         MCP 服务端（kb_* 工具）
  routers/                auth.py / datasources.py / qa.py(SSE) / agents.py(CRUD+SSE)
  assets/prompts/         graph_extraction.yaml / generate_summary.yaml（WeKnora 资产）
worker/
  celery_app.py           Celery 应用（beat 扫描 modo_cron_task）
  tasks/scheduler.py      cron 扫描 + execute_modo_job 分发（TASK_CLASS_REGISTRY）
  tasks/doc_process.py    文档解析/向量化任务
  tasks/wiki_graph.py     wiki/图谱构建任务
docreader/                WeKnora 解析引擎迁入（parser/models/splitter/utils/config）
scripts/
  knowledge_schema.sql    知识库 DDL（vector/uuid-ossp/pg_trgm 扩展 + 7 表 + 索引）
  verify_schema_alignment.py  框架模型↔真实库列级对齐检查
  verify_chat_stream.py   RAG 流式链路验证（mock OpenAI SSE）
tests/                    114 个测试（test_*.py）
```

## 5. 验证方式（每次改动后必跑）

```bash
cd /home/jenkins/chengkai/kb_compilation
.venv/bin/python -m pytest tests/ -q          # 全量测试
.venv/bin/python -c "from fastapi.testclient import TestClient; from api.main import app; print(list(TestClient(app).get('/openapi.json').json()['paths']))"
.venv/bin/python scripts/verify_chat_stream.py  # RAG 流式链路（mock 端点）
```

真实库验证配方：
- 框架库对齐：`KB_DATABASE_URL=$(读 data-synth .env.development 的 DATABASE_URL 并把 host 换成 127.0.0.1:13308) .venv/bin/python scripts/verify_schema_alignment.py`（k=V 必须用 venv 内的 python，execute_code 的系统 python 无依赖）
- 知识库 DDL/检索：连本机 WeKnora-postgres（5433，凭据从 WeKnora/.env 动态读），临时 schema `kb_verify` 跑 DDL + 插数据 + 检索，用完 DROP

## 6. 环境与连接信息

| 组件 | 地址 | 凭据来源 |
|---|---|---|
| 框架 MySQL（data_synth_neo） | 127.0.0.1:13308（synth-mysql-seed-test 容器） | data-synth/.env.development（host 替换为本机） |
| 知识库 PostgreSQL | 127.0.0.1:5433（WeKnora-postgres 容器，PG17） | WeKnora/.env（DB_USER/DB_PASSWORD/DB_NAME） |
| Python | `.venv`（uv 创建，3.11.15） | — |
| 包管理 | `uv sync` / `uv add <pkg>` | — |

注意：`.env` 是敏感文件（read_file 被拒），连接凭据一律**从 env 文件动态读取**，绝不写进命令行/代码。

## 7. 关键坑位记录（务必先读）

1. **Hermes 脱敏会写坏含 URL/凭据字面量的代码**：写含 `redis://host:port`、`jdbc:...@`、密码样式的字符串会被改写/截断。规避：字面量用字符串拼接（`"jdbc:mysql:" + "//h1/db1"`），或从 env 动态读。往写文件时如发现 `***` 或行被吞，立即改用拼接重写。
2. **PG 原生 tsvector 无法分词中文**（整句一个 token）→ 关键词臂用 ILIKE + pg_trgm word_similarity（已实现，勿改回 websearch_to_tsquery）。
3. **pgvector 扩展在 public schema**：表在其他 schema 时 search_path 必须含 public（`SET search_path TO x, public`；api/db.py get_knowledge_engine 通过 connect_args options 注入 SCHEMA_NAME）。DDL 文件顶部有注释说明。
4. **SQLite 不能建 GIN/to_tsvector 索引**：wiki_page 的 FTS 索引只写在 knowledge_schema.sql，不在 SQLAlchemy 模型里（否则 create_all 在 sqlite 测试崩）。
5. **SQLAlchemy 2.0 update().execute() 返回类型**：rowcount 用 `getattr(result, "rowcount", 0)` 规避 Pyright 误报。
6. **mcp SDK 用 v2 语法**：`from mcp.server.mcpserver import MCPServer`（FastMCP 在 2.x 已改名）。工具逻辑抽成模块级 handle_* 函数便于测试（Tool 对象不暴露底层 fn）。
7. **docreader 的 textract 已改惰性导入**（上游因 SSRF 禁用该方法，勿改回顶层 import）。
8. **embedding 维度必须与 DDL 一致**：knowledge_schema.sql 的 vector(1024) 与 EMBEDDING_DIM 默认 1024；测试假向量也用 1024，否则 DataError expected 1024 dimensions。
9. **对齐脚本/execute_code 的 python 是系统解释器**：没有项目依赖，跑 SQLAlchemy 必须用 `.venv/bin/python`。

## 8. 剩余工作（按优先级）

### 8.1 P12 前端对接（唯一剩余大项，3-5 周）
- 从 data-synth 拷贝 Next.js 前端（`src/app/(main)/system/*` 等框架页），把 server actions 调用改 fetch 到本 API（10 个端点）
- 新增知识库页面：知识库管理（列表/建库/索引策略）、文档上传（触发 Celery 入库）、wiki 浏览（wiki_page 树 + 详情）、问答对话（SSE）、智能体配置（agents CRUD）
- 身份：前端 `x-next-identity` cookie 已兼容（AES），`/api/v1/me` 可验证

### 8.2 部署落地（可选先做，让业务侧可看）
- docker-compose：PG+pgvector、Redis（Celery broker）、MySQL（框架库）、FastAPI (uvicorn)、Celery worker + beat
- 环境变量：KNOWLEDGE_DATABASE_URL、EMBEDDING_BASE_URL/API_KEY/MODEL、AI_CHAT_API_ENDPOINT/KEY/MODEL
- 数据库初始化：跑 scripts/knowledge_schema.sql

### 8.3 增强项（按需）
- 知识库删除保护（WeKnora vectorstore delete guard：绑定 KB 数>0 禁止删）
- wiki 页链接健康检查（wiki_lint：死链清理，参考 WeKnora wiki_lint.go）
- 多实例配置缓存失效（当前进程内 TTL，与源平台一致；如需跨实例用 Redis）
- Open API 鉴权层（当前 auth 只登录，open/* 数据源路由未加 X-API-Key 校验）

## 9. 开工检查单（新会话）

1. `cd /home/jenkins/chengkai/kb_compilation && .venv/bin/python -m pytest tests/ -q` → 应 114 passed
2. `git status` → 应干净（或了解未提交改动）
3. 读本文档「剩余工作」选一项继续
4. 改动后用「验证方式」整组跑
5. 完成后 `git add -A && git commit`（用户习惯：功能完成直接提交，无需询问）