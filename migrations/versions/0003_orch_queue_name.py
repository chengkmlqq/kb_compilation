"""Add queue_name to kb_tape_step if missing (orchestration async drift fix).

背景（2026-10-09 编排导入导出 e2e 撞出）：
- 编排迁移第一批（bd50b3d）时模型无 queue_name 列，kb_tape_step 由
  Base.metadata.create_all 建出（无该列）。
- 异步化批次模型加了 queue_name，但 0002 迁移对 kb_tape_step 用
  "表不存在才建" 保护 —— 表已存在则整段跳过，queue_name 永远缺失，
  且 alembic 仍记录 0002 成功。这是幂等保护写法的盲区：
  "表不存在才建" 掩盖了 "表存在但缺新列"。
- 症状：POST /orchestrations/{id}/design 500，
  `Unknown column 'queue_name' in 'field list'`。

本迁移：对已存在的 kb_tape_step 检查列，缺则 ALTER 补上。幂等，
表不存在或列已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0003_orch_queue_name
Revises: 0002_orchestration_tables
Create Date: 2026-10-09

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_orch_queue_name"
down_revision: Union[str, None] = "0002_orchestration_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    # 表可能不存在（新环境直接由 0002 建出含 queue_name）——仅当表存在且缺列才 ALTER
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_tape_step" not in insp.get_table_names():
        return
    if not _column_exists("kb_tape_step", "queue_name"):
        op.add_column("kb_tape_step", sa.Column("queue_name", sa.String(64), nullable=True))


def downgrade() -> None:
    if _column_exists("kb_tape_step", "queue_name"):
        op.drop_column("kb_tape_step", "queue_name")
