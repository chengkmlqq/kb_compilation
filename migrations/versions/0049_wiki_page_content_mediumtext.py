"""wiki_page.content 扩为 MEDIUMTEXT（16MB）——n14 建页内容实测 5.2万+ 字符超 TEXT 64KB 上限失败

背景（2026-10-26 测试发现）：wiki 构建编排 n14（业务实体页建页）生成的内容
可达 5.2 万+ 字符（UTF-8 中文 3 字节/字 ≈ 15 万+ 字节），MySQL TEXT 列（64KB）
无法容纳 → 148+ 个构建 run 报 Data too long for column 'content' 失败。
DocChunk.content 已用 Text().with_variant(MEDIUMTEXT, 'mysql')，WikiPage 对齐同法。

迁移幂等：MySQL 下检查列类型，已是 mediumtext 则跳过；非 MySQL 方言无操作。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0049_wiki_page_content_mediumtext"
down_revision: Union[str, None] = "0048_merge_osa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "wiki_page"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return  # 非 MySQL（如 PG 开发库）Text 即可，无容量问题
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE not in tables:
        return
    cols = {c["name"]: c for c in insp.get_columns(TABLE)}
    if "content" not in cols:
        return
    cur_type = str(cols["content"]["type"]).lower()
    if "mediumtext" in cur_type:
        return  # 已扩容，幂等跳过
    op.alter_column(TABLE, "content", type_=sa.dialects.mysql.MEDIUMTEXT(length=16 * 1024 * 1024))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.alter_column(TABLE, "content", type_=sa.Text())
