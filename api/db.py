"""SQLAlchemy engine + session management.

Two stores:
- Base (framework): shares the legacy database (mysql or pg, per DATABASE_URL)
- KnowledgeBase (knowledge domain): PostgreSQL + pgvector store for the
  wiki/kb tables (doc_chunk embeddings, wiki_pages, graph refs)

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
    """Declarative base for knowledge-domain models (PG + pgvector store)."""


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
    """Lazily create the knowledge-store engine (PostgreSQL + pgvector).

    SCHEMA_NAME is injected into the connection's search_path so tables in a
    non-public schema resolve without per-query SET (same for production and
    the temp-schema verification path).
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
