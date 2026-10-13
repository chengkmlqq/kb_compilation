"""Merge two heads (wiki page folders / tape category).

背景（2026-10-13）：同一时刻两位并行改动者各自基于 0014 建迁移——
- 0015_summary_strategy（上传链摘要自动路由，b7546ff）
- 0015_wiki_page_folders（供管 wiki 多目录挂载，4d85a5c）
以及 0016_tape_category（编排分类左右结构，684f395，链 0015_summary_strategy）。
两条链在 0014 分叉，upgrade head 需要 merge 迁移收拢为单头。

Revision ID: 0017_merge
Revises: 0016_tape_category, 0015_wiki_page_folders
Create Date: 2026-10-13
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0017_merge"
down_revision: Union[str, Sequence[str], None] = (
    "0016_tape_category",
    "0015_wiki_page_folders",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """空操作：纯 merge 迁移，无 schema 变更。"""
    pass


def downgrade() -> None:
    """空操作：纯 merge 迁移，无 schema 变更。"""
    pass