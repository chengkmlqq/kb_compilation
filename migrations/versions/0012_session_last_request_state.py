"""Add chat_session.last_request_state (session-level input-state restore).

背景（会话输入态恢复 2026-10-12，对齐 WeKnora `session.last_request_state`）：
- WeKnora 进入会话时会把上一次的输入态（agent / 知识库 / 文件 / 模型 / 联网开关等）
  快照还原到输入区，离开时还原全局默认。kb 侧在各维度资源更少，快照收敛为
  JSON：{"agent_id","kb_id","model_id","web_search_enabled","at_target"}。
- 该列存 JSON 字符串（Text），由前端在每次成功发送消息后写入，打开会话时读取恢复。
- 对已存在的 chat_session 检查列，缺则 ALTER 补上。幂等，表不存在或列已存在均
  no-op。auto-migrate 启动时自动执行。

Revision ID: 0012_session_last_request_state
Revises: 0011_doc_build_tape_id
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0012_session_last_request_state"
down_revision: Union[str, None] = "0011_doc_build_tape_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chat_session" not in insp.get_table_names():
        return
    if not _column_exists("chat_session", "last_request_state"):
        op.add_column("chat_session", sa.Column("last_request_state", sa.Text(), nullable=True))


def downgrade() -> None:
    if _column_exists("chat_session", "last_request_state"):
        op.drop_column("chat_session", "last_request_state")