"""Add descr column on kb_tape_category (category description).

背景（2026-10-14）：kb_tape_category 独立表（0019）建表时只有 label（显示名），
无描述字段；分类管理/树 tooltip 需要用途说明，故补 descr 列（长文本）。

Revision ID: 0020_tape_category_descr
Revises: 0019_tape_category_table
Create Date: 2026-10-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0020_tape_category_descr"
down_revision: Union[str, None] = "0019_tape_category_table"
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
    if not _col_exists("kb_tape_category", "descr"):
        op.add_column(
            "kb_tape_category",
            sa.Column("descr", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    if _table_exists("kb_tape_category") and _col_exists("kb_tape_category", "descr"):
        op.drop_column("kb_tape_category", "descr")