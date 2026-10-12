"""Add build_tape_id to kb_document (per-document build engine override).

背景（2026-10-12 构建服务重选）：
- 构建引擎此前只在 KB 级（kb_datasource.build_tape_id）：创建知识库时选一次，
  该库全部文档上传后都走同一个编排。
- 本次放开到文档级：上传文件 / 绑定数据源维度表时，可针对该文档重选一个
  构建服务（服务编排中已发布的编排），覆盖 KB 默认；空 = 用 KB 默认。
- kb_document.build_tape_id 存文档级覆盖；worker 触发编排时优先读它。
- 对已存在的 kb_document 检查列，缺则 ALTER 补上。幂等，
  表不存在或列已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0011_doc_build_tape_id
Revises: 0010_chunk_content_mediumtext
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0011_doc_build_tape_id"
down_revision: Union[str, None] = "0010_chunk_content_mediumtext"
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
    if not _column_exists("kb_document", "build_tape_id"):
        op.add_column("kb_document", sa.Column("build_tape_id", sa.String(64), nullable=True))
        try:
            op.create_index("idx_kb_document_build_tape", "kb_document", ["build_tape_id"])
        except Exception:  # noqa: BLE001 — 索引已存在等环境差异不阻塞迁移
            pass


def downgrade() -> None:
    if _column_exists("kb_document", "build_tape_id"):
        op.drop_column("kb_document", "build_tape_id")