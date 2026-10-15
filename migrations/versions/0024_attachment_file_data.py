"""Alter chat_attachment.file_data to LONGBLOB (capacity for image attachments).

背景（2026-10-15）：聊天上传图片附件 500。chat_attachment.file_data 原为
SQLAlchemy 裸 LargeBinary → MySQL BLOB（64KB 字节上限），而图片附件大小
上限 MAX_ATTACHMENT_BYTES = 20MB——真实截图/照片普遍 >64KB，INSERT 报
`Data too long for column 'file_data'`（1406）→ FastAPI 未捕获 → 500（
响应体恰为 21 字节 "Internal Server Error"，nginx access log 的 `500 21`
与之一致）。对比：mcp_skill.package_zip 用 LargeBinary(16MB)（→MEDIUMBLOB）
无此问题。改为 LONGBLOB（4GB）后 20MB 上限可完整落库。

- 只对 MySQL 生效（LONGBLOB 为 MySQL 专有类型）；其余方言 no-op
  （sqlite/PG 的 LargeBinary 本无 64KB 限制）。
- 幂等：表不存在 / 列已是 MEDIUM/LONG BLOB 均 no-op。auto-migrate 启动
  自动执行。

Revision ID: 0024_attachment_file_data
Revises: 0023_wiki_content_mediumtext
Create Date: 2026-10-15
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql as mysql_dialect

# revision identifiers, used by Alembic.
revision: str = "0024_attachment_file_data"
down_revision: Union[str, None] = "0023_wiki_content_mediumtext"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_type_name(table: str, column: str) -> str:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for c in insp.get_columns(table):
        if c["name"] == column:
            return str(c["type"]).upper()
    return ""


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chat_attachment" not in insp.get_table_names():
        return
    if bind.dialect.name != "mysql":
        return
    # 已是 MEDIUMBLOB / LONGBLOB 则无需改动
    cur = _col_type_name("chat_attachment", "file_data")
    if "MEDIUM" in cur or "LONG" in cur:
        return
    op.alter_column(
        "chat_attachment", "file_data",
        existing_type=sa.LargeBinary(),
        type_=mysql_dialect.LONGBLOB(),
        nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chat_attachment" not in insp.get_table_names() or bind.dialect.name != "mysql":
        return
    op.alter_column(
        "chat_attachment", "file_data",
        existing_type=mysql_dialect.LONGBLOB(),
        type_=sa.LargeBinary(),
        nullable=True,
    )