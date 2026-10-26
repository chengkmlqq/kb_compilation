# KB Compilation Platform — 架构说明

> 本文档描述 KB Compilation Platform（知识库 Wiki 平台）的系统架构：组件构成、组件关系、系统数据流与外部依赖。
> 事实来源：`deploy/docker-compose.yml`（17 服务）、`deploy/nginx/default.conf`、`api/config.py`、`worker/celery_app.py`、`docs/PROGRESS.md`（2026-10-12 核验）。
> 若与 README 旧描述冲突，以本文件为准（README 部分描述滞后于实际部署）。

## 1. 系统概述

KB Compilation Platform 是**知识库 Wiki 平台**：以系统框架层（认证/RBAC/菜单/调度/日志）为底座，承载知识库能力（文档解析 → 向量化 → 混合检索 → Wiki 生成 → Neo4j 图谱 → 问答）。

核心定位：
- **单库双引擎**：框架业务表（用户/角色/菜单/调度/日志 + 知识库业务表）走关系库（MySQL，可移植）；向量检索走独立向量库（PG17 + pgvector，`VectorStore` 抽象预留 ES）。
- **前后端分离**：Next.js 16 纯前端 + FastAPI 后端 + Celery 多队列异步任务。
- **任务队列隔离**：5 个 Celery worker 按队列物理隔离（解析 / wiki 构建 / 内化引擎 / 图谱 / 编排），互不饿死、可独立扩容。

## 2. 总体架构（分层视图）

```
┌──────────────────────────────────────────────────────────────────────┐
│                        接入层 (Access)                                │
│   nginx(:80 统一入口)  ── /wiki ──▶  web(Next.js 16 纯前端, :3000)   │
│        │  /api 直连      │  /mcp      │  /flower                     │
│        ▼                ▼            ▼                              │
├────────────────── API 层 (FastAPI) ──────────────────────────────────┤
│   api-server (:8000，24 组路由，RBAC 守卫，SSE 流式)                  │
│   mcp-server (:18001，3 个知识库工具，streamable-http)                │
├────────────────── 任务层 (Celery + Redis) ───────────────────────────┤
│   celery-worker(-Q default, c2)   文档解析/向量化                     │
│   celery-agent-worker(-Q agent, c4)  wiki 构建 agent 运行时           │
│   celery-build-worker(-Q build, c4)  内化 wiki 构建引擎               │
│   celery-graph-worker(-Q graph, c2)  实体抽取 / Neo4j 写入            │
│   celery-orch-worker(-Q orch, c2)    编排 DAG 调度中枢                │
│   celery-beat  定时扫描 modo_cron_task / flower(:5555) 监控           │
├────────────────── 数据层 (Storage) ──────────────────────────────────┤
│   mysql8 框架库+知识库业务表 │ pg17+pgvector 向量 │ redis7 broker     │
│   minio 对象存储 │ neo4j 图谱 │ mineru 文档解析(CPU)                  │
└──────────────────────────────────────────────────────────────────────┘
                    ↓ 外部依赖
   LLM(EMBEDDING_*/AI_CHAT_*/WIKI_LLM_*) │ agent-gateway(:8080 回退)
   镜像仓库(阿里云 CR) │ hf-mirror(权重下载) │ SSO/JWT(可选)
```

## 3. 组件清单与职责

### 3.1 接入层

| 服务 | 容器名 | 端口 | 职责 |
|---|---|---|---|
| nginx | kb-nginx | 宿主 **:80** | 统一入口门卫，按路径分发：`/api/`→api-server（直连，SSE 关缓冲）、`/mcp`→mcp-server、`/wiki/`→web（Next.js basePath）、`/flower/`→flower；`/nginx-health` 健康检查；上传体积上限 50MB |
| web | kb-web | 内网 3000 | Next.js 16 纯前端（App Router + Ant Design），应用整体挂 `/wiki` 前缀；页面渲染 + 与 api-server 的浏览器代理会话透传 |

### 3.2 API 层

| 服务 | 容器名 | 端口 | 职责 |
|---|---|---|---|
| api-server | kb-api-server | 内网 8000 | FastAPI 后端，24 组路由（auth/datasources/qa/chat-sessions/agents/kbs/jobs/cron/system/system-config/models/ontology/mcps/websearch/skills/workers/chunking/files/datagrid/metadata/graph/orchestrations）；RBAC 中间件守卫；SSE 流式问答 |
| mcp-server | kb-mcp-server | 内网 18001 | MCP 服务端（mcp SDK v2），暴露 3 个知识库工具 `kb_list` / `kb_search` / `kb_answer`，streamable-http 协议，经 nginx `/mcp` 对外；复用 api-server 镜像仅换启动命令 |

### 3.3 任务层（Celery）

| 服务 | 容器名 | 队列 | 并发 | 职责 |
|---|---|---|---|---|
| celery-worker | kb-celery-worker | `default` | 2 | 消费文档解析/向量化任务（KbDocumentProcessTask / KbDocumentEmbedTask），调用 docreader（内置）或 mineru（外部）解析，分块、向量化、落库 |
| celery-agent-worker | kb-celery-agent-worker | `agent` | 4 | wiki 构建的 agent 运行时（内联 openai-agents SDK），与解析 worker 物理隔离，独立镜像 |
| celery-build-worker | kb-celery-build-worker | `build` | 4 | 内化 wiki 构建引擎（worker/builtin_engine），只消费 build 队列 |
| celery-graph-worker | kb-celery-graph-worker | `graph` | 2 | 图谱构建 KbGraphBuildTask：全库 chunk 实体/关系抽取（LLM 重活，单任务数分钟）+ Neo4j 写入，独立队列防止饿死解析 |
| celery-orch-worker | kb-celery-orch-worker | `orch` | 2 | 编排调度中枢：execute_run / advance_run，负责编排 DAG（kb_tape_run）的依赖检查与节点投递，节点本身投到各自队列 |
| celery-beat | kb-celery-beat | — | — | 周期扫描 `modo_cron_task` 表（JOB_BEAT_SCAN_INTERVAL_SECONDS 默认 30s），乐观锁防重复触发 |
| flower | kb-flower | — | — | Celery worker 监控 UI（任务队列/执行状态/失败重试），内网 5555，经 nginx `/flower/`（FLOWER_BASIC_AUTH 登录） |

任务配置共性：`task_acks_late=True` + `worker_prefetch_multiplier=1` 保证长任务可靠执行；5 个 worker 共用同一 redis broker、不同队列。

**孤儿任务恢复**：worker 重启会中断执行中的任务（acks_late 消息要等 visibility_timeout 才重投），`celery_app.py` 在 worker_ready 时扫描本队列遗留 RUNNING 的 execute_modo_job 并重投到队尾，从源头消除「卡 RUNNING 数小时」。

### 3.4 数据层

| 服务 | 容器名 | 端口 | 职责 |
|---|---|---|---|
| redis | kb-redis | 内网 6379 | Celery broker + result backend（appendonly 持久化） |
| pg | kb-pg | 内网 5432 | PG17 + pgvector：向量表 `kb_embedding`（vector 1024 + HNSW）；首次启动自动应用 `knowledge_schema.sql`（幂等） |
| mysql | kb-mysql | 内网 3306 | 框架库：30 张框架表（用户/角色/菜单/数据源/调度/日志）+ 7 张知识库业务表（kb_datasource/kb_document/doc_chunk/wiki_folder/wiki_page/wiki_link/kb_agent）+ 编排表（kb_tape 等），utf8mb4；首次启动自动建知识库业务表 DDL，框架表需手动建表+种子 |
| minio | kb-minio | 宿主 9000 / 控制台 9001 | 对象存储（S3 协议），存储文档原始文件，桶 `kb-compilation` |
| neo4j | kb-neo4j | 宿主 17474(HTTP) / 17687(Bolt) | 知识图谱存储（实体/关系），apoc 插件开启，独立实例（数据卷/密码隔离）；heap 1G / pagecache 512M |
| mineru | kb-mineru | 宿主 **18000** | 文档解析服务（PDF/OCR，CPU 推理，onnxruntime+llama.cpp，不装 torch）：自建镜像 `mineru==4.0.9`（官方镜像被网络白名单拦截），入口 `server_shim`（官方 create_app + 同步 `/file_parse` 契约端点）；模型权重约 2.1GB 存 `mineru-models` 卷（hf-mirror 下载，首次 20-70 分钟，健康检查 start_period 1 小时）；常驻内存约 2GiB |

**数据卷**：pg-data / mysql-data / redis-data / kb-docs（文档目录 `/data/kb_documents`，api-server 与各 worker 共享）/ minio-data / mineru-models / neo4j-data。

## 4. 组件关系图

```
浏览器 / 外部MCP客户端
        │ HTTP(:80) / SSE
        ▼
   nginx (kb-nginx)
   ├── /wiki          ──▶ web ──(API_BASE_URL 内网)──▶ api-server
   ├── /api  (直连,关缓冲) ──▶ api-server
   ├── /mcp            ──▶ mcp-server
   └── /flower         ──▶ flower
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
     mysql(框架+业务表)   pg(向量)       redis(broker/backend)
              ▲               ▲                ▲
              │               │                │
        api-server ──enqueue──▶ redis ◀──consume── 5 个 celery worker
              ▲                                    │
              │                                    ├─ celery-worker(解析/向量化) ──▶ docreader/mineru
              │                                    ├─ celery-agent-worker ──▶ LLM(WIKI_LLM_*)
              │                                    ├─ celery-build-worker ──▶ LLM
              │                                    ├─ celery-graph-worker ──▶ LLM ──▶ neo4j
              │                                    └─ celery-orch-worker ──▶ 编排 DAG ──▶ 各队列
              │
              ├──▶ minio(对象存储，文档文件)
              ├──▶ neo4j(图谱查询)
              └──▶ agent-gateway(宿主:8080，host.docker.internal，回退)
```

## 5. 系统数据流详解

### 5.1 文档入库主链路（上传 → wiki 页）

```
浏览器上传(≤50MB)
  → nginx /api/ → api-server(落盘 kb-docs 卷 / minio，建 kb_document PENDING，入队)
  → celery-worker(解析：docreader 或 mineru) → 分块(800/80) → 向量化(EMBEDDING_*) → 落库
      ├─ doc_chunk 业务表(mysql) + kb_embedding 向量(pg)
      └─ parse_state=READY
            ├─ [KB 绑定构建引擎] KbOrchestrationRunTask → orch 队列
            │    → celery-orch-worker 编排 DAG(kb_tape_run, env=prod)
            │    → 按依赖逐节点投递(agent/build/graph 等队列)
            │    → wiki 页写入(wiki_page/wiki_link)
            └─ [未绑定] KbSkillWikiBuildTask → agent/build 队列
                 → 读 doc_chunk → LLM(WIKI_LLM_*) → 幂等写 wiki 页 → wiki_rebuild_links 重建互链
```

### 5.2 图谱构建

```
KbGraphBuildTask → graph 队列 → celery-graph-worker
  → 读全库 chunks → LLM 实体/关系抽取 → Neo4j MERGE 写入（api/services/graph.py Neo4jGraphStore）
  → api-server graph 路由提供图谱查询
```

### 5.3 问答链路（RAG，SSE 流式）

```
浏览器 /chat → nginx /api/ → api-server
  → RRF 混合检索：
      向量臂(kb_embedding 余弦距离+阈值) + 关键词臂(可移植 ilike+应用侧评分)，RRF k=60, 0.7/0.3
  → LLM 生成（AI_CHAT_* / kb_model 注册表，支持 agent 模式多轮）
  → SSE 流式返回（proxy_buffering off，chunk_id 引用标注）
```

### 5.4 定时调度

```
celery-beat 每 30s → scan_cron_tasks
  → 扫描 modo_cron_task（初始化/校准/乐观锁防双触发）→ execute_modo_job
  → 按 task_class 分发到对应队列（TASK_CLASS_REGISTRY）
```

### 5.5 MCP 对外服务

```
外部 MCP 客户端 → nginx /mcp → mcp-server:18001
  → kb_list / kb_search / kb_answer 三个工具（sessionId 参数约定）
  → 内部经 redis/pg/mysql 读知识库
```

## 6. 外部依赖说明

| 依赖 | 配置项 | 用途 | 说明 |
|---|---|---|---|
| **LLM 服务** | `EMBEDDING_*`（向量化）、`AI_CHAT_*`（问答/agent）、`WIKI_LLM_*`（wiki 构建，默认 inferaiapi.com + deepseek-v4-pro） | 向量化、RAG 问答、wiki 页生成、图谱实体抽取 | OpenAI 兼容接口；模型注册表 `kb_model` 表管理（支持 SiliconFlow 等；rerank 模型需 base_url 以 /rerank 结尾） |
| **agent-gateway** | `AGENT_GATEWAY_BASE_URL=http://host.docker.internal:8080` | 外部 agent 技能执行（回退路径） | 宿主绑定的 hermes agent 网关；WIKI_AGENT_MODE 默认 `inline`（worker 内联 agent），gateway 仅回退；worker 经 host.docker.internal 访问 |
| **镜像仓库** | `REGISTRY=crpi-...aliyuncs.com/ck-registry` | 部署镜像拉取 | 阿里云容器镜像服务（ccr），镜像 kb-web/kb-api-server/kb-worker/kb-agent-worker |
| **hf-mirror** | `HF_ENDPOINT=https://hf-mirror.com` | MinerU 模型权重下载 | HuggingFace 直连不可达，走镜像站（约 2.1GB，首次下载 20-70 分钟） |
| **SSO / JWT** | `SSO_ENABLED` / `JWT_SECRET` | 外部身份对接（可选） | SSO 登录探测；JWT 换签（挂载场景 URL 带 `?jwt=` 免二次登录，HS256 共享密钥，JWT_MAX_AGE 900s 防 token 流入日志） |
| **外部数据源** | — | 数据源连接测试/数据查询 | 通过数据源配置对接 mysql/postgres/kingbase/trino/minio 等 |

网络访问特性：
- 仅 **nginx(80)**、**mineru(18000)**、**minio(9000/9001)**、**neo4j(17474/17687)** 映射宿主端口；其余服务（web/api-server/mcp/flower/redis/pg/mysql/worker/beat）仅在 `kb-net` 内部网络互通，不直接对外。
- api-server / 各 worker 通过 `extra_hosts: host.docker.internal` 访问宿主绑定的 agent-gateway（Linux 无自动映射，须显式配置；Docker Desktop 上为空操作）。
- 对外唯一入口是 nginx：`/api/` 直连 api-server（不再经 web 跳板），SSE 路径关闭缓冲与缓存、超时 3600s。

## 7. 端口映射与网络策略

### 7.1 宿主端口映射（对外）

| 端口 | 服务 | 用途 |
|---|---|---|
| 80 (+`NGINX_PORT` 可改) | nginx | Web / API / MCP / Flower 统一入口 |
| 18000 | mineru | 文档解析服务（调试可 `curl -F "file=@doc.pdf" http://host:18000/file_parse`） |
| 9000 / 9001 | minio | S3 API / 管理控制台（`MINIO_API_PORT`/`MINIO_CONSOLE_PORT` 可改） |
| 17474 / 17687 | neo4j | 图谱 Browser / Bolt（避开常用端口 7474/7687，`NEO4J_HTTP_PORT`/`NEO4J_BOLT_PORT` 可改） |

### 7.2 服务内部网络依赖（kb-net 内）

| 组件 | 依赖 | 端口 |
|---|---|---|
| nginx | web / api-server / mcp-server / flower | 3000 / 8000 / 18001 / 5555 |
| web | api-server（API_BASE_URL 内网地址） | 8000 |
| api-server | redis（enqueue + 读结果）、pg（向量）、mysql（业务/框架）、minio（对象）、neo4j（图谱查询）、agent-gateway（经宿主） | 6379/5432/3306/9000/7687/8080 |
| 5 个 celery worker | redis（consume）、pg、mysql、minio、agent-gateway；解析 worker 另依赖 mineru | 同上 |
| mcp-server | pg / mysql / redis | 5432/3306/6379 |

### 7.3 nginx 路由表

| 路径 | 上游 | 特性 |
|---|---|---|
| `/nginx-health` | 直返 200 | 存活检查 |
| `/api/` | api-server:8000 | 直连、SSE 关缓冲/缓存、读/写超时 3600s、升级头 |
| `/mcp` | mcp-server:18001 | 前缀匹配不重写、SSE/长连接语义同 /api |
| `/wiki` | web:3000 | basePath 应用，读超时 300s |
| `/flower/` | flower:5555 | Flower UI，尾斜杠转发 |
| `/` | 302 → /wiki/ | 根路径引导 |
| 其余 | 404 | — |

## 8. 技术栈清单

| 维度 | 技术选型 |
|---|---|
| 前端 | Next.js 16（App Router）+ React + Ant Design，纯前端（basePath /wiki），nginx 直连 /api 不再经前端跳板 |
| 后端 | Python FastAPI 一体（api-server），SQLAlchemy 2.0 双引擎（MySQL + PG） |
| 任务 | Celery **5 队列隔离**（default/agent/build/graph/orch）+ orphan recovery 孤儿任务恢复 |
| 数据库 | MySQL 8 框架库 + 知识库业务表；**PG17 + pgvector** 向量库（`VectorStore` 抽象预留 ES） |
| 存储 | MinIO 对象存储（S3）+ 本地卷（kb-docs）双落点 |
| 图谱 | Neo4j（apoc 插件）实体/关系图谱 |
| 解析 | docreader（内置）+ **MinerU**（独立 CPU 解析服务，onnxruntime + llama.cpp） |
| 外部对接 | SSO/JWT 换签、MCP streamable-http 对外、agent-gateway 回退 |

**架构特点**：四层分层（接入 → API → 任务 → 数据），DAG 编排能力下沉为任务层的 orch 队列（编排模板即 kb_tape，wiki 构建/图谱/解析均可作为编排节点复用）。

## 9. 部署形态

- **镜像**：所有 kb-* 服务镜像从阿里云 CR 拉取（`REGISTRY` 可换私有源），MinerU 为本地自建镜像（`kb-mineru:local`，源码构建）。
- **启动**：`docker compose -f deploy/docker-compose.yml up -d`（全部配置在 deploy/.env，无凭据字面量）。
- **首次初始化**：pg 自动建向量 DDL；mysql 自动建知识库业务表；框架 `modo_*` 表需手动建表+种子（从已初始化共享库 mysqldump 或 SQLAlchemy create_all）。
- **健康检查**：pg（pg_isready）、mysql（mysqladmin ping）、minio（mc ready）、neo4j（cypher-shell）、mineru（/v1/health，start_period 1h）、api-server（/api/v1/open/health）、nginx（/nginx-health）。
- **扩容**：各 worker 独立 `--concurrency` 与队列，按需 `--scale` 或调并发参数即可独立扩容。