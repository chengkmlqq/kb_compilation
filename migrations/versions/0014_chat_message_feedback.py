"""Add chat_message.feedback / feedback_at (answer like/dislike).

背景（问答「喜欢/不喜欢」反馈 2026-10-13）：
- 智能问答页在每条 AI 回复下提供「喜欢 / 不喜欢」反馈按钮，记录到
  chat_message 行（like / dislike / NULL=未反馈），同会话同消息只保留一条。
- 反馈属会话所有者（chat_session → user），无需独立表；两列均在
  chat_message 上，序列化时透出给前端回显。
- 幂等：表不存在或列已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0014_chat_message_feedback
Revises: 0013_job_sort_indexes
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0014_chat_message_feedback"
down_revision: Union[str, None] = "0013_job_sort_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chat_message" not in insp.get_table_names():
        return
    if not _column_exists("chat_message", "feedback"):
        op.add_column("chat_message", sa.Column("feedback", sa.String(length=16), nullable=True))
    if not _column_exists("chat_message", "feedback_at"):
        op.add_column("chat_message", sa.Column("feedback_at", sa.String(length=32), nullable=True))


def downgrade() -> None:
    if _column_exists("chat_message", "feedback"):
        op.drop_column("chat_message", "feedback")
    if _column_exists("chat_message", "feedback_at"):
        op.drop_column("chat_message", "feedback_at")