"""kb_datasource 增加切片智能增强配置列（enhancement_config）。

背景（2026-10-25）：知识库表单新增「切片智能增强」配置——开启后文档解析
切分完成时触发「切片智能增强」编排（对标 duowen ChunkExtend：LLM 逐片提取
关键词/问题 + 文档级摘要/关键词），产物随向量同介质存储，检索时参与加权命中。

结构（JSON，扁平对齐 aggregation_config 风格）：:

    {
      "enabled": true,
      "keywords_dividend": 120,    # 可选：每 120 字提 1 个关键词（duowen 默认）
      "questions_dividend": 180,   # 可选：每 180 字生成 1 个问题
      "max_chunks": 500            # 可选：最多增强的切片数（0=全部）
    }

迁移幂等：列已存在则跳过。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0047_kb_enhancement_config"
down_revision: Union[str, None] = "0046_kb_aggregation_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "kb_datasource"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE not in tables:
        return
    existing = {c["name"] for c in insp.get_columns(TABLE)}
    if "enhancement_config" not in existing:
        op.add_column(
            TABLE,
            sa.Column(
                "enhancement_config",
                sa.JSON(),
                nullable=True,
                comment="切片智能增强配置（开启后解析完成触发增强编排，产物随向量存储）",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE not in tables:
        return
    existing = {c["name"] for c in insp.get_columns(TABLE)}
    if "enhancement_config" in existing:
        op.drop_column(TABLE, "enhancement_config")
