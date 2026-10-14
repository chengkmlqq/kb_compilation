"""Add LLM-config columns on ontology_schema + doc-type build-mode table.

背景（2026-10-14）：本体 Schema 此前只覆盖「抽取分类结构」（类别/提示/图谱边），
不足以驱动跨领域 wiki 构建编排：
- expert_role        领域专家角色（G1）——所有 LLM prompt 的 system 角色
- extract_rules      抽取总则（G2）——JOINT 抽取的硬约束 + JSON 输出格式示例
- summary_config     摘要卡片配置（G3）——摘要专家角色 + 卡片字段 + 关系表规则
- term_filter_config 术语筛选配置（G4）——保留/剔除标准 + 文种词表

以上 4 项为 schema 级（每套领域一份），沿用 ontology_schema 现有 schema_label/
schema_desc 的冗余行设计（同 schema 所有分类行同值），管理页/API 按 schema 编辑。

文档类型维度（G5）：kb_tape_category 二级分类（210 个文档类型）→ 构建模式映射，
新表 kb_doc_type_config：build_mode ∈ full|notice|table（v1），config JSON 可扩展。
种子：从 kb_tape_category 二级类型全量登记，按类型名关键词判默认模式——
  notice: 含 通知/公告/修订/通报/批复/请示
  table : 含 清单/审批/记录/台账/明细/报表/统计/一览/汇总/表格/资料
  其余 full

Revision ID: 0022_ontology_schema_llm_config
Revises: 0021_tape_category_parent
Create Date: 2026-10-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0022_ontology_schema_llm_config"
down_revision: Union[str, None] = "0021_tape_category_parent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _col_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


NOTICE_KEYS = ("通知", "公告", "修订", "通报", "批复", "请示")
TABLE_KEYS = ("清单", "审批", "记录", "台账", "明细", "报表", "统计", "一览", "汇总", "表格", "资料")


def _mode_for(doc_type: str) -> str:
    for k in TABLE_KEYS:
        if k in doc_type:
            return "table"
    for k in NOTICE_KEYS:
        if k in doc_type:
            return "notice"
    return "full"


def upgrade() -> None:
    # 1) ontology_schema 补 4 个 schema 级 LLM 配置列
    if _table_exists("ontology_schema"):
        for col, ddl in (
            ("expert_role", sa.String(256)),
            ("extract_rules", sa.Text()),
            ("summary_config", sa.Text()),
            ("term_filter_config", sa.Text()),
        ):
            if not _col_exists("ontology_schema", col):
                op.add_column("ontology_schema", sa.Column(col, ddl, nullable=True))

    # 2) 文档类型构建模式表
    if not _table_exists("kb_doc_type_config"):
        op.create_table(
            "kb_doc_type_config",
            sa.Column("doc_type", sa.String(64), primary_key=True),  # = kb_tape_category.category（二级）
            sa.Column("build_mode", sa.String(16), nullable=False, server_default="full"),
            sa.Column("config", sa.Text(), nullable=True),
            sa.Column("state", sa.String(1), nullable=False, server_default="1"),
            sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        # 种子：kb_tape_category 二级类型全量登记（幂等；已有行不动）
        if _table_exists("kb_tape_category") and _col_exists("kb_tape_category", "parent_id"):
            conn = op.get_bind()
            rows = conn.execute(
                sa.text(
                    "SELECT DISTINCT category FROM kb_tape_category "
                    "WHERE parent_id IS NOT NULL AND state = '1' ORDER BY category"
                )
            ).fetchall()
            for (doc_type,) in rows:
                exists = conn.execute(
                    sa.text("SELECT 1 FROM kb_doc_type_config WHERE doc_type = :t"),
                    {"t": doc_type},
                ).fetchone()
                if exists:
                    continue
                mode = _mode_for(doc_type or "")
                conn.execute(
                    sa.text(
                        "INSERT INTO kb_doc_type_config (doc_type, build_mode, sort) "
                        "VALUES (:t, :m, 0)"
                    ),
                    {"t": doc_type, "m": mode},
                )


def downgrade() -> None:
    if _table_exists("kb_doc_type_config"):
        op.drop_table("kb_doc_type_config")
    if _table_exists("ontology_schema"):
        for col in ("expert_role", "extract_rules", "summary_config", "term_filter_config"):
            if _col_exists("ontology_schema", col):
                op.drop_column("ontology_schema", col)
