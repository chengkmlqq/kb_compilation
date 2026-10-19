"""Add kb_model.max_tokens (chat 单次回答最大输出 token).

思考型模型（如 deepseek-v4-pro）reasoning 吃满 max_tokens 预算时正文为空，
模型注册表增加可配置字段，问答链路据此覆盖默认 2048。

Revision ID: 0033_model_max_tokens
Revises: 0032_merge_0030_heads
Create Date: 2026-10-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033_model_max_tokens"
down_revision: Union[str, Sequence[str], None] = "0032_merge_0030_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists("kb_model"):
        return
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kb_model")}
    if "max_tokens" not in cols:
        op.add_column("kb_model", sa.Column("max_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    if not _table_exists("kb_model"):
        return
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kb_model")}
    if "max_tokens" in cols:
        op.drop_column("kb_model", "max_tokens")
