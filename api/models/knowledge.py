"""Knowledge-domain ORM models.

Storage split (multi-database, cross-dialect):
- Business tables (kb_datasource / kb_document / doc_chunk / wiki_* / kb_agent)
  live on the FRAMEWORK store (MySQL or any relational DB) — they carry only
  portable SQLAlchemy types (String/Text/JSON/Integer/...), so the platform
  can switch to another relational database without SQL changes.
- Embedding vectors live in a SEPARATE vector store: `kb_embedding` on the
  knowledge engine (PostgreSQL + pgvector today). All vector I/O goes through
  api.services.vector_store.VectorStore, so the vector backend can later be
  swapped to Elasticsearch / Milvus / etc. without touching callers.

Note: the JSON columns use SQLAlchemy's generic JSON type (not PG JSONB) so
the business tables stay dialect-neutral.
"""

from __future__ import annotations

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

from api.db import Base, KnowledgeBase
from api.config import get_settings

_EMBEDDING_DIM = get_settings().EMBEDDING_DIM


# pgvector column type — importable only when the extension is available.
# We import lazily inside column creation to keep sqlite tests working without
# pgvector installed; for real PG runs the extension is required.
def _vector_type():
    from pgvector.sqlalchemy import Vector  # noqa: PLC0415

    return Vector(_EMBEDDING_DIM)


class KbDatasource(Base):
    """A knowledge base (compiled corpus / wiki space) — business store."""

    __tablename__ = "kb_datasource"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    # 三级权限：personal(owner_user_id) / team(team_name) / system(仅管理员)
    scope: Mapped[str | None] = mapped_column(String(16), default="system", index=True)
    # team-level ownership (team_name from the framework store)
    team_name: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    owner_team_name: Mapped[str | None] = mapped_column(String(64), index=True)

    # Pipeline toggles — mirrors WeKnora indexing_strategy:
    # {"vector_enabled": bool, "keyword_enabled": bool, "wiki_enabled": bool, "graph_enabled": bool}
    indexing_strategy: Mapped[dict] = mapped_column(JSON, default=dict)

    # ── WeKnora 对齐配置（2026-10）────────────────────────────────────────
    # 字段与 WeKnora internal/types/knowledgebase.go 的 KnowledgeBase 结构一一对应。
    type: Mapped[str] = mapped_column(String(32), default="document")  # document / faq
    # 自定义 Wiki 生成：开启后不自动构建，需手动触发（WeKnora custom_wiki_generation）
    custom_wiki_generation: Mapped[bool] = mapped_column(Boolean, default=False)
    # KB 级模型绑定（WeKnora embedding_model_id / summary_model_id）
    embedding_model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    summary_model_id: Mapped[str | None] = mapped_column(String(64))
    # VLM（图片理解）/ ASR（语音转写）/ 图片处理：KB 级配置
    vlm_config: Mapped[dict] = mapped_column(JSON, default=dict)
    asr_config: Mapped[dict] = mapped_column(JSON, default=dict)
    image_processing_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 知识图谱抽取配置（WeKnora extract_config / GraphSettings）
    extract_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # FAQ 知识库配置（type=faq 时生效）
    faq_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 文档型 KB 的问题生成配置
    question_generation_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Wiki 配置：合成模型/抽取粒度/内容指令/skill（KB 级绑定构建技能）
    wiki_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 存储后端 / 向量存储绑定（provider 选择 + 具体实例）
    storage_provider_config: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_backend_id: Mapped[str | None] = mapped_column(String(36))
    vector_store_id: Mapped[str | None] = mapped_column(String(36))

    state: Mapped[str] = mapped_column(String(16), default="1")  # 1=active 0=deleted
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class KbDocument(Base):
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
    # LLM 生成的文档摘要（对齐 WeKnora：知识库配置 summary_model_id 后上传文档自动/手动生成）
    summary: Mapped[str | None] = mapped_column(Text)
    # ""（未生成）/READY/FAILED
    summary_status: Mapped[str] = mapped_column(String(16), default="")
    summary_error: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class DocChunk(Base):
    """Chunked document text + metadata (NO vector — vector lives in kb_embedding).

    Splitting the embedding into a dedicated vector store keeps this table
    portable across relational databases and lets the vector backend swap
    (PG -> ES) independently.
    """

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
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class KbEmbedding(KnowledgeBase):
    """Vector-only table: chunk embedding vectors (pgvector today).

    One row per chunk. `chunk_id` mirrors doc_chunk.id in the business store.
    The table is created only on the knowledge engine (PG); callers never
    touch it directly — they use VectorStore (api.services.vector_store).
    """

    __tablename__ = "kb_embedding"
    __table_args__ = (
        Index("idx_kb_embedding_kb", "kb_id"),
        # NOTE: HNSW index created in scripts/knowledge_schema.sql (PG-only).
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    embedding = mapped_column(_vector_type(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class WikiFolder(Base):
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


class WikiPage(Base):
    """A wiki page generated from document knowledge (entity/concept/summary)."""

    __tablename__ = "wiki_page"
    __table_args__ = (
        # NOTE: PG full-text GIN index would be PG-only; we keep wiki search
        # application-side (ILIKE via the generic keyword arm), so the schema
        # stays portable across relational databases.
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
    source_refs: Mapped[list] = mapped_column(JSON, default=list)
    folder_id: Mapped[str | None] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active/draft/archived
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class WikiLink(Base):
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


class WikiOperationLog(Base):
    """Wiki 操作日志：页面/目录增删改、链接重建等管理动作审计。"""

    __tablename__ = "wiki_operation_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # page_create/page_update/page_delete/folder_create/...
    slug: Mapped[str | None] = mapped_column(String(255), default="")
    title: Mapped[str | None] = mapped_column(String(255), default="")
    detail: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(String(64), default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class WikiFeedback(Base):
    """页面反馈：用户对 wiki 页面的评价/问题上报。"""

    __tablename__ = "wiki_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    slug: Mapped[str | None] = mapped_column(String(255), default="")
    user_id: Mapped[str | None] = mapped_column(String(64), default="")
    feedback_type: Mapped[str] = mapped_column(String(32), default="issue")  # helpful/issue
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open/resolved/ignored
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class KbAgent(Base):
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
    config: Mapped[dict] = mapped_column(JSON, default=dict)
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
    "KbAgent",
    "KbDatasource",
    "KbDocument",
    "KbEmbedding",
    "DocChunk",
    "WikiFolder",
    "WikiPage",
    "WikiLink",
    "default_indexing_strategy",
]
