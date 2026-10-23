"""chat_message 增加 attachment_ids 列：user 消息携带的全部附件 id（图片+文档）。

2026-10-20 附件问答交互改造：前端在用户消息侧展示附件入口，
需要知道每条 user 消息携带了哪些附件（不限于图片）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_chat_message_attachment_ids"
down_revision = "0033_model_max_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_message", sa.Column("attachment_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_message", "attachment_ids")
