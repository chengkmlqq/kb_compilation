"""Enlarge wiki_page.content to MEDIUMTEXT.

背景（2026-10-14）：领域编排全链路实测——技术规范总册（860 规则）实体
「企业级省大数据平台」约束对象 63 条规则，业务实体页关键规则表 content
超 TEXT(64KB) 上限 → POST wiki/pages 500 → 业务实体页建页步骤失败。
TEXT 上限按字节计（utf8mb4 中文 3 字节，实际 ~2 万字），规则密集实体
的大表页合理超限。改为 MEDIUMTEXT（16MB）。

Revision ID: 0023_wiki_content_mediumtext
Revises: 0022_ontology_schema_llm_config
Create Date: 2026-10-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023_wiki_content_mediumtext"
down_revision: Union[str, None] = "0022_ontology_schema_llm_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("wiki_page"):
        return
    from sqlalchemy.dialects import mysql

    insp = sa.inspect(op.get_bind())
    for c in insp.get_columns("wiki_page"):
        if c["name"] == "content" and str(c["type"]).lower() == "text":
            op.alter_column("wiki_page", "content", type_=mysql.MEDIUMTEXT())
            break


def downgrade() -> None:
    if _table_exists("wiki_page"):
        op.alter_column("wiki_page", "content", type_=sa.Text())
