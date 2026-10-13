"""Add kb_datasource.summary_strategy (upload-chain summary routing).

背景（2026-10-13 上传链摘要生成，方案 A 平台自动路由）：
- 三个标准摘要编排（Stuff 全文直塞 / Map-Reduce 分块合并 / Refine 增量修订）
  实测质量达标后，接入上传链：文档解析 READY 后按策略自动选编排生成摘要页。
- KB 级策略：off（默认关，行为不变）| auto（按规模+历史摘要自动路由）|
  stuff | mapreduce | refine（指定模式强制用该编排）。
- auto 路由规则（doc_process._resolve_summary_tape）：
  有历史摘要（kb_document.summary 非空）→ refine；
  无历史摘要且 chunk_count <= SUMMARY_STUFF_MAX_CHUNKS(10) → stuff；
  否则 → mapreduce。
- 幂等：列已存在则 no-op。

Revision ID: 0015_summary_strategy
Revises: 0014_chat_message_feedback
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0015_summary_strategy"
down_revision: Union[str, None] = "0014_chat_message_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_datasource" not in insp.get_table_names():
        return
    if not _column_exists("kb_datasource", "summary_strategy"):
        op.add_column("kb_datasource", sa.Column("summary_strategy", sa.String(16), nullable=True))
        # SQLite 不支持列注释；MySQL 下补字段说明
        if bind.dialect.name == "mysql":
            op.execute(
                "ALTER TABLE kb_datasource MODIFY COLUMN summary_strategy VARCHAR(16) "
                "NULL COMMENT '上传摘要策略: off/auto/stuff/mapreduce/refine'"
            )


def downgrade() -> None:
    if _column_exists("kb_datasource", "summary_strategy"):
        op.drop_column("kb_datasource", "summary_strategy")
