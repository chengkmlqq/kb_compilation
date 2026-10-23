"""Add job business-reference denormalized columns (modo_job run_id/kb_id/doc_id/datasource_id).

背景（2026-10-21 性能修复）：
- task_params 是 Text JSON，此前按业务引用检索只能 `task_params LIKE '%runId%'` /
  `LIKE '%kb_id%'` / `LIKE '%datasource_id%'` 全文扫（jobs 列表/统计、文档列表 WIKI 状态、
  元数据采集记录共 4 处），随任务量线性变慢且无索引可用。
- 修复：modo_job 加 run_id/kb_id/doc_id/datasource_id 冗余列 + 索引；写入侧由
  api/models/framework.py 的 before_insert/before_update 事件自动从 task_params 提取
  （所有创建路径零改动）。本迁移补列 + 存量回填（Python 遍历解析 JSON，跨 MySQL/PG 通用）。

Revision ID: 0035_job_ref_columns
Revises: 0034_chat_message_attachment_ids
Create Date: 2026-10-21
"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0035_job_ref_columns"
down_revision: Union[str, None] = "0034_chat_message_attachment_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names()


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


def _index_exists(table: str, index: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return index in [i["name"] for i in insp.get_indexes(table)]


def _extract_refs(task_params: str | None) -> dict[str, str]:
    """与 api/models/framework.py._extract_job_refs 同款提取（迁移回填用，勿漂移）。"""
    if not task_params:
        return {}
    try:
        params = json.loads(task_params)
    except (TypeError, ValueError):
        return {}
    if not isinstance(params, dict):
        return {}
    config = params.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    refs: dict[str, str] = {}
    run_id = params.get("runId") or params.get("run_id")
    if run_id:
        refs["run_id"] = str(run_id)
    kb_id = params.get("kbId") or params.get("kb_id") or config.get("kb_id")
    if kb_id:
        refs["kb_id"] = str(kb_id)
    doc_id = (
        params.get("documentId")
        or params.get("document_id")
        or config.get("knowledge_id")
        or config.get("kid")
        or config.get("doc_name")
    )
    if doc_id:
        refs["doc_id"] = str(doc_id)
    ds_id = params.get("datasource_id") or params.get("datasourceId")
    if ds_id:
        refs["datasource_id"] = str(ds_id)
    return refs


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists("modo_job"):
        return
    new_cols = ["run_id", "kb_id", "doc_id", "datasource_id"]
    for col in new_cols:
        if not _column_exists("modo_job", col):
            op.add_column("modo_job", sa.Column(col, sa.String(64), nullable=True))
        if not _index_exists("modo_job", f"ix_modo_job_{col}"):
            op.execute(f"CREATE INDEX ix_modo_job_{col} ON modo_job ({col})")

    # ── 存量回填：Python 逐行解析（跨 MySQL/PG 通用，JSON 提取函数方言差异大）──
    # 主键游标分页（id 字典序），每行至多处理一次；task_params 无引用的行跳过，
    # 不阻塞游标前进（NULL 过滤 + LIMIT 分页会在无引用行上原地打转且漏扫尾部）。
    table = sa.table(
        "modo_job",
        sa.column("id", sa.String),
        sa.column("task_params", sa.Text),
        sa.column("run_id", sa.String),
        sa.column("kb_id", sa.String),
        sa.column("doc_id", sa.String),
        sa.column("datasource_id", sa.String),
    )
    batch = 500
    last_id: str | None = None
    while True:
        stmt = sa.select(table.c.id, table.c.task_params)
        if last_id is not None:
            stmt = stmt.where(table.c.id > last_id)
        stmt = stmt.order_by(table.c.id).limit(batch)
        rows = bind.execute(stmt).fetchall()
        if not rows:
            break
        for row_id, task_params in rows:
            refs = _extract_refs(task_params)
            if refs:
                bind.execute(
                    sa.update(table)
                    .where(table.c.id == row_id)
                    .values(
                        run_id=refs.get("run_id"),
                        kb_id=refs.get("kb_id"),
                        doc_id=refs.get("doc_id"),
                        datasource_id=refs.get("datasource_id"),
                    )
                )
        last_id = str(rows[-1][0])


def downgrade() -> None:
    if not _table_exists("modo_job"):
        return
    for col in ["run_id", "kb_id", "doc_id", "datasource_id"]:
        if _index_exists("modo_job", f"ix_modo_job_{col}"):
            op.execute(f"DROP INDEX ix_modo_job_{col} ON modo_job")
        if _column_exists("modo_job", col):
            op.execute(f"ALTER TABLE modo_job DROP COLUMN {col}")
