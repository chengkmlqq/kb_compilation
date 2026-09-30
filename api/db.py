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
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for framework-layer ORM models (shared legacy DB)."""


class KnowledgeBase(DeclarativeBase):
    """Declarative base for the vector-only store (PG + pgvector, kb_embedding)."""


@lru_cache
def get_engine() -> Engine:
    """Lazily create the framework engine (pool sized like the source)."""
    settings = get_settings()
    return create_engine(
        settings.require_database_url(),
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_MAX_CONNECTIONS,
        max_overflow=0,
        future=True,
    )


@lru_cache
def get_knowledge_engine() -> Engine:
    """Lazily create the vector-store engine (PostgreSQL + pgvector).

    Only `kb_embedding` (chunk embeddings) lives here; the knowledge business
    tables live on the framework engine. SCHEMA_NAME is injected into the
    connection's search_path so tables in a non-public schema resolve without
    per-query SET (same for production and the temp-schema verification path).
    """
    settings = get_settings()
    connect_args: dict = {}
    if settings.SCHEMA_NAME and settings.SCHEMA_NAME != "public":
        # psycopg2: options="-c search_path=x,public" applies per-connection.
        connect_args["options"] = f"-csearch_path={settings.SCHEMA_NAME},public"
    return create_engine(
        settings.require_knowledge_database_url(),
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_MAX_CONNECTIONS,
        max_overflow=0,
        connect_args=connect_args,
        future=True,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@lru_cache
def get_knowledge_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_knowledge_engine(), autoflush=False, expire_on_commit=False
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
def session_scope() -> Iterator[Session]:
    """Celery / script helper: transactional framework session."""
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def knowledge_session_scope() -> Iterator[Session]:
    """Celery / script helper: transactional knowledge-store session."""
    db = get_knowledge_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
