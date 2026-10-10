"""Add process_config to kb_document if missing (file-level upload overrides).

背景（2026-10-10 上传处理配置对齐 WeKnora）：
- WeKnora 在上传文件时弹出 UploadConfirmDialog，可按批次选择解析引擎 /
  切片（chunk_size/overlap/separators/父子分块/strategy/token_limit）等
  处理配置，随上传带 process_config（KnowledgeProcessOverrides）——文件级
  覆盖 KB 默认。
- kb 此前只有 KB 级配置（kb_datasource.indexing_strategy["chunking"]），
  上传无确认弹窗。本次新增 kb_document.process_config（Text/JSON 字符串）：
  该文档解析时若带文件级配置，则覆盖 KB 级配置。

本迁移：对已存在的 kb_document 检查列，缺则 ALTER 补上。幂等，
表不存在或列已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0005_doc_process_config
Revises: 0003_orch_queue_name
Create Date: 2026-10-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005_doc_process_config"
down_revision: Union[str, None] = "0004_doc_folder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_document" not in insp.get_table_names():
        return
    if not _column_exists("kb_document", "process_config"):
        op.add_column(
            "kb_document",
            sa.Column("process_config", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("kb_document", "process_config"):
        op.drop_column("kb_document", "process_config")
