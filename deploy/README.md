# KB Compilation Platform — 部署指南（docker-compose）

一键起整套：nginx + web(Next.js) + api-server(FastAPI) + celery worker/beat + redis + pg(pgvector) + mysql。

## 前置

- Docker + docker compose（本机验证用 `docker compose config` 语法检查）
- 端口：80（nginx，可用 `NGINX_PORT` 改）

## 步骤

```bash
cd /home/jenkins/chengkai/kb_compilation

# 1. 生成部署环境变量（凭据/密钥行留空，需手动补）
.venv/bin/python scripts/gen_deploy_env.py

# 2. 补 deploy/.env 里的空值：
#    - AUTH_SECRET / AES_SECRET_KEY / AES_IV_KEY：沿用 data-synth 源平台的值
#      （与 scripts/gen_env.py 生成的本地 .env 一致）
#    - EMBEDDING_* / AI_CHAT_*：LLM/向量服务地址（不配则向量臂禁用/问答报错）

# 3. 启动
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
```

访问 `http://<host>:80`（登录账号沿用框架库种子用户，如 huqiang/sys）。

## 首次初始化

- **知识库 PG**：首次启动自动执行 `scripts/knowledge_schema.sql`（pgvector DDL，幂等），无需手动。
- **框架 MySQL**：容器建空库 `kb_frame`（MYSQL_DATABASE），**表结构与种子数据需手动灌入**：

```bash
# 方式一：从已初始化的共享库导出（如 data_synth_neo，30 张框架表 + 用户/角色种子）
mysqldump -h 127.0.0.1 -P 13308 -u<user> -p data_synth_neo \
  | docker compose -f deploy/docker-compose.yml exec -T mysql mysql -u root -p kb_frame

# 方式二：SQLAlchemy 建表 + 手工插入种子用户
.venv/bin/python - <<'PY'
from api.db import get_sessionmaker
from api.models.framework import Base, User
db = get_sessionmaker()()
Base.metadata.create_all(bind=db.get_bind())
# 再按需插入用户（密码需 AES 加密，参考 api.lib.crypto）
PY
```

> 注意：框架 MySQL 与知识库 PG 是**两套独立存储**——用户/角色/菜单/数据源/调度/日志走 MySQL，文档分块向量/wiki 页走 PG。本机联调时框架库连的是共享的 data_synth_neo（127.0.0.1:13308），部署环境用自带 MySQL，二者凭据互不通用。

## 组件与端口

| 服务 | 容器名 | 对外端口 | 说明 |
|---|---|---|---|
| nginx | kb-nginx | 80 | 统一入口，转发到 web；SSE 路径关缓冲 |
| web | kb-web | —(内网 3000) | Next.js 前端，内部代理 /api/* 到 api-server |
| api-server | kb-api-server | —(内网 8000) | FastAPI 23 端点 |
| celery-worker | kb-celery-worker | — | 文档解析/向量化/wiki/图谱 |
| celery-beat | kb-celery-beat | — | 扫描 modo_cron_task |
| flower | kb-flower | —(内网 5555) | Celery 监控 UI，经 nginx `/flower/` 访问（basic auth） |
| redis | kb-redis | —(内网 6379) | broker + 结果后端 |
| pg | kb-pg | —(内网 5432) | PG17 + pgvector，知识库 DDL 自动初始化 |
| mysql | kb-mysql | —(内网 3306) | 框架库（需手动建表/种子） |

## 访问链路

```
浏览器 → :80 nginx → web:3000 (Next.js 页面 + /api/* 代理) → api-server:8000
                                                          → pg / mysql / redis
                                                          → celery worker（异步解析/向量化/wiki）
```

## 常用运维

```bash
# 看日志
docker compose -f deploy/docker-compose.yml logs -f api-server
docker compose -f deploy/docker-compose.yml logs -f celery-worker

# 重启单服务
docker compose -f deploy/docker-compose.yml restart api-server

# 停止/清理
docker compose -f deploy/docker-compose.yml down          # 保留数据卷
docker compose -f deploy/docker-compose.yml down -v       # 连数据卷一起清
```

## 验证

```bash
curl -s http://<host>/nginx-health          # nginx 存活
curl -s http://<host>/api/v1/open/health    # API 存活
curl -s -X POST http://<host>/api/v1/auth/login \
  -H 'content-type: application/json' -d '{"userId":"huqiang","pwd":"sys"}'   # 登录
```

## Worker 监控（Flower）

浏览器访问 `http://<host>/flower/`，用 `FLOWER_BASIC_AUTH`（deploy/.env，默认 admin:admin，建议部署后修改）登录。可查看：worker 存活、任务队列、各任务执行状态/耗时/失败重试、Broker 队列深度。Flower 通过同一 Redis broker 监控，与 celery-worker 同源。
