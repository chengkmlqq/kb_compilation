"""Add sort index on kb_quality_run (quality-runs list filesort 1038).

背景（2026-10-16 质量评估页 500 实测）：
- `GET /api/v1/quality/runs` 按 `created_at DESC` 排序分页，但 kb_quality_run
  只有 PRIMARY(id) 一个索引，无排序索引 → 全表 filesort。
- kb_quality_run.issues 是聚合问题清单 JSON，实测整库评估单行 ~600KB（
  kb_quality_report.issues 单行 ~580KB），filesort 需把宽行读进 sort_buffer
  （默认 256KB）→ MySQL 1038 'Out of sort memory'，列表与详情端点 500。
- 修复：① 查询侧改「窄列 id 排序 → IN 回表」（api/routers/quality.py，不依赖
  优化器；见同源修复 jobs parents 9c5d15b / 编排 runs dc63463）；② 本迁移补
  降序复合索引供数据量大后走索引序读取（与 0013_job_sort_indexes 同款）。
  对已手动建过索引的库幂等跳过。

Revision ID: 0026_quality_run_sort_index
Revises: 0025_chat_attachment_image_object
Create Date: 2026-10-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026_quality_run_sort_index"
down_revision: Union[str, None] = "0025_chat_attachment_image_object"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def _index_exists(table: str, index: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return index in [i["name"] for i in insp.get_indexes(table)]


def upgrade() -> None:
    # 质量评估列表：ORDER BY created_at DESC, id DESC
    if _table_exists("kb_quality_run") and not _index_exists(
        "kb_quality_run", "ix_kb_quality_run_created_id"
    ):
        op.execute(
            "CREATE INDEX ix_kb_quality_run_created_id "
            "ON kb_quality_run (created_at DESC, id DESC)"
        )


def downgrade() -> None:
    if _table_exists("kb_quality_run") and _index_exists(
        "kb_quality_run", "ix_kb_quality_run_created_id"
    ):
        op.execute("DROP INDEX ix_kb_quality_run_created_id ON kb_quality_run")
