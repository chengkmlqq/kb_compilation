"""merge 0030 双头收敛（0032）

0030_image_chunk_type（7465d99，ingest 图片子 chunk 可检索）与
0030_wiki_feedback_preset（00f7a1d，高频词标注）由并行提交各自挂在
0029_overview_trend_indexes 之下，形成两个 head → api-server 启动
alembic upgrade head 报 Multiple head revisions 直接退出（fail-fast）。

本合并迁移收敛两个分支：
  0029 → 0030_image_chunk_type →┐
                                 ├→ 0032_merge_0030_heads (head)
  0029 → 0030_wiki_feedback_preset → 0031_wiki_feedback →┘

两分支操作对象不重叠（doc_chunk.chunk_type vs wiki_feedback.preset），
合并本身为空操作。

revision id <= 32 字符（0032_merge_0030_heads = 20 字符）。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0032_merge_0030_heads"
down_revision: tuple[str, ...] | None = ("0030_image_chunk_type", "0031_wiki_feedback")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
