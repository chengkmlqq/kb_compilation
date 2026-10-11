"""Add modo_job.parent_id for parent/child task hierarchy (task monitor tree).

背景（2026-10-12）：任务监控页父子任务改造——文档处理链（解析→向量化→
wiki 构建→图谱）与编排执行（kb_tape_run）要按「父任务 + 子任务」展示。
modo_job 增加 parent_id 列：链头任务（如 DOC_{docId} 解析）为父任务
（parent_id=NULL），其触发下游任务（wiki/graph/embed）写 parent_id=链头 job id。
编排 run 走 kb_tape_run（虚拟父任务），不写 modo_job。

本迁移幂等：列不存在则加，已存在 no-op。auto-migrate 启动时自动执行。

Revision ID: 0008_job_parent_id
Revises: 0007_wiki_page_issue
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_job_parent_id"
down_revision: Union[str, None] = "0007_wiki_page_issue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.has_table(bind, "modo_job"):
        cols = [r[0] for r in bind.execute(sa.text("SHOW COLUMNS FROM modo_job"))]
        if "parent_id" not in cols:
            op.add_column("modo_job", sa.Column("parent_id", sa.String(64), nullable=True))
            op.create_index("ix_modo_job_parent_id", "modo_job", ["parent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.has_table(bind, "modo_job"):
        op.drop_index("ix_modo_job_parent_id", table_name="modo_job")
        op.drop_column("modo_job", "parent_id")