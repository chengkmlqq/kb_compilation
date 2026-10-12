"""Add build_tape_id to kb_datasource (build engine = published orchestration tape).

背景（构建引擎 2026-10-12）：
- 知识库创建表单去掉「知识库类型 / 本体 Schema / 自定义 Wiki 生成」，
  新增「构建引擎」下拉：选择服务编排中已发布的编排程序（kb_tape.status=effective）。
- kb_datasource.build_tape_id 记录该绑定（空 = 使用内置 wiki 构建链）；
  文档解析成功后若绑定存在，自动新建 TapeRun 按该编排构建 wiki。
- 对已存在的 kb_datasource 检查列，缺则 ALTER 补上。幂等，
  表不存在或列已存在均 no-op。auto-migrate 启动时自动执行。

Revision ID: 0009_build_tape_id
Revises: 0008_job_parent_id
Create Date: 2026-10-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0009_build_tape_id"
down_revision: Union[str, None] = "0008_job_parent_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "kb_datasource" not in insp.get_table_names():
        return
    if not _column_exists("kb_datasource", "build_tape_id"):
        op.add_column("kb_datasource", sa.Column("build_tape_id", sa.String(64), nullable=True))
        try:
            op.create_index("idx_kb_datasource_build_tape", "kb_datasource", ["build_tape_id"])
        except Exception:  # noqa: BLE001 — 索引已存在等环境差异不阻塞迁移
            pass


def downgrade() -> None:
    if _column_exists("kb_datasource", "build_tape_id"):
        op.drop_column("kb_datasource", "build_tape_id")