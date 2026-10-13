"""Add category column on kb_tape (orchestration category tree).

背景（2026-10-13）：编排管理页改左右结构——左分类树 + 右编排列表，
分类可配置（表单 Select 支持输入新分类，聚合自 kb_tape.category）。
TapeTemplate 已有同名字段（0014 之前即存在），Tape 本次补齐。

Revision ID: 0016_tape_category
Revises: 0015_summary_strategy
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0016_tape_category"
down_revision: Union[str, None] = "0015_summary_strategy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_tape" not in insp.get_table_names():
        return
    if not _column_exists("kb_tape", "category"):
        op.add_column(
            "kb_tape",
            sa.Column("category", sa.String(length=64), nullable=False, server_default="通用"),
        )
        op.create_index("ix_kb_tape_category", "kb_tape", ["category"])


def downgrade() -> None:
    if _column_exists("kb_tape", "category"):
        op.drop_index("ix_kb_tape_category", table_name="kb_tape")
        op.drop_column("kb_tape", "category")
