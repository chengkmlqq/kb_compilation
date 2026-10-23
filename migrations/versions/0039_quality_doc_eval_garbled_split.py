"""Add file_name_garbled / content_garbled columns to kb_quality_doc_eval.

背景（2026-10-23 乱码列拆分）：页面乱码列由单一「乱码」拆分为「标题乱码」与
「内容乱码」两列。kb_quality_doc_eval 需要两个独立布尔：
- file_name_garbled：文件名本身乱码（编码错位文件名）
- content_garbled：转换后 MD 文本乱码
garbled 保留为「任一乱码」的兼容列（页面/接口仍可用）。

迁移幂等：列已存在则跳过（跨环境兼容，dev 双表由 create_all 补建）。

Revision ID: 0039_quality_doc_eval_garbled_split
Revises: 0038_quality_doc_eval
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0039_quality_doc_eval_garbled_split"
down_revision: Union[str, None] = "0038_quality_doc_eval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_quality_doc_eval" not in set(insp.get_table_names()):
        return
    if not _column_exists(bind, "kb_quality_doc_eval", "file_name_garbled"):
        op.add_column(
            "kb_quality_doc_eval",
            sa.Column("file_name_garbled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if not _column_exists(bind, "kb_quality_doc_eval", "content_garbled"):
        op.add_column(
            "kb_quality_doc_eval",
            sa.Column("content_garbled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    # 回填：既有 garbled=1 行的归属（文件名乱码 issue 存在则标 file_name_garbled）
    # MySQL 无 UPDATE ... FROM，用子查询分批置位（兼容 PG 亦可）。
    op.execute(
        "UPDATE kb_quality_doc_eval SET file_name_garbled = 1 WHERE garbled = 1 "
        "AND (file_name LIKE '%╓%' OR file_name LIKE '%鎴%' OR file_name LIKE '%╞%' OR file_name LIKE '%╩%' OR file_name LIKE '%╠%' OR file_name LIKE '%φ%' OR file_name LIKE '%¿%')"
    )
    op.execute(
        "UPDATE kb_quality_doc_eval SET content_garbled = 1 WHERE garbled = 1 AND file_name_garbled = 0"
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_quality_doc_eval" not in set(insp.get_table_names()):
        return
    if _column_exists(bind, "kb_quality_doc_eval", "content_garbled"):
        op.drop_column("kb_quality_doc_eval", "content_garbled")
    if _column_exists(bind, "kb_quality_doc_eval", "file_name_garbled"):
        op.drop_column("kb_quality_doc_eval", "file_name_garbled")
