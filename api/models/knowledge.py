"""Knowledge-domain ORM models (PostgreSQL + pgvector store).

Naming follows the project convention — wiki / kb semantics, never synth.

Tables (all on KnowledgeBase => PG + pgvector):
- kb_datasource   — a knowledge base (a compiled corpus / wiki space)
- kb_document     — an uploaded/parsed document inside a knowledge base
- doc_chunk       — chunked document text + its embedding vector
- wiki_folder     — folder tree for wiki pages
- wiki_page       — generated wiki page (entity/concept/summary)
- wiki_link       — bidirectional wiki page links

The `embedding` column uses pgvector's vector type; the GIN full-text index on
wiki_page mirrors WeKnora's search setup. `indexing_strategy` mirrors WeKnora's
per-KB pipeline toggles (vector / keyword / wiki / graph).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from api.db import KnowledgeBase
from api.config import get_settings

_EMBEDDING_DIM = get_settings().EMBEDDING_DIM


# pgvector column type — importable only when the extension is available.
# We import lazily inside column creation to keep sqlite tests working without
# pgvector installed; for real PG runs the extension is required.
def _vector_type():
    from pgvector.sqlalchemy import Vector  # noqa: PLC0415

    return Vector(_EMBEDDING_DIM)


class KbDatasource(KnowledgeBase):
    """A knowledge base (compiled corpus / wiki space)."""

    __tablename__ = "kb_datasource"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    # team-level ownership (team_name from the framework store)
    team_name: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(64))

    # Pipeline toggles — mirrors WeKnora indexing_strategy:
    # {"vector_enabled": bool, "keyword_enabled": bool, "wiki_enabled": bool, "graph_enabled": bool}
    indexing_strategy: Mapped[dict] = mapped_column(JSON, default=dict)

    state: Mapped[str] = mapped_column(String(16), default="1")  # 1=active 0=deleted
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class KbDocument(KnowledgeBase):
    """A document inside a knowledge base (source file + parse status)."""

    __tablename__ = "kb_document"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("kb_datasource.id"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_ext: Mapped[str | None] = mapped_column(String(32))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    # storage ref into modo_sys_file (framework store)
    sys_file_id: Mapped[str | None] = mapped_column(String(64))
    storage_path: Mapped[str | None] = mapped_column(String(1000))

    parse_engine: Mapped[str | None] = mapped_column(String(64))  # builtin/markitdown/...
    parse_state: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING/PARSING/EMBEDDING/READY/FAILED
    parse_error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class DocChunk(KnowledgeBase):
    """Chunked document text with its embedding vector (pgvector)."""

    __tablename__ = "doc_chunk"
    __table_args__ = (
        Index("idx_doc_chunk_doc_seq", "document_id", "seq"),
        Index("idx_doc_chunk_kb", "kb_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("kb_document.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # page/section metadata for citation
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    # pgvector embedding column
    embedding = mapped_column(_vector_type(), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class WikiFolder(KnowledgeBase):
    """Folder tree for wiki pages."""

    __tablename__ = "wiki_folder"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(64), default="")
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class WikiPage(KnowledgeBase):
    """A wiki page generated from document knowledge (entity/concept/summary)."""

    __tablename__ = "wiki_page"
    __table_args__ = (
        # NOTE: the GIN full-text index (to_tsvector) is created by
        # scripts/knowledge_schema.sql, not here — it is PG-only and would
        # break create_all on sqlite (used by unit tests).
        Index("idx_wiki_page_kb", "kb_id"),
        Index("idx_wiki_page_slug", "kb_id", "slug"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), default="entity")  # entity/concept/summary
    content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str | None] = mapped_column(Text)
    # JSON array of source document ids used to build this page
    source_refs: Mapped[list] = mapped_column(JSONB, default=list)
    folder_id: Mapped[str | None] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active/draft/archived
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class WikiLink(KnowledgeBase):
    """Bidirectional link between wiki pages."""

    __tablename__ = "wiki_link"
    __table_args__ = (Index("idx_wiki_link_from", "from_page_id"), Index("idx_wiki_link_to", "to_page_id"))

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_page_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("wiki_page.id"), nullable=False
    )
    to_page_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("wiki_page.id"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(String(32), default="related")  # related/see-also/tag
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class KbAgent(KnowledgeBase):
    """An AI agent (QA assistant) bound to knowledge bases.

    Config JSON structure mirrors WeKnora's CustomAgentConfig: agent_mode
    (quick-answer / smart-reasoning), system_prompt, model settings,
    KB selection mode, allowed tools, retrieval flags.
    """

    __tablename__ = "kb_agent"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    avatar: Mapped[str | None] = mapped_column(String(64))
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # team-level ownership (team_name from the framework store)
    team_name: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    # JSON config, see KbAgentConfig defaults in api/services/agents.py
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="1")  # 1=active 0=deleted
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


def default_indexing_strategy() -> dict:
    """Default pipeline toggles: all enabled (matches WeKnora backfill)."""
    return {
        "vector_enabled": True,
        "keyword_enabled": True,
        "wiki_enabled": True,
        "graph_enabled": True,
    }


__all__ = [
    "KbDatasource",
    "KbDocument",
    "DocChunk",
    "WikiFolder",
    "WikiPage",
    "WikiLink",
    "KbAgent",
    "default_indexing_strategy",
]