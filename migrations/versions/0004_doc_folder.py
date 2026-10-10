"""Document multi-level directory (doc_folder table + kb_document.folder_id).

需求（2026-10-10）：知识库文档多级目录分类。决策：
- 单归属：一个文档只能在一个目录（kb_document.folder_id，"" = KB 根）。
- 递归子树浏览：点父目录显示整棵子树文档（服务层展开 folder 集合）。
- 非空目录禁止删除（须先移走子目录和文档）。
- 仅文档列表浏览用，不参与检索范围。
- 上传新文档时可选目录。

幂等：doc_folder 表不存在才建；kb_document 缺 folder_id 列才 ALTER。
auto-migrate 启动时自动执行。

Revision ID: 0004_doc_folder
Revises: 0003_orch_queue_name
Create Date: 2026-10-10

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_doc_folder"
down_revision: Union[str, None] = "0003_orch_queue_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "doc_folder" not in insp.get_table_names():
        op.create_table(
            "doc_folder",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("kb_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("parent_id", sa.String(64), nullable=True, server_default=""),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index("ix_doc_folder_kb_id", "doc_folder", ["kb_id"])
        op.create_index("ix_doc_folder_parent_id", "doc_folder", ["parent_id"])
    if "kb_document" in insp.get_table_names() and not _column_exists("kb_document", "folder_id"):
        op.add_column(
            "kb_document",
            sa.Column("folder_id", sa.String(64), nullable=True, server_default=""),
        )
        op.create_index("ix_kb_document_folder_id", "kb_document", ["folder_id"])


def downgrade() -> None:
    if _column_exists("kb_document", "folder_id"):
        op.drop_index("ix_kb_document_folder_id", table_name="kb_document")
        op.drop_column("kb_document", "folder_id")
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "doc_folder" in insp.get_table_names():
        op.drop_table("doc_folder")
