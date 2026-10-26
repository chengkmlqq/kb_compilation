"""Add kb_tape_run status covering index for task statistics counting.

背景（2026-10-23 性能审计）：GET /api/v1/jobs/statistics 实测 12-26s——原实现
`SELECT id, status FROM kb_tape_run` 全表扫描聚簇索引，而 step_results/bindings
是单行可达 ~940KB 的大 JSON，每个数据页都要读 → 秒级。任务统计/列表按状态
分组计数只需 id+status 两列，加 status 覆盖索引后纯索引扫描零回表。

迁移幂等：表/列缺失或索引已存在则跳过。dev 双表（kb_tape_run_dev）由
create_all（模型 __table_args__ 已声明同名索引）按需补建。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0043_tape_run_status_index"
down_revision: Union[str, None] = "0042_chat_trace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_kb_tape_run_status"


def _index_exists(bind, table: str, index: str) -> bool:
    insp = sa.inspect(bind)
    return index in [i["name"] for i in insp.get_indexes(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_tape_run" not in set(insp.get_table_names()):
        return
    if not _index_exists(bind, "kb_tape_run", INDEX_NAME):
        op.create_index(INDEX_NAME, "kb_tape_run", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_tape_run" not in set(insp.get_table_names()):
        return
    if _index_exists(bind, "kb_tape_run", INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="kb_tape_run")