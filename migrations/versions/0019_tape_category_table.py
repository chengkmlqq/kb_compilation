"""Create kb_tape_category (orchestration category tree standalone table).

背景（2026-10-14）：编排管理 / 编排模板左侧目录树原为从
kb_tape.category / kb_tape_template.category 实时聚合（无独立分类表）。
改为独立表存储：kb_tape_category 存分类定义（cat_type 区分 tape/template/
both，sort_num 排序），存量分类自动回填，计数仍动态聚合。

Revision ID: 0019_tape_category_table
Revises: 0018_quality_assessment
Create Date: 2026-10-14
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0019_tape_category_table"
down_revision: Union[str, None] = "0018_quality_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _col(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists("kb_tape_category"):
        op.create_table(
            "kb_tape_category",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=128), nullable=True),
            sa.Column("cat_type", sa.String(length=16), nullable=False, server_default="both"),
            sa.Column("sort_num", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="1"),
            sa.Column("create_user", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_kb_tape_category_category", "kb_tape_category", ["category"], unique=True)

    # 回填存量分类（保持幂等）：编排管理 = kb_tape.category（默认 通用），
    # 编排模板 = kb_tape_template.category（默认 未分类）。
    conn = bind
    seen: dict[str, str] = {}
    next_sort: list[int] = [0]  # 序号从 0 起（与 ensure_tape_category 的 next_sort 一致）

    def _collect(src_table: str, src_col: str, cat_type: str, default: str) -> None:
        if not _table_exists(src_table) or not _col(src_table, src_col):
            return
        rows = conn.execute(sa.text(f"SELECT DISTINCT {src_col} FROM {src_table}")).fetchall()
        for (val,) in rows:
            name = (val or default).strip() or default
            if name in seen:
                # 已存在：若归属不同则并集为 both
                cur = seen[name]
                conn.execute(
                    sa.text(
                        "UPDATE kb_tape_category SET cat_type = :ct WHERE category = :c"
                    ),
                    {"ct": "both" if cur != cat_type else cur, "c": name},
                )
                continue
            seen[name] = cat_type
            sort = next_sort[0]
            next_sort[0] += 1
            conn.execute(
                sa.text(
                    "INSERT INTO kb_tape_category (id, category, label, cat_type, sort_num, state) "
                    "VALUES (:id, :c, :c, :ct, :s, '1')"
                ),
                {"id": uuid.uuid4().hex, "c": name, "ct": cat_type, "s": sort},
            )

    _collect("kb_tape", "category", "tape", "通用")
    _collect("kb_tape_template", "category", "template", "未分类")
    # 两个源都没有任何分类时，保证默认分类存在（新部署场景）
    if not seen:
        conn.execute(
            sa.text(
                "INSERT INTO kb_tape_category (id, category, label, cat_type, sort_num, state) "
                "VALUES (:id, '通用', '通用', 'both', 0, '1')"
            ),
            {"id": uuid.uuid4().hex},
        )


def downgrade() -> None:
    if _table_exists("kb_tape_category"):
        op.drop_table("kb_tape_category")
