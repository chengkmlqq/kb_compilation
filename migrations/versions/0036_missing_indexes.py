"""Add missing indexes for business tables (chat_session/kb_datasource/kb_document/kb_quality_run/wiki_link/wiki_page_issue).

背景（2026-10-21 索引巡检）：模型声明 index=True / Index() 但线上库缺失的
索引——比对 SQLAlchemy 元数据声明列组合与实际 SHOW INDEX 覆盖，差异表 6 张：

- chat_session.parent_session_id   分叉会话查询（按父会话列出子会话）
- kb_datasource.scope / owner_team_name   kb_visible_clauses 每请求可见性 OR 过滤
- kb_document.source_type          按来源类型过滤（file/git 等）
- kb_quality_run.kb_id             按知识库查质量运行记录
- wiki_link.kb_id                  51k+ 行，多处 WHERE kb_id（删除/读链接）
- wiki_page_issue.kb_id            按知识库查 wiki 问题

命名统一用 SQLAlchemy 自动命名约定（ix_<table>_<col>），与模型声明一致；
迁移幂等：索引已存在则跳过（跨环境兼容）。dev 双表（xxx_dev）物理索引
与正式表同名，由 env_table_router 路由的查询同样受益，本迁移只作用于
正式表（dev 表随 create_all/后续迁移补建，量小影响有限）。

Revision ID: 0036_missing_indexes
Revises: 0035_job_ref_columns
Create Date: 2026-10-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0036_missing_indexes"
down_revision: Union[str, None] = "0035_job_ref_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column) → 期望索引名（SQLAlchemy 单列 index=True 自动命名 ix_<table>_<col>）
MISSING_INDEXES: list[tuple[str, str]] = [
    ("chat_session", "parent_session_id"),
    ("kb_datasource", "scope"),
    ("kb_datasource", "owner_team_name"),
    ("kb_document", "source_type"),
    ("kb_quality_run", "kb_id"),
    ("wiki_link", "kb_id"),
    ("wiki_page_issue", "kb_id"),
]


def _index_exists(bind, table: str, index: str) -> bool:
    insp = sa.inspect(bind)
    return index in [i["name"] for i in insp.get_indexes(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for table, column in MISSING_INDEXES:
        if table not in tables:
            continue
        # 列可能未建（旧库缺列时索引无法创建，跳过由后续迁移负责）
        col_names = {c["name"] for c in insp.get_columns(table)}
        if column not in col_names:
            continue
        idx = f"ix_{table}_{column}"
        if not _index_exists(bind, table, idx):
            op.execute(f"CREATE INDEX {idx} ON {table} ({column})")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for table, column in MISSING_INDEXES:
        if table not in tables:
            continue
        idx = f"ix_{table}_{column}"
        if _index_exists(bind, table, idx):
            op.execute(f"DROP INDEX {idx} ON {table}")
