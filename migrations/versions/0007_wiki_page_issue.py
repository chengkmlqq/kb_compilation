"""Add wiki_page_issue table (wiki lint issues with status workflow).

背景（对齐 WeKnora wiki_page_issues，2026-10-11）：
- WeKnora 的 wiki lint 会把检出问题持久化到 wiki_page_issues（三态
  pending/ignored/resolved），MCP 工具 wiki_list_issues /
  wiki_update_issue_status 读这张表。
- kb_compilation 此前 lint 是即时计算的（wiki_lint 不落库），为了 MCP
  工具对齐补持久化表：lint 运行时可把问题写入，MCP 工具可按 slug/status
  过滤查询、更新状态。

本迁移幂等：表不存在则建，已存在 no-op。auto-migrate 启动时自动执行。

Revision ID: 0007_wiki_page_issue
Revises: 0006_doc_table_source
Create Date: 2026-10-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007_wiki_page_issue"
down_revision: Union[str, None] = "0006_doc_table_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.has_table(op.get_bind(), "wiki_page_issue"):
        return
    op.create_table(
        "wiki_page_issue",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("issue_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("reported_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("wiki_page_issue")