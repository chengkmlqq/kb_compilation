"""本体 Schema 数据模型。

本体 schema 用于配置化定义「抽取分类结构」——每套 schema 描述一个领域
（如市场监管法规、供管制度）的业务本体 + 规则本体双维度分类：
- ontology_schema：schema 及分类行（schema_name+dimension+cat_no 维度隔离）
- kb_ontology_schema：知识库 ↔ schema 绑定（每 KB 一套）

wiki 构建技能从平台读取绑定 schema，动态生成分类目录、LLM 提示词与
Neo4j 边类型，替代脚本内硬编码常量。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


class OntologySchema(Base):
    """一套本体 schema 下的一个分类行（双维度：业务本体/规则本体）。"""

    __tablename__ = "ontology_schema"
    __table_args__ = (
        UniqueConstraint("schema_name", "dimension", "cat_no", name="uk_ontology_schema_cat"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # 领域名（「市场监管法规」）
    schema_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    schema_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)  # business | rule
    cat_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cat_name: Mapped[str] = mapped_column(String(64), nullable=False)  # 分类名（"处罚规则"）
    cat_label: Mapped[str] = mapped_column(String(96), nullable=False, default="")  # 目录名（"9-处罚规则"）
    prompt_hint: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM 提示词段（识别要点）
    neo4j_edge: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 规则维度的 Neo4j 边类型
    state: Mapped[str] = mapped_column(String(1), nullable=False, default="1")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KbOntologySchema(Base):
    """知识库 ↔ 本体 schema 绑定（每 KB 一套）。"""

    __tablename__ = "kb_ontology_schema"

    kb_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )