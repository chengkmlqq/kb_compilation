# KB Compilation Platform (kb_compilation)

知识库编译平台：从 data-synth 中剔除数据合成业务逻辑，保留系统框架层，并在其上迁移 WeKnora 的知识库能力（文档解析 → 向量化 → 混合检索 → Wiki 生成 → Neo4j 图谱 → 问答）。

## 架构

```
Next.js 前端（保留自 data-synth，改造为纯前端）
        │  HTTP / SSE
        ▼
api-server  Python FastAPI（本仓库 api/，抽离自 data-synth Next.js 后端）
        │  同步调用
        ▼
Celery workers（本仓库 worker/，复用 data-synth synth_scheduler 模式）
        ├─ doc_process 文档解析/分块（WeKnora docreader 迁移）
        ├─ embedding   向量化
        ├─ wiki        Wiki 页生成
        └─ graph       实体抽取 / Neo4j 图谱写入
```

## 当前进度

- [x] **P0 项目骨架**：FastAPI + SQLAlchemy 2.0 + Celery，双数据库方言（pg/mysql）
- [x] **P1 框架层模型**：30 张框架表从 data-synth schema.ts 1:1 迁移（认证/RBAC/数据源/元数据/调度/日志/文件/操作文档/AI 会话），合成业务表（`synth_*`、`modo_requirement*` 等 36 张）已剔除
- [x] **模型对齐验证**：`scripts/verify_schema_alignment.py` 对真实库列级比对通过
- [ ] P2 框架服务迁移（auth/RBAC / 数据源 / 元数据 / 文件 / 配置缓存）
- [ ] P3 cron/任务调度 → Celery
- [ ] P4 MCP 服务端（Python mcp SDK）
- [ ] P5 WeKnora 功能迁移（docreader / 向量化 / 混合检索 / 问答 / Wiki / 图谱 / 智能体）

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
  config.py          配置（环境变量，对齐 data-synth 命名）
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

- **表名/列名/索引名与原库完全一致**：本服务直接共享 data-synth 的数据库（同一套表），不做任何改名。
- **环境变量命名对齐 data-synth**：`DB_TYPE` / `DATABASE_URL` / `AUTH_SECRET` / `CELERY_*`，方便复用现有部署配置。
- **数据库懒加载**：engine 在首次访问时创建，测试和应用启动不依赖真实数据库。
- **Celery 沿用 synth_scheduler 模式**：beat 扫描 `modo_cron_task` 表分发任务；`task_acks_late` + `worker_prefetch_multiplier=1` 保证长任务可靠执行。
