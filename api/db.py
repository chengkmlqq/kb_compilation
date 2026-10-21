"""SQLAlchemy engine + session management.

Two stores:
- Base (framework): shares the legacy database (mysql or pg, per DATABASE_URL)
  and hosts BOTH the framework tables (modo_*) and the knowledge BUSINESS
  tables (kb_datasource / kb_document / doc_chunk / wiki_* / kb_agent) — all
  portable relational types, so the platform can switch relational DBs.
- KnowledgeBase (vector store): PostgreSQL + pgvector for the single
  vector-only table `kb_embedding`. All vector I/O goes through
  api.services.vector_store.VectorStore, so the vector backend can later be
  swapped to Elasticsearch / Milvus without touching callers.

Both engines are created lazily so the app boots (and tests run) without a
live database.

Environment routing (2026-10-12):
- dev / prod 双环境：编排执行（手工/设计器/定时）→ dev；知识库上传触发链路 → prod。
- contextvar `_ENV` 标记当前环境（默认 prod）；`env_scope(env)` 上下文管理器设置。
- 所有现有 `get_sessionmaker()` / `session_scope()` 调用**零改动**——内部读
  contextvar 解析环境 URL（未配置 DEV_* 或总开关关闭时回落 prod）。
- engine/sessionmaker 按 URL lru_cache，每个环境一套连接池。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.config import get_settings

# 环境常量与 contextvar
ENV_PROD = "prod"
ENV_DEV = "dev"

_ENV: ContextVar[str] = ContextVar("kb_env", default=ENV_PROD)

# 2026-10-21 请求级会话复用：rbac_guard 中间件创建的 session 写入本 contextvar，
# 端点 get_db 优先复用（省一次远程库连接建立 ~100ms）。token 供中间件恢复。
_REQUEST_DB: ContextVar[Session | None] = ContextVar("kb_request_db", default=None)
_REQUEST_DB_TOKEN: ContextVar[Token | None] = ContextVar(
    "kb_request_db_token", default=None
)


def set_request_db(db: Session | None) -> None:
    """中间件入口调用：把会话挂到请求上下文供 get_db 复用。"""
    tok = _REQUEST_DB.set(db)
    _REQUEST_DB_TOKEN.set(tok)


def reset_request_db() -> None:
    """中间件结束调用：恢复 contextvar（异常路径防泄漏）。"""
    tok = _REQUEST_DB_TOKEN.get()
    if tok is not None:
        try:
            _REQUEST_DB.reset(tok)
        except ValueError:  # noqa: BLE001 — token 已失效（嵌套覆盖）时忽略
            pass
        _REQUEST_DB_TOKEN.set(None)


def current_env() -> str:
    """当前执行环境（prod/dev）。"""
    return _ENV.get()


@contextmanager
def env_scope(env: str) -> Iterator[None]:
    """在上下文内设置执行环境（celery 任务/编排节点入口调用）。

    用法：
        with env_scope("dev"):
            session_scope()  # 连开发库
    """
    token = _ENV.set(env)
    try:
        yield
    finally:
        _ENV.reset(token)


class Base(DeclarativeBase):
    """Declarative base for framework-layer ORM models (shared legacy DB)."""


class KnowledgeBase(DeclarativeBase):
    """Declarative base for the vector-only store (PG + pgvector, kb_embedding)."""


def _resolve_db_url(env: str) -> str:
    """按环境解析框架库 URL。

    2026-10-20 双表改造：废弃双库路由（DEV_DATABASE_URL 独立库）——dev/prod
    全部使用同一个 DATABASE_URL（data_supply），环境隔离改为同库双表
    （见 api/services/env_table_router.py，dev 环境自动路由到 xxx_dev 表）。
    保留 env 参数仅为兼容调用签名；dev 环境也返回生产 URL。
    """
    settings = get_settings()
    return settings.require_database_url()


def _resolve_kb_url(env: str) -> tuple[str, str]:
    """按环境解析向量库 (url, schema_name)：开发用独立 schema（同一 PG 实例）。

    2026-10-20 双表改造：向量库 kb_embedding 不在隔离名单（编排测试执行
    不直接写向量表；向量经 VectorStore 服务层），dev 同样回落生产 URL，
    不再切 schema/dev 库。
    """
    settings = get_settings()
    return settings.require_knowledge_database_url(), settings.SCHEMA_NAME


@lru_cache
def _framework_engine_for(url: str) -> Engine:
    """框架库引擎（按 URL 缓存——dev/prod 双表改造后同一 URL，单连接池）。"""
    settings = get_settings()
    engine = create_engine(
        url,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_MAX_CONNECTIONS,
        max_overflow=0,
        future=True,
    )
    # 2026-10-20 双表改造：dev 环境（编排测试执行）自动把隔离表路由到
    # xxx_dev 表。挂在框架引擎上，一次安装全部服务共享。
    from api.services.env_table_router import install_dev_table_router

    install_dev_table_router(engine)
    return engine

@lru_cache
def _knowledge_engine_for(url: str, schema: str) -> Engine:
    """向量库引擎（按 URL+schema 缓存；开发 schema 注入 search_path）。"""
    settings = get_settings()
    connect_args: dict = {}
    if schema and schema != "public":
        connect_args["options"] = f"-csearch_path={schema},public"
    return create_engine(
        url,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_MAX_CONNECTIONS,
        max_overflow=0,
        connect_args=connect_args,
        future=True,
    )


def get_engine(env: Optional[str] = None) -> Engine:
    """Lazily create the framework engine for the current (or given) env."""
    env = env or current_env()
    return _framework_engine_for(_resolve_db_url(env))


def get_knowledge_engine(env: Optional[str] = None) -> Engine:
    """Lazily create the vector-store engine for the current (or given) env."""
    env = env or current_env()
    url, schema = _resolve_kb_url(env)
    return _knowledge_engine_for(url, schema)


def get_sessionmaker(env: Optional[str] = None) -> sessionmaker[Session]:
    """Framework sessionmaker for the current (or given) env.

    ⚠️ 不缓存：无参调用时 env 从 contextvar 解析，若 lru_cache 会按 None 缓存
    首次解析的环境，后续切环境(session_scope(env))仍命中旧缓存——engine 层
    已按 URL 缓存(连接池复用)，sessionmaker 新建开销可忽略。

    2026-10-20 双表改造：dev 环境（编排测试执行）的表路由由框架引擎的
    before_cursor_execute 事件实现（见 env_table_router），session 无需特殊类。
    """
    env = env or current_env()
    return sessionmaker(bind=get_engine(env), autoflush=False, expire_on_commit=False)


def get_knowledge_sessionmaker(env: Optional[str] = None) -> sessionmaker[Session]:
    """Knowledge-store sessionmaker for the current (or given) env."""
    env = env or current_env()
    return sessionmaker(
        bind=get_knowledge_engine(env), autoflush=False, expire_on_commit=False
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a scoped framework session.

    2026-10-21 连接复用：rbac_guard 中间件创建的 session 写入 _REQUEST_DB
    contextvar，端点 get_db 优先复用同一连接（省一次远程库连接建立 ~100ms）；
    contextvar 为空（脚本/测试直调、中间件未挂载）时新建。
    """
    db = _REQUEST_DB.get()
    if db is not None:
        try:
            yield db
        finally:
            # 复用中间件连接：端点用完即归还（close 后连接回池），
            # 中间件放行路径不再 close（生命周期交给端点）。
            db.close()
        return
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def get_knowledge_db() -> Iterator[Session]:
    """FastAPI dependency: yield a scoped knowledge-store session."""
    db = get_knowledge_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope(env: Optional[str] = None) -> Iterator[Session]:
    """Celery / script helper: transactional framework session."""
    db = get_sessionmaker(env)()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def knowledge_session_scope(env: Optional[str] = None) -> Iterator[Session]:
    """Celery / script helper: transactional knowledge-store session."""
    db = get_knowledge_sessionmaker(env)()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
