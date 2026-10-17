"""Create kb_llm_usage (LLM 使用记录，2026-10-16 LLM 使用看板数据源).

一条逻辑 LLM 调用一条记录：智能问答流式（chat.py）、wiki 构建管线
（weknora_rpc.llm_call）、agent 任务三处埋点 best-effort 写入。
含 token/延迟/状态/错误分类字段，无任何金额字段。

Revision ID: 0028_llm_usage
Revises: 0027_kb_category
Create Date: 2026-10-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_llm_usage"
down_revision: Union[str, None] = "0027_kb_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def upgrade() -> None:
    if _table_exists("kb_llm_usage"):
        return
    op.create_table(
        "kb_llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("ts", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default=""),
        sa.Column("kb_id", sa.String(64), nullable=True),
        sa.Column("kb_name", sa.String(128), nullable=True),
        sa.Column("doc_id", sa.String(64), nullable=True),
        sa.Column("doc_name", sa.String(255), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("operation_type", sa.String(32), nullable=True),
        sa.Column("phase", sa.String(48), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("tps", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="success"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_kb_llm_usage_ts", "kb_llm_usage", ["ts"])
    op.create_index("ix_kb_llm_usage_trace_id", "kb_llm_usage", ["trace_id"])
    op.create_index("ix_kb_llm_usage_kb_id", "kb_llm_usage", ["kb_id"])
    op.create_index("ix_kb_llm_usage_model", "kb_llm_usage", ["model"])
    op.create_index("ix_kb_llm_usage_operation_type", "kb_llm_usage", ["operation_type"])
    op.create_index("ix_kb_llm_usage_status", "kb_llm_usage", ["status"])


def downgrade() -> None:
    if not _table_exists("kb_llm_usage"):
        return
    op.drop_index("ix_kb_llm_usage_ts", table_name="kb_llm_usage")
    op.drop_index("ix_kb_llm_usage_trace_id", table_name="kb_llm_usage")
    op.drop_index("ix_kb_llm_usage_kb_id", table_name="kb_llm_usage")
    op.drop_index("ix_kb_llm_usage_model", table_name="kb_llm_usage")
    op.drop_index("ix_kb_llm_usage_operation_type", table_name="kb_llm_usage")
    op.drop_index("ix_kb_llm_usage_status", table_name="kb_llm_usage")
    op.drop_table("kb_llm_usage")
