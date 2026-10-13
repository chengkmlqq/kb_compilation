"""多目录挂载：wiki_page_folder 关联表 + wiki_page.folder_ids 列（对齐 WeKnora）。

需求（2026-10-13 供管技能移植）：跨文档合并实体/关键词需挂多个本体分类目录。
- wiki_page.folder_id 保留为主目录（不破坏既有逻辑/前端树）。
- wiki_page.folder_ids JSON 冗余列 = 全部挂载（含主目录），服务层读写方便。
- wiki_page_folder 关联表 = 真值（页面↔目录多对多，folder 维度过滤用）。

幂等：表不存在才建；缺列才 ALTER。auto-migrate 启动时自动执行。

Revision ID: 0015_wiki_page_folders
Revises: 0014_chat_message_feedback
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_wiki_page_folders"
down_revision: Union[str, None] = "0014_chat_message_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "wiki_page_folder" not in insp.get_table_names():
        op.create_table(
            "wiki_page_folder",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("kb_id", sa.String(64), nullable=False, index=True),
            sa.Column("page_id", sa.String(64), nullable=False),
            sa.Column("folder_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("idx_wiki_page_folder_page", "wiki_page_folder", ["page_id"])
        op.create_index("idx_wiki_page_folder_folder", "wiki_page_folder", ["folder_id"])
    cols = [c["name"] for c in insp.get_columns("wiki_page")]
    if "folder_ids" not in cols:
        op.add_column(
            "wiki_page",
            sa.Column("folder_ids", sa.JSON, nullable=True),
        )


def downgrade() -> None:
    op.drop_index("idx_wiki_page_folder_folder", table_name="wiki_page_folder")
    op.drop_index("idx_wiki_page_folder_page", table_name="wiki_page_folder")
    op.drop_table("wiki_page_folder")
    op.drop_column("wiki_page", "folder_ids")