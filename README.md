# KB Compilation Platform (kb_compilation)

知识库 wiki 平台：从旧数据合成平台（data-synth）中剔除数据合成业务逻辑，保留系统框架层，并在其上迁移 WeKnora 的知识库能力（文档解析 → 向量化 → 混合检索 → Wiki 生成 → Neo4j 图谱 → 问答）。

> **续接工作请先读 [docs/PROGRESS.md](docs/PROGRESS.md)** —— 记录当前状态、关键文件地图、验证方式、坑位与剩余任务。

## 架构

```
Next.js 前端（保留自旧平台，改造为纯前端）
        │  HTTP / SSE
        ▼
api-server  Python FastAPI（本仓库 api/，抽离自旧平台 Next.js 后端）
        │  同步调用
        ▼
Celery workers（本仓库 worker/，沿用旧平台调度模式）
        ├─ doc_process 文档解析/分块（WeKnora docreader 迁移）
        ├─ embedding   向量化
        ├─ wiki        Wiki 页生成
        └─ graph       实体抽取 / Neo4j 图谱写入
```

## 当前进度

- [x] **P0 项目骨架**：FastAPI + SQLAlchemy 2.0 + Celery，双数据库方言（pg/mysql）
- [x] **P1 框架层模型**：30 张框架表从旧平台 Drizzle schema 1:1 迁移（认证/RBAC/数据源/元数据/调度/日志/文件/操作文档/AI 会话），旧数据合成业务表（`synth_*`、`modo_requirement*` 等 36 张）已剔除
- [x] **模型对齐验证**：`scripts/verify_schema_alignment.py` 对真实库列级比对通过
- [x] **P2 框架服务迁移**：auth/RBAC（密码学与前端字节级兼容）、数据源连接测试、配置缓存（TTL+single-flight）
- [x] **P3 cron/任务调度 → Celery**：beat 扫描 `modo_cron_task`，乐观锁防重复触发
- [x] **P7 docreader 迁移**：文档解析引擎（md/pdf/docx/xlsx/pptx/epub/mhtml，去 gRPC 壳）
- [ ] P4 MCP 服务端（registry → Python mcp SDK）
- [ ] P5 知识库域模型（wiki_ / kb_ 前缀）+ 向量化（pgvector）+ 混合检索（RRF）
- [ ] P6 问答链路（chat_pipeline 照搬）+ 流式 SSE
- [ ] P8 Wiki 生成 + Neo4j 图谱
- [ ] P9 智能体配置
- [ ] P10 前端对接（Next.js 纯前端 + fetch 改造）

## 命名约定

本项目是**知识库 / wiki 域**，新代码中不使用"合成（synth）"概念作为业务命名。

- 本项目自有的表、模块、任务、服务一律采用 wiki 语义（`wiki_*` / `kb_*` 前缀，如 wiki_page、wiki_link、doc_chunk、kb_datasource 等）。
- 例外（必须保留原样）：与源数据库共享的**物理表名**（`synth_*` 等）以及源平台/库/路径标识（`data-synth` 项目名、`data_synth_neo` 库名、旧 `.env` 路径），这些是待对接的源系统真实标识，改名会导致测试断言失实或脚本连不上库。

## 快速开始

```bash
# 依赖安装（uv）
uv sync

# 运行测试（不依赖真实库）
.venv/bin/python -m pytest

# 模型 ↔ 真实库对齐检查
.venv/bin/python scripts/verify_schema_alignment.py /path/to/data-synth/.env.development

# 启动 API 服务
.venv/bin/uvicorn api.main:app --port 8000

# 启动 Celery worker（示例）
.venv/bin/celery -A worker.celery_app worker --loglevel=info
.venv/bin/celery -A worker.celery_app beat
```

## 目录结构

```
api/
  main.py            FastAPI 入口（健康检查等）
  config.py          配置（环境变量，对齐源平台命名）
  db.py              SQLAlchemy engine/session（懒加载，双方言）
  models/
    framework.py     框架层 ORM 模型（30 张表，1:1 迁移自 schema.ts）
  routers/           路由（P2 起迁移）
  services/          业务服务（P2 起迁移）
  schemas/           Pydantic DTO（P2 起迁移）
worker/
  celery_app.py      Celery 应用（beat 扫描 modo_cron_task）
  tasks/             任务模块（P3/P5 填充）
scripts/
  verify_schema_alignment.py   模型与真实库列级对齐检查
tests/
  test_framework_models.py     框架模型自洽性测试
docreader/           WeKnora 文档解析服务（P5 迁入）
```

## 设计约定

- **表名/列名/索引名与原库完全一致**：本服务直接共享源平台数据库（同一套表），不做任何改名。
- **环境变量命名对齐源平台**：`DB_TYPE` / `DATABASE_URL` / `AUTH_SECRET` / `CELERY_*`，方便复用现有部署配置。
- **Celery 沿用旧平台调度模式**：beat 扫描 `modo_cron_task` 表分发任务；`task_acks_late` + `worker_prefetch_multiplier=1` 保证长任务可靠执行。
