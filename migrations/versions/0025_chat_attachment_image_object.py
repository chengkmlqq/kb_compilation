"""Chat attachment image: object storage key + VLM caption.

背景（2026-10-18 图片问答对齐 WeKnora）：
- 图片原先只存 chat_attachment.file_data（LONGBLOB），会话内每轮问答都把全部图片
  重新 base64 重发；对齐 WeKnora 后图片落对象存储（chat_images/<session>/<att>.<ext>），
  DB 只留 storage key（object_key），未配置 MinIO 的环境继续用 file_data 兼容。
- 新增 caption 列保存视觉模型生成的图片描述：对话模型不支持视觉时用它做文本回退，
  历史轮次按此回带「用户上传图片内容」（对齐 WeKnora message.Images[].Caption）。

幂等：列已存在即跳过；表不存在即跳过（新环境由 create_all/0001 建立）。
MySQL / PostgreSQL / SQLite 通用（列类型为可移植类型），无需方言分支。

Revision ID: 0025_chat_attachment_image_object
Revises: 0024_attachment_file_data
Create Date: 2026-10-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0025_chat_attachment_image_object"
down_revision: Union[str, None] = "0024_attachment_file_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _existing_columns("chat_attachment")
    if not cols:
        return
    if "object_key" not in cols:
        op.add_column(
            "chat_attachment", sa.Column("object_key", sa.String(length=512), nullable=True)
        )
    if "caption" not in cols:
        op.add_column("chat_attachment", sa.Column("caption", sa.Text(), nullable=True))


def downgrade() -> None:
    cols = _existing_columns("chat_attachment")
    if not cols:
        return
    if "caption" in cols:
        op.drop_column("chat_attachment", "caption")
    if "object_key" in cols:
        op.drop_column("chat_attachment", "object_key")
