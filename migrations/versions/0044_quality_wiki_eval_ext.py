"""Extend kb_quality_wiki_eval with LLM 对照原文评估（v3）字段 + chunk 程序计数列.

背景（2026-10-23 wiki 评估迁移）：单文件粒度「LLM 对照完整原文评估 Wiki」
逻辑从「切片评估」编排迁至「质量评估-QA」n06「Wiki-原文对比评估」节点，
wiki 维度每文件一行明细（kb_quality_wiki_eval）需承载 v3 明细字段：
切片数/均长/Wiki 页数、Wiki 构建起始时间与耗时、文档处理时长、评估耗时、
结论、错误——结构对齐 kb_quality_chunk_eval（v3 原挂靠表）。

同时「切片评估」编排恢复程序版（QC_CODE：过碎/过长/截断/重复）后，
kb_quality_chunk_eval 增加 short/long/truncated/dup 计数列供页面平铺展示。

迁移幂等：列已存在则跳过。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0044_quality_wiki_eval_ext"
down_revision: Union[str, None] = "0043_tape_run_status_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_WIKI = "kb_quality_wiki_eval"
TABLE_CHUNK = "kb_quality_chunk_eval"

# (表, 列名, 类型, 说明)
_WIKI_COLUMNS: list[tuple[str, sa.types.TypeEngine, str]] = [
    ("chunks", sa.Integer(), "该文档切片数"),
    ("avg_len", sa.Float(), "切片均长（字）"),
    ("wiki_pages", sa.Integer(), "该文档构建出的 wiki 页数"),
    ("wiki_start_time", sa.DateTime(), "Wiki 构建起始时间（run 创建，回落最早页创建）"),
    ("wiki_build_sec", sa.Float(), "Wiki 构建耗时（秒）"),
    ("doc_process_sec", sa.Float(), "文档处理时长（秒，负值置空）"),
    ("wiki_tape", sa.String(128), "Wiki 构建编排名"),
    ("eval_duration_sec", sa.Float(), "LLM 评估本文件耗时（秒）"),
    ("conclusion", sa.String(500), "评估结论一句话"),
    ("error", sa.String(500), "LLM 评估失败原因"),
]

# 切片评估恢复程序版（QC_CODE）后，每文件过短/过长/截断/重复计数落库展示
_CHUNK_COLUMNS: list[tuple[str, sa.types.TypeEngine, str]] = [
    ("short", sa.Integer(), "过短切片数（<50 字）"),
    ("long", sa.Integer(), "过长切片数（>2000 字）"),
    ("truncated", sa.Integer(), "含截断标记切片数"),
    ("dup", sa.Integer(), "近似重复切片数"),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE_WIKI in tables:
        existing = {c["name"] for c in insp.get_columns(TABLE_WIKI)}
        for name, col_type, _comment in _WIKI_COLUMNS:
            if name not in existing:
                op.add_column(TABLE_WIKI, sa.Column(name, col_type, nullable=True))
    if TABLE_CHUNK in tables:
        existing = {c["name"] for c in insp.get_columns(TABLE_CHUNK)}
        for name, col_type, _comment in _CHUNK_COLUMNS:
            if name not in existing:
                op.add_column(TABLE_CHUNK, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if TABLE_WIKI in tables:
        existing = {c["name"] for c in insp.get_columns(TABLE_WIKI)}
        for name, _col_type, _comment in _WIKI_COLUMNS:
            if name in existing:
                op.drop_column(TABLE_WIKI, name)
    if TABLE_CHUNK in tables:
        existing = {c["name"] for c in insp.get_columns(TABLE_CHUNK)}
        for name, _col_type, _comment in _CHUNK_COLUMNS:
            if name in existing:
                op.drop_column(TABLE_CHUNK, name)