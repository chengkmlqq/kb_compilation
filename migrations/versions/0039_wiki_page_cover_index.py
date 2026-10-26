"""Add wiki_page cover index (kb_id, status, page_type) for the /kbs list aggregate.

背景（2026-10-23 知识库列表接口效率实测）：
- GET /api/v1/kbs 的 page_count + keyword_count 条件聚合（api/services/kb_admin.py
  list_kbs）实测 28-80s：聚合需 page_type 判断高频词页，但现有
  (kb_id,status) 索引不含 page_type，每行回表取页类型；且 wiki_page 持续
  写入致统计信息失真，优化器对多库 IN 选 9 万行全扫。
- 覆盖索引 (kb_id, status, page_type)：条件聚合纯索引扫描、零回表，
  优化器无论选什么执行计划都是毫秒级（page_type 枚举值小，索引体积可接受）。
- 2026-10-21 曾用 ANALYZE TABLE 应急（同一条 SQL 28s -> 0.4s），因统计再
  次失真复发，本索引为根治。

迁移幂等：表/列缺失或索引已存在则跳过。dev 双表（wiki_page_dev）由
create_all 按需建表时补建，本迁移只作用于正式表。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0039_wiki_page_cover_index"
down_revision: Union[str, None] = "0038_quality_doc_eval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_wiki_page_kb_status_type"


def _index_exists(bind, table: str, index: str) -> bool:
    insp = sa.inspect(bind)
    return index in [i["name"] for i in insp.get_indexes(table)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "wiki_page" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("wiki_page")}
    if not {"kb_id", "status", "page_type"} <= cols:
        return
    if not _index_exists(bind, "wiki_page", INDEX_NAME):
        op.create_index(INDEX_NAME, "wiki_page", ["kb_id", "status", "page_type"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "wiki_page" not in set(insp.get_table_names()):
        return
    if _index_exists(bind, "wiki_page", INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="wiki_page")