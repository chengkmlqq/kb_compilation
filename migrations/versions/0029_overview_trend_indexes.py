"""Add date-aggregation indexes for overview trend (首页总览趋势按天分桶).

背景（2026-10-20 首页总览看板 /overview/trend）：
- 趋势端点按 `DATE(kb_document.created_at)` / `DATE(doc_chunk.created_at)` /
  `DATE(wiki_page.created_at)` 分组统计「近 N 天新增」。三表仅有 kb_id 单列索引，
  按时间聚合会退化为「扫描可见 kb 全部行 + 函数计算日期」。
- 修复：补 (kb_id, created_at) 复合索引，使「先按 kb_id 收窄再按时间分组」可走索引，
  避免全量扫行。对已手动建过索引的库幂等跳过。

Revision ID: 0029_overview_trend_indexes
Revises: 0028_llm_usage
Create Date: 2026-10-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0029_overview_trend_indexes"
down_revision: Union[str, None] = "0028_llm_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def _index_exists(table: str, index: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return index in [i["name"] for i in insp.get_indexes(table)]


def upgrade() -> None:
    # 文档：按 kb_id 收窄 + created_at 分桶
    if _table_exists("kb_document") and not _index_exists("kb_document", "ix_kb_document_kb_created"):
        op.execute("CREATE INDEX ix_kb_document_kb_created ON kb_document (kb_id, created_at)")
    # 分块：doc_chunk 已有 idx_doc_chunk_kb(kb_id)，补 (kb_id, created_at)
    if _table_exists("doc_chunk") and not _index_exists("doc_chunk", "ix_doc_chunk_kb_created"):
        op.execute("CREATE INDEX ix_doc_chunk_kb_created ON doc_chunk (kb_id, created_at)")
    # Wiki 页：wiki_page 已有 idx_wiki_page_kb(kb_id)，补 (kb_id, created_at)
    if _table_exists("wiki_page") and not _index_exists("wiki_page", "ix_wiki_page_kb_created"):
        op.execute("CREATE INDEX ix_wiki_page_kb_created ON wiki_page (kb_id, created_at)")


def downgrade() -> None:
    if _table_exists("kb_document") and _index_exists("kb_document", "ix_kb_document_kb_created"):
        op.execute("DROP INDEX ix_kb_document_kb_created ON kb_document")
    if _table_exists("doc_chunk") and _index_exists("doc_chunk", "ix_doc_chunk_kb_created"):
        op.execute("DROP INDEX ix_doc_chunk_kb_created ON doc_chunk")
    if _table_exists("wiki_page") and _index_exists("wiki_page", "ix_wiki_page_kb_created"):
        op.execute("DROP INDEX ix_wiki_page_kb_created ON wiki_page")
