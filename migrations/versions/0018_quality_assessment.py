"""Add quality assessment tables (kb_quality_run + kb_quality_report).

背景（2026-10-13 质量评估编排 v1）：
- 用服务编排做「文档构建质量评估」：切片质量 / wiki 质量 / 图数据质量
  三类检查，结果落库供后续质量评估页面查询与汇总。
- kb_quality_run：一次评估任务（scope=单文档/多文档/整库，三维度指标，
  规则归因与优化建议）。
- kb_quality_report：评估详情（每文档的三维度问题清单，供页面抽看/汇总）。

幂等：表不存在才建。auto-migrate 启动时自动执行。

Revision ID: 0018_quality_assessment
Revises: 0017_merge
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018_quality_assessment"
down_revision: Union[str, None] = "0017_merge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.has_table(bind, "kb_quality_run"):
        return
    op.create_table(
        "kb_quality_run",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False, server_default="single"),  # single|multi|all
        sa.Column("doc_ids", sa.JSON(), nullable=True),  # 多文档清单
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),  # queued|running|success|failed
        sa.Column("chunk_metrics", sa.JSON(), nullable=True),   # 切片质量指标
        sa.Column("wiki_metrics", sa.JSON(), nullable=True),    # wiki 质量指标
        sa.Column("graph_metrics", sa.JSON(), nullable=True),   # 图数据质量指标
        sa.Column("issues", sa.JSON(), nullable=True),          # 问题清单 [{dim, slug, issue_type, detail}]
        sa.Column("attribution", sa.JSON(), nullable=True),     # 规则归因 [{dim, cause, suggestion}]
        sa.Column("summary", sa.Text(), nullable=True),         # 汇总文本
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "kb_quality_report",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(64), nullable=True),  # 单文档评估时对应文档；整库评估可为空
        sa.Column("dim", sa.String(32), nullable=False),  # chunk|wiki|graph
        sa.Column("metrics", sa.JSON(), nullable=True),   # 该维度指标
        sa.Column("issues", sa.JSON(), nullable=True),    # 该维度问题清单
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_quality_report_run", "kb_quality_report", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_quality_report_run", table_name="kb_quality_report")
    op.drop_table("kb_quality_report")
    op.drop_table("kb_quality_run")
