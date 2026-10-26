"""Create per-file detail tables for chunk/graph/wiki evaluations.

背景（2026-10-23 评估直落到文件）：文档转换评估已按「每文件一行」落库
（kb_quality_doc_eval）。切片/图数据/wiki 评估同样改为文件粒度存储与展示：
- kb_quality_chunk_eval：切片评估每文件一行（chunks/avg_len/wiki_pages/
  wiki 构建时间/耗时/评分/结论/问题）
- kb_quality_graph_eval：图数据评估每文件一行（按文档聚合其 wiki 页对应
  图节点/边/哑节点/不可回溯/wiki 缺失）
- kb_quality_wiki_eval：wiki 评估每文件一行（按文件聚合构建出的 wiki 页
  空页/孤立/断链/占位/缺出处/未挂目录）

三表结构对齐 kb_quality_doc_eval：kb_id+doc_id 唯一约束、run_id 记录来源、
评估落库按 kb 整批 DELETE+INSERT 覆盖。

迁移幂等：表已存在则跳过。

Revision ID: 0041_quality_eval_file_tables
Revises: 0040_quality_doc_eval_garbled_split
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0041_quality_eval_file_tables"
down_revision: Union[str, None] = "0040_quality_doc_eval_garbled_split"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _make_table(name: str, extra_columns: list[sa.Column]) -> None:
    cols = [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
    ] + extra_columns + [
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("kb_id", "doc_id", name=f"uq_{name}_kb_doc"),
    ]
    op.create_table(name, *cols)
    op.create_index(f"ix_{name}_kb_id", name, ["kb_id"])
    op.create_index(f"ix_{name}_doc_id", name, ["doc_id"])
    op.create_index(f"ix_{name}_run_id", name, ["run_id"])


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    if "kb_quality_chunk_eval" not in existing:
        _make_table("kb_quality_chunk_eval", [
            sa.Column("chunks", sa.Integer(), nullable=True),
            sa.Column("avg_len", sa.Float(), nullable=True),
            sa.Column("wiki_pages", sa.Integer(), nullable=True),
            sa.Column("wiki_start_time", sa.DateTime(), nullable=True),
            sa.Column("wiki_build_sec", sa.Float(), nullable=True),
            sa.Column("doc_process_sec", sa.Float(), nullable=True),
            sa.Column("wiki_tape", sa.String(128), nullable=True),
            sa.Column("eval_duration_sec", sa.Float(), nullable=True),
            sa.Column("conclusion", sa.String(500), nullable=True),
            sa.Column("error", sa.String(500), nullable=True),
        ])
    if "kb_quality_graph_eval" not in existing:
        _make_table("kb_quality_graph_eval", [
            sa.Column("nodes", sa.Integer(), nullable=True),
            sa.Column("rels", sa.Integer(), nullable=True),
            sa.Column("dumb_nodes", sa.Integer(), nullable=True),
            sa.Column("untraceable_rels", sa.Integer(), nullable=True),
            sa.Column("wiki_missing", sa.Integer(), nullable=True),
        ])
    if "kb_quality_wiki_eval" not in existing:
        _make_table("kb_quality_wiki_eval", [
            sa.Column("pages", sa.Integer(), nullable=True),
            sa.Column("empty", sa.Integer(), nullable=True),
            sa.Column("orphan", sa.Integer(), nullable=True),
            sa.Column("broken_links", sa.Integer(), nullable=True),
            sa.Column("placeholder", sa.Integer(), nullable=True),
            sa.Column("missing_source", sa.Integer(), nullable=True),
            sa.Column("no_folder", sa.Integer(), nullable=True),
        ])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for t in ("kb_quality_wiki_eval", "kb_quality_graph_eval", "kb_quality_chunk_eval"):
        if t in tables:
            op.drop_table(t)