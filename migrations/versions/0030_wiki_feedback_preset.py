"""Add preset column to wiki_feedback (high-frequency keyword annotation).

背景（2026-10-20 高频词管理功能迁移自 WeKnora）：
- WikiFeedback 原仅 feedback_type=helpful/issue，无业务含义预设标注维度。
- WeKnora 对高频词用 preset 键 kw_meaningful/kw_noise/kw_mis_extracted/custom
  标注「有意义/噪声/误提取/自定义」，overview 接口按 preset 聚合计数展示 ✅/❌/⚠️。
- 仅新增一列，不动既有反馈数据，对 test_framework_models 白名单无影响。

Revision ID: 0030_wiki_feedback_preset
Revises: 0029_overview_trend_indexes
Create Date: 2026-10-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_wiki_feedback_preset"
down_revision: Union[str, None] = "0029_overview_trend_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("wiki_feedback")}
    if "preset" not in cols:
        op.add_column(
            "wiki_feedback",
            sa.Column("preset", sa.String(32), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("wiki_feedback")}
    if "preset" in cols:
        op.drop_column("wiki_feedback", "preset")
