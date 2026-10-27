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


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    # 2026-10-26 守卫：空库自举时 create_all 已用最新 ORM 模型建表（含 attachment_ids），
    # 无守卫 add_column 会 1060 Duplicate column → 迁移中断 → 容器重启循环。
    if _column_exists("chat_message", "attachment_ids"):
        return
    op.add_column("chat_message", sa.Column("attachment_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_message", "attachment_ids")
