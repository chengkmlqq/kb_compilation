"""Alter doc_chunk.content to MEDIUMTEXT (capacity backstop for oversized chunks).

背景（方案 C 2026-10-12）：
- 「全量指标20260814.xlsx」上传入库失败：doc_chunk.content 是 TEXT
  （MySQL 64KB 字节上限），xlsx 表格文本的超长块（实测 78687 字符约
  140KB）INSERT 直接报 Data too long (1406)。
- 根因是 heuristic tier 的 preamble 分支未做长度兜底 + split 出口无
  物理上限（已在 chunking_tiers/chunking_orch 同批修复）；本迁移把列
  容量提到 MEDIUMTEXT（16MB）作为第二道兜底。
- 只对 MySQL 生效（MEDIUMTEXT 为 MySQL 专有类型）；其余方言 no-op
  （sqlite/PG 的 Text 本无 64KB 限制，补丁无意义）。
- 幂等：表不存在 / 列已是 MEDIUMTEXT 均 no-op。auto-migrate 启动自动执行。

Revision ID: 0010_chunk_content_mediumtext
Revises: 0009_build_tape_id
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql as mysql_dialect

# revision identifiers, used by Alembic.
revision: str = "0010_chunk_content_mediumtext"
down_revision: Union[str, None] = "0009_build_tape_id"
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
    if "doc_chunk" not in insp.get_table_names():
        return
    if bind.dialect.name != "mysql":
        return
    # 已是 MEDIUMTEXT / LONGTEXT 则无需改动
    cur = _col_type_name("doc_chunk", "content")
    if "MEDIUM" in cur or "LONG" in cur:
        return
    op.alter_column(
        "doc_chunk", "content",
        existing_type=sa.Text(),
        type_=mysql_dialect.MEDIUMTEXT(),
        nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "doc_chunk" not in insp.get_table_names() or bind.dialect.name != "mysql":
        return
    op.alter_column(
        "doc_chunk", "content",
        existing_type=mysql_dialect.MEDIUMTEXT(),
        type_=sa.Text(),
        nullable=False,
    )