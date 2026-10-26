"""kb_datasource 增加知识聚合配置列（aggregation_config）。

背景（2026-10-25）：知识库表单新增「知识聚合」配置——开关主题聚类（含用户
填写主题列表）与按版本聚类；开启后平台自动创建调用对应编排的每日定时任务。

结构（JSON，扁平对齐 indexing_strategy 风格）：:

    {
      "topic_cluster_enabled": true,
      "topic_cluster_topics": ["采购管理", "合同管理"],
      "version_cluster_enabled": true,
      "cron_expression": "0 2 * * *"   # 默认每日 02:00，表单可调
    }

迁移幂等：列已存在则跳过。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0046_kb_aggregation_config"
down_revision: Union[str, None] = "0045_doc_delete_state"
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
    if "aggregation_config" not in existing:
        op.add_column(TABLE, sa.Column("aggregation_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE not in tables:
        return
    existing = {c["name"] for c in insp.get_columns(TABLE)}
    if "aggregation_config" in existing:
        op.drop_column(TABLE, "aggregation_config")
