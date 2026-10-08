"""Add orchestration tables (kb_step_define / kb_tape / kb_tape_step + kb_tape_run).

编排功能首次落地：既有库从未有这三张表（旧镜像无编排代码），且
kb_tape_step 新增 queue_name 列（异步化，2026-10-09）、kb_tape_run 为新增表。

Revision ID: 0002_orchestration_tables
Revises: 0001_baseline
Create Date: 2026-10-09

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_orchestration_tables"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def upgrade() -> None:
    # kb_step_define —— 组件定义
    if not _table_exists("kb_step_define"):
        op.create_table(
            "kb_step_define",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("group_type", sa.String(64), nullable=False, server_default="基础"),
            sa.Column("step_inst", sa.String(64), nullable=False),
            sa.Column("step_label", sa.String(128), nullable=False),
            sa.Column("step_icon", sa.String(32), nullable=True),
            sa.Column("step_desc", sa.Text(), nullable=True),
            sa.Column("step_cfg", sa.JSON(), nullable=True),
            sa.Column("step_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="effective"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        )
        op.create_index("ix_kb_step_define_step_inst", "kb_step_define", ["step_inst"])

    # kb_tape —— 编排主表
    if not _table_exists("kb_tape"):
        op.create_table(
            "kb_tape",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tape_name", sa.String(64), nullable=False),
            sa.Column("tape_label", sa.String(128), nullable=False, server_default=""),
            sa.Column("tape_descr", sa.String(2000), nullable=True),
            sa.Column("tape_type", sa.String(32), nullable=False, server_default="general"),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("nodes", sa.JSON(), nullable=True),
            sa.Column("edges", sa.JSON(), nullable=True),
            sa.Column("exec_params", sa.JSON(), nullable=True),
            sa.Column("create_user", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        )
        op.create_index("ix_kb_tape_tape_name", "kb_tape", ["tape_name"])

    # kb_tape_step —— 步骤子表（含 queue_name 异步化列）
    if not _table_exists("kb_tape_step"):
        op.create_table(
            "kb_tape_step",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tape_id", sa.String(64), nullable=False),
            sa.Column("step_inst", sa.String(64), nullable=False),
            sa.Column("step_label", sa.String(128), nullable=False, server_default=""),
            sa.Column("step_config", sa.JSON(), nullable=True),
            sa.Column("step_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pre_step_ids", sa.JSON(), nullable=True),
            sa.Column("next_step_ids", sa.JSON(), nullable=True),
            sa.Column("queue_name", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        )
        op.create_index("ix_kb_tape_step_tape_id", "kb_tape_step", ["tape_id"])

    # kb_tape_run —— 执行记录（异步化新增）
    if not _table_exists("kb_tape_run"):
        op.create_table(
            "kb_tape_run",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tape_id", sa.String(64), nullable=False),
            sa.Column("tape_name", sa.String(128), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("inputs", sa.JSON(), nullable=True),
            sa.Column("step_results", sa.JSON(), nullable=True),
            sa.Column("bindings", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("create_user", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        )
        op.create_index("ix_kb_tape_run_tape_id", "kb_tape_run", ["tape_id"])


def downgrade() -> None:
    for t in ("kb_tape_run", "kb_tape_step", "kb_tape", "kb_step_define"):
        if _table_exists(t):
            op.drop_table(t)