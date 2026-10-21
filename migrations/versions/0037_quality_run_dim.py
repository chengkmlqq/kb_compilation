"""Add dim + doc_metrics columns to kb_quality_run for single-dimension assessments.

背景（2026-10-21 单维度评估拆分）：原「质量评估-QA」编排三维度（切片/wiki/图数据）
合一跑。现新增三个独立评估功能（文档转换评估/切片评估/图数据评估），各自走独立
编排、只落一个维度。kb_quality_run 需要：
- dim 列：标识本次评估维度（all=三维度合一旧行为 / doc=文档转换 / chunk=切片 /
  graph=图数据），供列表按维度过滤、页面按维度渲染。
- doc_metrics 列：文档转换评估指标（文档解析/转换质量），其余维度沿用既有
  chunk_metrics/wiki_metrics/graph_metrics。

迁移幂等：列已存在则跳过（跨环境兼容，dev 双表由 create_all 补建）。

Revision ID: 0037_quality_run_dim
Revises: 0036_missing_indexes
Create Date: 2026-10-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0037_quality_run_dim"
down_revision: Union[str, None] = "0036_missing_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_quality_run" not in set(insp.get_table_names()):
        return
    if not _column_exists(bind, "kb_quality_run", "dim"):
        op.add_column(
            "kb_quality_run",
            sa.Column("dim", sa.String(32), nullable=False, server_default="all"),
        )
    if not _column_exists(bind, "kb_quality_run", "doc_metrics"):
        op.add_column(
            "kb_quality_run",
            sa.Column("doc_metrics", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_quality_run" not in set(insp.get_table_names()):
        return
    if _column_exists(bind, "kb_quality_run", "doc_metrics"):
        op.drop_column("kb_quality_run", "doc_metrics")
    if _column_exists(bind, "kb_quality_run", "dim"):
        op.drop_column("kb_quality_run", "dim")
