"""wiki_feedback 维护中心对齐迁移（0031）

对齐 WeKnora wiki_page_feedback 的维护中心形态。注意：preset 列由
0030_wiki_feedback_preset（高频词标注）已加，本迁移不再重复添加：
- 新增反规范化字段 page_title / reported_by_name（列表免 join）
- 新增 updated_at（变更时间）/ deleted_at（软删，对齐 WeKnora gorm.DeletedAt）
- 存量状态 open -> pending（对齐 WeKnora pending 语义）
- 补 kb_id+slug / kb_id+feedback_type+status 复合索引（对齐 WeKnora idx）

revision id <= 32 字符（0031_wiki_feedback = 20 字符）。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0031_wiki_feedback"
down_revision: str | None = "0030_wiki_feedback_preset"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "wiki_feedback"


def _columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {c["name"] for c in inspector.get_columns(TABLE)}


def upgrade() -> None:
    cols = _columns()
    if "page_title" not in cols:
        op.add_column(TABLE, sa.Column("page_title", sa.String(255), server_default="", nullable=False))
    if "reported_by_name" not in cols:
        op.add_column(TABLE, sa.Column("reported_by_name", sa.String(100), server_default="", nullable=False))
    if "updated_at" not in cols:
        op.add_column(TABLE, sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")))
    if "deleted_at" not in cols:
        op.add_column(TABLE, sa.Column("deleted_at", sa.DateTime(), nullable=True))

    # 存量状态 open -> pending（对齐 WeKnora pending 语义）
    op.execute(f"UPDATE {TABLE} SET status = 'pending' WHERE status = 'open'")

    op.create_index(f"ix_{TABLE}_kb_slug", TABLE, ["kb_id", "slug"])
    op.create_index(f"ix_{TABLE}_kb_type_status", TABLE, ["kb_id", "feedback_type", "status"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_kb_type_status", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_kb_slug", table_name=TABLE)
    cols = _columns()
    if "deleted_at" in cols:
        op.drop_column(TABLE, "deleted_at")
    if "updated_at" in cols:
        op.drop_column(TABLE, "updated_at")
    if "reported_by_name" in cols:
        op.drop_column(TABLE, "reported_by_name")
    if "page_title" in cols:
        op.drop_column(TABLE, "page_title")
