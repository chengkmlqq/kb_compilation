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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
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
    type: Mapped[str] = mapped_column(String(32), default="document")  # document（问答对型已下线）
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
    # FAQ 知识库配置（type=faq 时生效；2026-10-15 问答对模式下线，列保留兼容存量）
    faq_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 文档型 KB 的问题生成配置
    question_generation_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Wiki 配置：合成模型/抽取粒度/内容指令/skill（KB 级绑定构建技能）
    wiki_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 存储后端 / 向量存储绑定（provider 选择 + 具体实例）
    storage_provider_config: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_backend_id: Mapped[str | None] = mapped_column(String(36))
    vector_store_id: Mapped[str | None] = mapped_column(String(36))
    # 构建引擎（2026-10-12）：绑定「服务编排」中已发布的编排程序（kb_tape.id）。
    # 绑定后文档解析成功自动按该编排构建 wiki（空 = 使用内置 wiki 构建链）。
    build_tape_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # 上传摘要策略（2026-10-13）：off(默认不生成) | auto(按规模+历史摘要路由) |
    # stuff | mapreduce | refine(指定模式强制用该编排)。路由实现见
    # worker/tasks/doc_process.py::_resolve_summary_tape。
    summary_strategy: Mapped[str] = mapped_column(String(16), default="auto")

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
    # 上传时文件级处理配置（对齐 WeKnora process_config：JSON 字符串，含
    # chunking 切片配置 / parser_engine_rules 等，覆盖 KB 级默认；空=用 KB 配置）
    process_config: Mapped[str | None] = mapped_column(Text)
    # 文档摘要（2026-10-14 起由摘要编排 Stuff/Map-Reduce/Refine 的
    # kb_write_doc_summary 原子写回；直连 LLM 链路已移除）
    summary: Mapped[str | None] = mapped_column(Text)
    # ""（未生成）/READY/FAILED
    summary_status: Mapped[str] = mapped_column(String(16), default="")
    summary_error: Mapped[str | None] = mapped_column(Text)
    # 文档目录（doc_folder.id，"" = KB 根层级）。单归属：一个文档只能在一个目录。
    folder_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # 知识源类型：file=文件（默认，现状）/ table=数据源维度表（表→wiki，2026-10-10）
    source_type: Mapped[str] = mapped_column(String(16), default="file", index=True)
    # 文档级构建引擎（2026-10-12）：上传/选表时可重选构建服务覆盖知识库默认
    # （kb_datasource.build_tape_id）。空 = 用 KB 默认构建引擎。
    build_tape_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # table 源专属：数据源分类名（dsName）/ 表所在 schema / 表名 / 采集快照行数
    ds_source_name: Mapped[str | None] = mapped_column(String(64))
    ds_table_schema: Mapped[str | None] = mapped_column(String(128))
    ds_table_name: Mapped[str | None] = mapped_column(String(256))
    row_count: Mapped[int | None] = mapped_column(BigInteger)

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
    # content 用 Text 变体：MySQL 落 MEDIUMTEXT（16MB），其余方言回退 Text。
    # 2026-10-12 扩列：xlsx 等大表格文本的超长块（实测 78687 字符）曾撞
    # TEXT 64KB 字节上限导致入库失败；MEDIUMTEXT 作为容量兜底（切片硬上限
    # 兜底在前，见 chunking_orch.HARD_MAX_CHARS）。
    content: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT(length=16 * 1024 * 1024), "mysql"),
        nullable=False,
    )
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


class DocFolder(Base):
    """Multi-level document directory tree (single placement).

    One document belongs to exactly one folder (kb_document.folder_id, ""
    = KB root). parent_id forms an adjacency-list tree ("" = root) so any
    depth is supported. Decisions (2026-10-10): single placement, recursive
    subtree browsing, non-empty folders cannot be deleted, browsing-only
    (does not affect retrieval), upload can target a folder.
    """

    __tablename__ = "doc_folder"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(64), default="")
    created_by: Mapped[str | None] = mapped_column(String(64))
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

    folder_ids: Mapped[list] = mapped_column(JSON, default=list)


class WikiPageFolder(Base):
    """多目录挂载关联表（对齐 WeKnora wiki_page_folders）：页面 ↔ 目录 多对多。

    2026-10-13 供管技能移植新增——跨文档合并实体/关键词挂多个本体目录的核心机制。
    主目录仍存 wiki_page.folder_id；本表记录全部挂载（含主目录）。
    """

    __tablename__ = "wiki_page_folder"
    __table_args__ = (
        Index("idx_wiki_page_folder_page", "page_id"),
        Index("idx_wiki_page_folder_folder", "folder_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    folder_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
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


class WikiPageIssue(Base):
    """Wiki 页面问题（lint 检出的健康问题，可跟踪/处理）。

    对齐 WeKnora wiki_page_issues（persistent 表）：
    - issue_type: empty_content / orphan / broken_link / ...
    - status: pending / ignored / resolved（三态状态机，WeKnora 语义）
    """

    __tablename__ = "wiki_page_issue"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    slug: Mapped[str | None] = mapped_column(String(255), default="")
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/ignored/resolved
    reported_by: Mapped[str | None] = mapped_column(String(64), default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )


class QualityRun(Base):
    """文档构建质量评估任务（质量评估编排 v1，2026-10-13）。

    scope=single|multi|all；三维度指标 + 问题清单 + 规则归因，供质量评估页面
    查询/汇总。编排汇总节点落库。
    """

    __tablename__ = "kb_quality_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), default="single")  # single|multi|all
    doc_ids: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|success|failed
    chunk_metrics: Mapped[dict | None] = mapped_column(JSON)
    wiki_metrics: Mapped[dict | None] = mapped_column(JSON)
    graph_metrics: Mapped[dict | None] = mapped_column(JSON)
    issues: Mapped[list | None] = mapped_column(JSON)
    attribution: Mapped[list | None] = mapped_column(JSON)  # [{dim, cause, suggestion}]
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )


class QualityReport(Base):
    """质量评估维度报告（kb_quality_report）：每维度一条，页面按 run 聚合。

    dim=chunk|wiki|graph；metrics 为该维度指标，issues 为该维度问题清单。
    """

    __tablename__ = "kb_quality_report"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_id: Mapped[str | None] = mapped_column(String(64))
    dim: Mapped[str] = mapped_column(String(32), nullable=False)  # chunk|wiki|graph
    metrics: Mapped[dict | None] = mapped_column(JSON)
    issues: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class KbCategory(Base):
    """知识库分类（知识库页左侧分类树实体，2026-10）。

    分类以 name 关联知识库的 label（KB.label == 分类名 = 归属该分类）；本表只
    持久化分类实体本身（含暂无知识库归属的「空分类」）—— 分类树 = 本表行 ∪
    可见知识库的 label，按 name 合并。
    """

    __tablename__ = "kb_category"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
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


class LlmUsageRecord(Base):
    """LLM 调用使用记录（2026-10-16 LLM 使用看板数据源）。

    一次逻辑调用一条记录（重试收敛在一条里，retries 计数）；wiki 构建管线
    （worker/builtin_engine weknora_rpc.llm_call）、智能问答（chat.py 流式）、
    agent 任务三处埋点写入，best-effort 写库不阻断业务。字段全部可移植
    SQL 类型，与业务表同存 framework store（跨 MySQL/PG 通用）。

    status 取值：success / error / timeout / context_overflow / running。
    cache_read = 缓存命中（DeepSeek 系 usage.prompt_cache_hit_tokens 或
    prompt_tokens_details.cached_tokens）；cache_creation = 缓存写入（多数
    上游不暴露，记 0）。ttft_ms 仅流式问答有值（首字耗时）；wiki 构建非流式
    记 NULL。
    """

    __tablename__ = "kb_llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 请求级 trace id（req_xxx）；wiki 构建场景 = job_id
    trace_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    ts: Mapped[datetime | None] = mapped_column(
        DateTime, index=True, server_default=text("CURRENT_TIMESTAMP")
    )
    source: Mapped[str] = mapped_column(String(16), default="")  # chat/wiki_build/agent
    kb_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kb_name: Mapped[str | None] = mapped_column(String(128))
    doc_id: Mapped[str | None] = mapped_column(String(64))
    doc_name: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    # 业务动作 key（OP_TYPES 映射表见 api/services/llm_usage.py）；phase 保留原始构建阶段名
    operation_type: Mapped[str | None] = mapped_column(String(32), index=True)
    phase: Mapped[str | None] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[float | None] = mapped_column(Float)  # 首字耗时（流式）
    total_ms: Mapped[int | None] = mapped_column(Integer)  # 端到端耗时
    tps: Mapped[float | None] = mapped_column(Float)  # 输出 TPS = output/(total-ttft)
    status: Mapped[str] = mapped_column(String(24), default="success", index=True)
    error_msg: Mapped[str | None] = mapped_column(Text, default="")
    retries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


__all__ = [
    "KbAgent",
    "KbDatasource",
    "KbDocument",
    "KbEmbedding",
    "DocChunk",
    "WikiFolder",
    "WikiPage",
    "WikiLink",
    "LlmUsageRecord",
    "default_indexing_strategy",
]
