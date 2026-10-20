"""KB Compilation Platform — configuration.

Environment variables follow the source platform's conventions (DB_TYPE, DATABASE_URL, ...)
so the extracted service can be dropped into the existing deployment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (same behaviour as the source Next.js app).
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


class Settings:
    """Runtime settings, read from env with source-compatible names."""

    # --- Database (dual dialect: pg / mysql, switched by DB_TYPE) ---
    DB_TYPE: str = os.getenv("DB_TYPE", "pg")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SCHEMA_NAME: str = os.getenv("SCHEMA_NAME", "public")
    DB_MAX_CONNECTIONS: int = int(os.getenv("DB_MAX_CONNECTIONS", "10"))
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "1") == "1"
    # 启动时自动应用 Alembic 迁移（框架库 schema 对齐，见 api/services/migrations.py）
    AUTO_MIGRATE_ON_START: bool = os.getenv("AUTO_MIGRATE_ON_START", "1") == "1"
    # ── 环境隔离（2026-10-12 双库 → 2026-10-20 双表改造）────────────
    # 原「dev/prod 双库」方案（DEV_DATABASE_URL 独立库）已废弃：远程库只有
    # data_supply 一个，dev/prod 共用同一个 DATABASE_URL（api/db.py
    # _resolve_db_url 恒返回 DATABASE_URL）。环境隔离改为同库双表——
    # dev 环境（编排测试执行：手工/设计器/定时）的 SQL 由
    # api/services/env_table_router.py 的 before_cursor_execute 事件自动
    # 路由到 xxx_dev 表；prod（知识库上传触发链路）写正式表。
    # 以下 DEV_* / KB_ENV_ROUTING_ENABLED 字段保留仅为.env 兼容读取，
    # 已无任何消费点（勿重新启用）。
    DEV_DATABASE_URL: str = os.getenv("DEV_DATABASE_URL", "")
    DEV_KNOWLEDGE_DATABASE_URL: str = os.getenv("DEV_KNOWLEDGE_DATABASE_URL", "")
    DEV_SCHEMA_NAME: str = os.getenv("DEV_SCHEMA_NAME", "dev")
    KB_ENV_ROUTING_ENABLED: bool = os.getenv("KB_ENV_ROUTING_ENABLED", "0") == "1"

    # --- Vector store (PostgreSQL + pgvector, for chunk embeddings only) ---
    # The knowledge BUSINESS tables (kb_datasource / kb_document / doc_chunk /
    # wiki_* / kb_agent) live on the FRAMEWORK relational store (DATABASE_URL);
    # only the `kb_embedding` vector table lives on this PG store so pgvector
    # and HNSW search are available. VECTOR_STORE_TYPE=es is reserved for a
    # future Elasticsearch backend.
    KNOWLEDGE_DATABASE_URL: str = os.getenv("KNOWLEDGE_DATABASE_URL", "")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1024"))
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")

    # --- Auth ---
    AUTH_SECRET: str = os.getenv("AUTH_SECRET", os.getenv("NEXTAUTH_SECRET", ""))
    SSO_ENABLED: bool = os.getenv("SSO_ENABLED", "0") == "1"
    SSO_URL: str = os.getenv("SSO_URL", "")
    COOKIES_MAX_AGE: int = int(os.getenv("COOKIES_MAX_AGE", "172800"))
    # --- JWT 换签认证（挂载场景 URL 带 ?jwt= 免二次登录）---
    # 宿主系统用 HS256 共享密钥签发 JWT（payload 含 userId/sub + exp），
    # 前端调 /api/v1/auth/jwt-exchange 验签换签 x-next-identity cookie，
    # 之后所有接口走正常认证链路（RBAC 照常生效）。密钥空 = 端点禁用。
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    # 可选：签发方白名单，配置后校验 JWT 的 iss 必须匹配（空 = 不校验）
    JWT_ISSUER: str = os.getenv("JWT_ISSUER", "")
    # URL 携带 JWT 的最大可接受时效（秒），防止长期有效的 token 流入日志
    JWT_MAX_AGE: int = int(os.getenv("JWT_MAX_AGE", "900"))
    # Comma-separated user ids that bypass the RBAC path check (source
    # proxy's AUTH_ADMIN_USERS equivalent). plat-mgr role also bypasses.
    AUTH_ADMIN_USERS: str = os.getenv("AUTH_ADMIN_USERS", "")

    # --- Redis / Celery ---
    # All URLs come from env only. If unset, Celery falls back to its own
    # localhost defaults for local development.
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "")
    CELERY_TIMEZONE: str = os.getenv("CELERY_TIMEZONE", "Asia/Shanghai")
    JOB_BEAT_SCAN_INTERVAL_SECONDS: int = int(
        os.getenv("JOB_BEAT_SCAN_INTERVAL_SECONDS", "30")
    )

    # --- Knowledge file storage (uploaded KB documents) ---
    # Local directory where uploaded document bytes are kept. The Celery
    # doc-process task reads from `storage_path` on kb_document.
    KB_STORAGE_DIR: str = os.getenv("KB_STORAGE_DIR", "")

    # --- Vector store backend ---
    # pg (default, pgvector) | es (Elasticsearch dense_vector kNN).
    # Business tables always live on the framework store; only chunk
    # embeddings go to the vector backend.
    VECTOR_STORE_TYPE: str = os.getenv("VECTOR_STORE_TYPE", "pg")
    # Elasticsearch connection (used when VECTOR_STORE_TYPE=es).
    ES_URL: str = os.getenv("ES_URL", "http://127.0.0.1:9200")
    ES_USERNAME: str = os.getenv("ES_USERNAME", "")
    ES_PASSWORD: str = os.getenv("ES_PASSWORD", "")
    ES_INDEX_NAME: str = os.getenv("ES_INDEX_NAME", "kb_chunks")

    # --- Flower (Celery worker 监控，worker monitor page) ---
    # Base URL of the Flower HTTP API. In docker-compose the api-server reaches
    # the flower container by service name (http://flower:5555). Local dev can
    # point at a locally-running flower.
    FLOWER_API_BASE_URL: str = os.getenv("FLOWER_API_BASE_URL", "http://flower:5555")
    # Basic auth for the Flower HTTP API (user:password).
    FLOWER_BASIC_AUTH: str = os.getenv("FLOWER_BASIC_AUTH", "admin:admin")
    # Timeout (seconds) for each Flower API call.
    FLOWER_API_TIMEOUT_S: int = int(os.getenv("FLOWER_API_TIMEOUT_S", "15"))

    # --- Wiki skill auto-trigger (文档处理完成后自动技能构建) ---
    # 上传文档解析/向量化成功后，自动向 agent-gateway 提交的技能任务：
    #   WIKI_SKILL_NAME      技能名（kb_skill 表 scope=system 的 wiki 构建技能）
    #   KB_PUBLIC_BASE_URL   技能脚本调 kb API 的入口（宿主 nginx）
    WIKI_SKILL_NAME: str = os.getenv("WIKI_SKILL_NAME", "kb-wiki-builder")
    KB_PUBLIC_BASE_URL: str = os.getenv("KB_PUBLIC_BASE_URL", "http://10.1.215.50")
    # 技能任务执行的 LLM（config 注入后 gateway runner 转 WEKNORA_LLM_*）：
    # 默认对齐 Hermes 当前供应商 Infer AI；部署 .env 覆写
    WIKI_LLM_BASE_URL: str = os.getenv("WIKI_LLM_BASE_URL", "https://inferaiapi.com/v1")
    WIKI_LLM_MODEL: str = os.getenv("WIKI_LLM_MODEL", "deepseek-v4-pro")
    WIKI_LLM_API_KEY: str = os.getenv("WIKI_LLM_API_KEY", "")

    # --- MinIO 对象存储（2026-10-03 引入，本地磁盘为默认后端）---
    # 上传路径形如 minio://bucket/key 时走 MinIO，其余走 KB_STORAGE_DIR 本地磁盘
    # （历史数据零迁移）。MINIO_ENDPOINT 留空 = 不启用 MinIO。
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "")
    # 应用版本号（构建时注入；页面 /system/info 展示）
    APP_VERSION: str = os.getenv("APP_VERSION", "0.0.0")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "")
    MINIO_SECURE: str = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
    # 默认 bucket（kb 文档/文件上传用）
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "kb-compilation")

    # --- Agent gateway (hermes-agent skill execution service) ---
    # Base URL of the separately-deployed agent-gateway (default host:8080).
    # In docker-compose the worker reaches it via host.docker.internal:8080
    # (gateway binds host 8080 -> container 8080).
    AGENT_GATEWAY_BASE_URL: str = os.getenv("AGENT_GATEWAY_BASE_URL", "http://127.0.0.1:8080")
    # Admin token for the gateway's management APIs (MCP/skills). Empty = no token.
    AGENT_GATEWAY_ADMIN_TOKEN: str = os.getenv("AGENT_GATEWAY_ADMIN_TOKEN", "")
    # Global wait budget for a single gateway task (a full skill run takes
    # 10-25 min; mirrors the gateway's SKILL_SCRIPT_TIMEOUT_S=3600).
    AGENT_GATEWAY_TIMEOUT_S: int = int(os.getenv("AGENT_GATEWAY_TIMEOUT_S", "3600"))
    # Polling interval for GET /tasks/{id} while the agent runs.
    AGENT_GATEWAY_POLL_INTERVAL_S: int = int(os.getenv("AGENT_GATEWAY_POLL_INTERVAL_S", "15"))

    # --- Neo4j knowledge graph ---
    # 实体/关系图存储。写入：worker KbGraphBuildTask -> api/services/graph.py
    # Neo4jGraphStore；查询：api/routers/graph.py。独立实例 kb-neo4j（与
    # WeKnora 隔离），docker-compose 内经服务名访问 bolt://neo4j:7687。
    NEO4J_URI: str = os.getenv("NEO4J_URI", "")
    NEO4J_USERNAME: str = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
    NEO4J_DATABASE: str = os.getenv("NEO4J_DATABASE", "neo4j")

    @property
    def neo4j_enabled(self) -> bool:
        """Neo4j 是否已配置（URI + 口令齐备才算可用）。"""
        return bool(self.NEO4J_URI and self.NEO4J_PASSWORD)

    # --- Server ---
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

    @property
    def kb_storage_dir(self) -> str:
        """Resolve the KB document storage directory (creates it lazily)."""
        base = self.KB_STORAGE_DIR or str(Path(__file__).resolve().parent.parent / "data" / "kb_documents")
        Path(base).mkdir(parents=True, exist_ok=True)
        return base

    @property
    def is_pg(self) -> bool:
        return self.DB_TYPE in ("pg", "postgres", "postgresql", "kingbasees8")

    def require_database_url(self) -> str:
        if not self.DATABASE_URL:
            raise RuntimeError("Missing required env var: DATABASE_URL")
        return self.DATABASE_URL

    def require_knowledge_database_url(self) -> str:
        """URL for the PG + pgvector VECTOR store (kb_embedding only).

        Falls back to DATABASE_URL when KNOWLEDGE_DATABASE_URL is unset (e.g.
        a single-PG dev deployment), but refuses to use MySQL for the vector
        store (pgvector types are PG-only). Not needed when
        VECTOR_STORE_TYPE=es (reserved backend).
        """
        url = self.KNOWLEDGE_DATABASE_URL or self.DATABASE_URL
        if not url:
            raise RuntimeError("Missing required env var: KNOWLEDGE_DATABASE_URL")
        if url.startswith("mysql"):
            raise RuntimeError(
                "KNOWLEDGE_DATABASE_URL must be PostgreSQL (pgvector required), got mysql"
            )
        return url

    def effective_broker_url(self) -> str:
        """Resolve Celery broker URL: explicit env wins, fall back to REDIS_URL."""
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        return self.REDIS_URL or ""

    def effective_result_backend(self) -> str:
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        return self.REDIS_URL or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
