"""Add sort indexes on kb_tape_run / modo_job (task-monitor filesort 1038).

背景（2026-10-13 任务监控页 500 实测）：
- `GET /api/v1/jobs/parents` 与 `GET /jobs` 按
  `kb_tape_run.created_at DESC, id DESC` / `modo_job.create_time DESC, id DESC`
  排序分页，但两张表都只有 PRIMARY(id) + 业务索引，无排序索引 → 全表 filesort。
- kb_tape_run 行含大 JSON（step_results / bindings，实测单行 step_results 330KB
  + bindings 607KB ≈ 940KB），filesort 需把宽行读进 sort_buffer（默认 256KB）
  → MySQL 1038 'Out of sort memory'，端点 500。
- 修复：补降序复合索引，查询走索引序读取，不再 filesort（MySQL 8 支持降序索引；
  亦兼容 ASC 索引反向扫描）。对已手动建过索引的库幂等跳过。

Revision ID: 0013_job_sort_indexes
Revises: 0012_session_last_request_state
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0013_job_sort_indexes"
down_revision: Union[str, None] = "0012_session_last_request_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def _index_exists(table: str, index: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return index in [i["name"] for i in insp.get_indexes(table)]


def upgrade() -> None:
    # 任务监控父任务/列表查询：ORDER BY created_at DESC, id DESC
    if _table_exists("kb_tape_run") and not _index_exists("kb_tape_run", "ix_kb_tape_run_created_id"):
        op.execute("CREATE INDEX ix_kb_tape_run_created_id ON kb_tape_run (created_at DESC, id DESC)")
    # 任务列表/链头查询：ORDER BY create_time DESC, id DESC
    if _table_exists("modo_job") and not _index_exists("modo_job", "ix_modo_job_create_time_id"):
        op.execute("CREATE INDEX ix_modo_job_create_time_id ON modo_job (create_time DESC, id DESC)")


def downgrade() -> None:
    if _table_exists("kb_tape_run") and _index_exists("kb_tape_run", "ix_kb_tape_run_created_id"):
        op.execute("DROP INDEX ix_kb_tape_run_created_id ON kb_tape_run")
    if _table_exists("modo_job") and _index_exists("modo_job", "ix_modo_job_create_time_id"):
        op.execute("DROP INDEX ix_modo_job_create_time_id ON modo_job")
