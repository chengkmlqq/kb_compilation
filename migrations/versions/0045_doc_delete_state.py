"""kb_document 增加异步删除状态列（delete_state / delete_error）。

背景（2026-10-23）：文档删除改为异步——API 只标记 DELETING 并入队 Celery
清理任务，级联清理（wiki 页/链接/目录、chunks、向量、存储文件、Neo4j 图谱、
任务监控残留）由 worker 后台执行，避免大文档删除时同步清理拖垮 HTTP 响应。

- delete_state："" 正常 / DELETING 删除中 / DELETE_FAILED 清理失败（celery
  重试耗尽后由 beat 定时扫描重投）。
- delete_error：清理失败原因（DELETE_FAILED 时回填，展示/排查用）。

迁移幂等：列已存在则跳过。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0045_doc_delete_state"
down_revision: Union[str, None] = "0044_quality_wiki_eval_ext"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "kb_document"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE not in tables:
        return
    existing = {c["name"] for c in insp.get_columns(TABLE)}
    if "delete_state" not in existing:
        op.add_column(
            TABLE,
            sa.Column(
                "delete_state",
                sa.String(16),
                nullable=False,
                server_default="",
            ),
        )
        op.create_index("ix_kb_document_delete_state", TABLE, ["delete_state"])
    if "delete_error" not in existing:
        op.add_column(TABLE, sa.Column("delete_error", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE not in tables:
        return
    existing = {c["name"] for c in insp.get_columns(TABLE)}
    if "delete_state" in existing:
        op.drop_index("ix_kb_document_delete_state", table_name=TABLE)
        op.drop_column(TABLE, "delete_state")
    if "delete_error" in existing:
        op.drop_column(TABLE, "delete_error")