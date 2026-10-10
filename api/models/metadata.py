"""元数据采集模型（迁移 data-synth 的 modo_metadata_table/column）。

两张表落框架库（MySQL DATABASE_URL，与 modo_datasource 同库）：
- MetadataTable  → kb_metadata_table：某个数据源采集到的表级元数据
- MetadataColumn → kb_metadata_column：表内的字段级元数据

命名说明：ds 用 modo_metadata_* 前缀（drizzle），kb 框架表已统一 kb_* 前缀
（kb_document/kb_step_define/kb_websearch_provider…），故用 kb_metadata_*。
唯一键 (datasource_id, schema_name, table_name) 支撑采集时的 upsert 语义。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


class MetadataTable(Base):
    """表级元数据（一次采集 = 一个 datasource_id 下的若干行）。"""

    __tablename__ = "kb_metadata_table"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_name: Mapped[str | None] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_type: Mapped[str | None] = mapped_column(String(32))
    table_comment: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    table_size: Mapped[int | None] = mapped_column(BigInteger)
    create_time: Mapped[str | None] = mapped_column(String(32))
    update_time: Mapped[str | None] = mapped_column(String(32))
    collection_time: Mapped[str] = mapped_column(String(32), nullable=False)
    # 采集配置快照 JSON（scene_tag / sampling_method / sample_size …）
    collection_config: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(16), default="active")

    __table_args__ = (
        Index("idx_metadata_datasource_id", "datasource_id"),
        Index("idx_metadata_table_name", "table_name"),
        UniqueConstraint(
            "datasource_id", "schema_name", "table_name", name="unique_metadata_table_key"
        ),
    )


class MetadataColumn(Base):
    """字段级元数据（挂 MetadataTable，采集时按 ordinal_position 排序）。"""

    __tablename__ = "kb_metadata_column"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    metadata_table_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    column_name: Mapped[str] = mapped_column(String(256), nullable=False)
    column_type: Mapped[str | None] = mapped_column(String(128))
    data_type: Mapped[str | None] = mapped_column(String(64))
    column_length: Mapped[int | None] = mapped_column(Integer)
    column_precision: Mapped[int | None] = mapped_column(Integer)
    column_scale: Mapped[int | None] = mapped_column(Integer)
    # YES / NO（对齐 ds：varchar(3) 而非 bool，保留数据库原始语义）
    is_nullable: Mapped[str | None] = mapped_column(String(3), default="YES")
    column_default: Mapped[str | None] = mapped_column(Text)
    column_comment: Mapped[str | None] = mapped_column(Text)
    ordinal_position: Mapped[int | None] = mapped_column(Integer)
    is_primary_key: Mapped[int] = mapped_column(Integer, default=0)
    is_unique: Mapped[int] = mapped_column(Integer, default=0)
    collection_time: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        Index("idx_metadata_column_table_id", "metadata_table_id"),
        Index("idx_metadata_column_name", "column_name"),
    )