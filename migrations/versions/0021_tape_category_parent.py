"""Add parent_id column on kb_tape_category (two-level category tree).

背景（2026-10-14）：编排管理分类树按「企业领域 → 文档类型」两级初始化
（19 个领域目录 + 各领域下文档类型子分类）。kb_tape_category 此前是单层
（无 parent 概念），补 parent_id 列支持两级；parent_id = 父分类的
category 名（与 Tape.category 的引用口径一致），一级分类为 NULL。

Revision ID: 0021_tape_category_parent
Revises: 0020_tape_category_descr
Create Date: 2026-10-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0021_tape_category_parent"
down_revision: Union[str, None] = "0020_tape_category_descr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _col_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    if not _table_exists("kb_tape_category"):
        return
    if not _col_exists("kb_tape_category", "parent_id"):
        op.add_column(
            "kb_tape_category",
            sa.Column("parent_id", sa.String(length=64), nullable=True),
        )
        op.create_index(
            "ix_kb_tape_category_parent_id", "kb_tape_category", ["parent_id"]
        )


def downgrade() -> None:
    if _table_exists("kb_tape_category") and _col_exists("kb_tape_category", "parent_id"):
        op.drop_index("ix_kb_tape_category_parent_id", table_name="kb_tape_category")
        op.drop_column("kb_tape_category", "parent_id")