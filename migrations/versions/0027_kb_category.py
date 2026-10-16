"""Add kb_category table (知识库页左侧分类树实体，2026-10）。

背景：知识库一直只有自由文本 `kb_datasource.label`，左侧分类树由 label 去重派生，
没有分类实体 → 无法「新增空分类 / 重命名 / 删除」（删除后无归属的知识库也无处标记）。
本迁移新增 `kb_category`（name 唯一）持久化分类实体；分类树 = 本表行 ∪ 可见知识库
的 label，按 name 合并（见 api/routers/kb_categories.py）。

Revision ID: 0027_kb_category
Revises: 0026_quality_run_sort_index
Create Date: 2026-10-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_kb_category"
down_revision: Union[str, None] = "0026_quality_run_sort_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "kb_category" in inspector.get_table_names():
        return  # 幂等：已建过（热修/重复执行）直接跳过
    op.create_table(
        "kb_category",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("name", name="uq_kb_category_name"),
    )


def downgrade() -> None:
    op.drop_table("kb_category")
