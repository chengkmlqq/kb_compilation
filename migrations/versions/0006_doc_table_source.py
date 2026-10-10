"""Add table-source columns to kb_document (table → wiki, 2026-10-10).

背景（表→wiki 功能）：
- 知识库此前只支持文件作为知识源（上传 → 切片 → 向量 → wiki）。
- 本次让知识库支持「从数据源选一张维度表」：表像文件一样成为知识库资源
  （表卡片：数据量/汇总摘要/样例数据），表数据进入向量化切片，wiki 构建
  用表数据构建（表主页）。
- KbDocument 新增：source_type（file|table）、ds_source_name（数据源分类名
  dsName）、ds_table_schema、ds_table_name、row_count（采集快照行数）。

本迁移：对已存在的 kb_document 检查列，缺则逐个 ALTER 补上。幂等，
表不存在或列已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0006_doc_table_source
Revises: 0005_doc_process_config
Create Date: 2026-10-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_doc_table_source"
down_revision: Union[str, None] = "0005_doc_process_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = [
    ("source_type", sa.String(16)),
    ("ds_source_name", sa.String(64)),
    ("ds_table_schema", sa.String(128)),
    ("ds_table_name", sa.String(256)),
    ("row_count", sa.BigInteger()),
]


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_document" not in insp.get_table_names():
        return
    for col_name, col_type in _COLUMNS:
        if not _column_exists("kb_document", col_name):
            op.add_column("kb_document", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    for col_name, _ in _COLUMNS:
        if _column_exists("kb_document", col_name):
            op.drop_column("kb_document", col_name)
