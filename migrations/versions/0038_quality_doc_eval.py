"""Create kb_quality_doc_eval: per-file document-conversion evaluation detail.

背景（2026-10-23 文档转换评估单文件粒度落库）：
- 需求：文档转换评估结果按「每文件一行」存储；每次重新评估时覆盖该文件
  上一次的评估结果；页面按文件粒度查看。
- 此前 per_doc 明细嵌在 kb_quality_run.doc_metrics JSON 里（整库一次评估
  = 一行，JSON 随文件数线性膨胀，曾触发 1038 Out of sort memory）。
- 新表 kb_quality_doc_eval：kb_id+doc_id 唯一约束（一文件一行），评估落库时
  按 kb 整批 DELETE+INSERT 覆盖（文件删除后旧行不残留）；run_id 记录最后一次
  评估来源 run（仅信息性，不级联删除）。

迁移幂等：表已存在则跳过（跨环境兼容，dev 双表由 create_all 补建）。

Revision ID: 0038_quality_doc_eval
Revises: 0037_quality_run_dim
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0038_quality_doc_eval"
down_revision: Union[str, None] = "0037_quality_run_dim"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_quality_doc_eval" in set(insp.get_table_names()):
        return
    op.create_table(
        "kb_quality_doc_eval",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_ext", sa.String(32), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("parse_state", sa.String(32), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parse_engine", sa.String(64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("md_length", sa.Integer(), nullable=True),
        sa.Column("garbled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("garbled_hits", sa.JSON(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("kb_id", "doc_id", name="uq_quality_doc_eval_kb_doc"),
    )
    op.create_index("ix_quality_doc_eval_kb_id", "kb_quality_doc_eval", ["kb_id"])
    op.create_index("ix_quality_doc_eval_doc_id", "kb_quality_doc_eval", ["doc_id"])
    op.create_index("ix_quality_doc_eval_run_id", "kb_quality_doc_eval", ["run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_quality_doc_eval" not in set(insp.get_table_names()):
        return
    op.drop_table("kb_quality_doc_eval")
