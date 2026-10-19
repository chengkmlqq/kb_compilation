"""Add chunk_type column to doc_chunk (图片 OCR/Caption 子 chunk, 2026-10-19).

背景（图片处理对齐 WeKnora）：
- WeKnora 在文档解析后对每张图片异步跑 image:multimodal 任务，产出
  image_ocr / image_caption 两种子 chunk（ParentChunkID + ChunkFlagRecommended），
  使图片内容可被检索命中。kb 此前仅把图片持久化为 kb-image:// 引用（问答期
  读回注入视觉模型），图片语义不可检索。
- 本迁移为 doc_chunk 增加 chunk_type 列："text"（默认，普通文本切片）/
  "image_ocr" / "image_caption"（解析期 VLM 生成的图片语义子 chunk）。

本迁移：对已存在的 doc_chunk 检查列，缺则 ALTER 补上。幂等，表不存在或列
已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0030_image_chunk_type
Revises: 0029_overview_trend_indexes
Create Date: 2026-10-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0030_image_chunk_type"
down_revision: Union[str, None] = "0029_overview_trend_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "doc_chunk" not in insp.get_table_names():
        return
    if not _column_exists("doc_chunk", "chunk_type"):
        op.add_column(
            "doc_chunk",
            sa.Column("chunk_type", sa.String(32), nullable=False, server_default="text"),
        )


def downgrade() -> None:
    if _column_exists("doc_chunk", "chunk_type"):
        op.drop_column("doc_chunk", "chunk_type")