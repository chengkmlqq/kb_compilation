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
from contextvars import ContextVar
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
    """按环境解析框架库 URL（dev 未配 / 总开关关 → 回落 prod）。"""
    settings = get_settings()
    if env == ENV_DEV and settings.KB_ENV_ROUTING_ENABLED and settings.DEV_DATABASE_URL:
        return settings.DEV_DATABASE_URL
    return settings.require_database_url()


def _resolve_kb_url(env: str) -> tuple[str, str]:
    """按环境解析向量库 (url, schema_name)：开发用独立 schema（同一 PG 实例）。"""
    settings = get_settings()
    if env == ENV_DEV and settings.KB_ENV_ROUTING_ENABLED and settings.DEV_KNOWLEDGE_DATABASE_URL:
        return settings.DEV_KNOWLEDGE_DATABASE_URL, settings.DEV_SCHEMA_NAME or "dev"
    return settings.require_knowledge_database_url(), settings.SCHEMA_NAME


@lru_cache
def _framework_engine_for(url: str) -> Engine:
    """框架库引擎（按 URL 缓存——每个环境独立连接池）。"""
    settings = get_settings()
    return create_engine(
        url,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_MAX_CONNECTIONS,
        max_overflow=0,
        future=True,
    )


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
    """FastAPI dependency: yield a scoped framework session."""
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
