"""Add code-repo columns to kb_datasource / kb_document (代码仓库知识库，2026-10).

代码仓库知识库：创建知识库时选择 Git 仓库作为知识来源，创建后由 worker
(KbCodeRepoSyncTask) 拉取仓库代码存 MinIO，按仓库目录结构建立文档目录树，
文件进入既有解析与 Wiki 构建编排链路。

kb_datasource 新增：
- repo_type: ""=普通文档库 / "git"=代码仓库知识库
- repo_url / repo_branch / repo_auth_type / repo_auth_secret(AES 加密凭证)
- repo_commit_sha(增量同步基准) / repo_pull_status / repo_pull_error
- repo_last_pull_at / repo_file_count

kb_document 新增：
- repo_commit_sha: 文件来源 commit（增量 diff 判定）

Revision ID: 0029_code_repo_config
Revises: 0028_llm_usage
Create Date: 2026-10-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_code_repo_config"
down_revision: Union[str, None] = "0028_llm_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def _add_column(table: str, name: str, column: sa.Column) -> None:
    if name in _columns(table):
        return  # 幂等：已加过（热修/重复执行）直接跳过
    op.add_column(table, column)


def upgrade() -> None:
    _add_column(
        "kb_datasource",
        "repo_type",
        sa.Column("repo_type", sa.String(16), nullable=False, server_default=""),
    )
    _add_column(
        "kb_datasource",
        "repo_url",
        sa.Column("repo_url", sa.String(512), nullable=False, server_default=""),
    )
    _add_column(
        "kb_datasource",
        "repo_branch",
        sa.Column("repo_branch", sa.String(128), nullable=False, server_default=""),
    )
    _add_column(
        "kb_datasource",
        "repo_auth_type",
        sa.Column("repo_auth_type", sa.String(16), nullable=False, server_default="none"),
    )
    _add_column(
        "kb_datasource",
        "repo_auth_secret",
        sa.Column("repo_auth_secret", sa.Text(), nullable=True),
    )
    _add_column(
        "kb_datasource",
        "repo_commit_sha",
        sa.Column("repo_commit_sha", sa.String(64), nullable=False, server_default=""),
    )
    _add_column(
        "kb_datasource",
        "repo_pull_status",
        sa.Column("repo_pull_status", sa.String(16), nullable=False, server_default=""),
    )
    _add_column(
        "kb_datasource",
        "repo_pull_error",
        sa.Column("repo_pull_error", sa.Text(), nullable=True),
    )
    _add_column(
        "kb_datasource",
        "repo_last_pull_at",
        sa.Column("repo_last_pull_at", sa.DateTime(), nullable=True),
    )
    _add_column(
        "kb_datasource",
        "repo_file_count",
        sa.Column("repo_file_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column(
        "kb_document",
        "repo_commit_sha",
        sa.Column("repo_commit_sha", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    for table, cols in (
        ("kb_datasource", (
            "repo_type", "repo_url", "repo_branch", "repo_auth_type",
            "repo_auth_secret", "repo_commit_sha", "repo_pull_status",
            "repo_pull_error", "repo_last_pull_at", "repo_file_count",
        )),
        ("kb_document", ("repo_commit_sha",)),
    ):
        existing = _columns(table)
        for c in cols:
            if c in existing:
                op.drop_column(table, c)
