"""Framework-layer ORM models.

Ported 1:1 from the source platform's Drizzle schema. Only the framework tables are
kept — the legacy data-synthesis business tables (tape / training /
raw_dataset / quality / sensitive / requirement / wizard) are intentionally
excluded. Those table names appear verbatim in the exclusion test because
they are physical names in the shared source database.

Column types and table/column/index names are preserved EXACTLY so this service
can share the same database as the existing legacy deployment.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


# ============================================================================
# Auth / RBAC  (modo_user / modo_team / modo_menu)
# ============================================================================


class User(Base):
    __tablename__ = "modo_user"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    user_name: Mapped[str | None] = mapped_column(String(128))
    user_pwd: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(16))
    create_dt: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(1))
    default_team: Mapped[str | None] = mapped_column(String(32))


class UserRole(Base):
    __tablename__ = "modo_user_role"

    role_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    create_date: Mapped[str | None] = mapped_column(String(32))
    role_descr: Mapped[str | None] = mapped_column(String(128))
    role_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role_type: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(1), nullable=False)


class UserRoleRela(Base):
    __tablename__ = "modo_user_role_rela"

    rela_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)


class Team(Base):
    __tablename__ = "modo_team"

    team_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    create_dt: Mapped[str | None] = mapped_column(String(32))
    create_user: Mapped[str | None] = mapped_column(String(32))
    descr: Mapped[str | None] = mapped_column(String(1024))
    label: Mapped[str | None] = mapped_column(String(512))
    parent_team_id: Mapped[str | None] = mapped_column(String(32))
    parent_team_name: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(1))
    team_name: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 digest (hex) of the team MCP token; empty = not configured.
    mcp_token_hash: Mapped[str | None] = mapped_column(String(64))
    mcp_token_updated_at: Mapped[str | None] = mapped_column(String(32))


class TeamMember(Base):
    __tablename__ = "modo_team_member"

    member_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    create_dt: Mapped[str | None] = mapped_column(String(32))
    create_user: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32))
    team_name: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    role_name: Mapped[str | None] = mapped_column(String(32))


class Menu(Base):
    __tablename__ = "modo_menu"

    menu_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    create_date: Mapped[str | None] = mapped_column(String(32))
    menu_descr: Mapped[str | None] = mapped_column(String(2048))
    menu_ext_conf: Mapped[str | None] = mapped_column(String(4000))
    menu_icon: Mapped[str | None] = mapped_column(String(32))
    menu_label: Mapped[str | None] = mapped_column(String(256))
    menu_name: Mapped[str | None] = mapped_column(String(128))
    menu_type: Mapped[str | None] = mapped_column(String(32))
    route: Mapped[str | None] = mapped_column(String(512))
    parent_id: Mapped[str | None] = mapped_column(String(64))
    sort_num: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(32))


class RoleMenuRela(Base):
    __tablename__ = "modo_role_menu_rela"

    rela_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    menu_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)


class TeamDsMap(Base):
    __tablename__ = "modo_team_ds_map"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ds_name: Mapped[str | None] = mapped_column(String(64))
    schema_name: Mapped[str | None] = mapped_column(String(64))
    team_name: Mapped[str | None] = mapped_column(String(64))
    is_prod: Mapped[str | None] = mapped_column(String(32))


# ============================================================================
# System dictionary (modo_dim)
# ============================================================================


class Dim(Base):
    __tablename__ = "modo_dim"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    dim_code: Mapped[str] = mapped_column(String(64), nullable=False)
    dim_group: Mapped[str | None] = mapped_column(String(32))
    dim_value: Mapped[str | None] = mapped_column(Text)
    dim_desc: Mapped[str | None] = mapped_column(Text)
    parent_dim_code: Mapped[str | None] = mapped_column(String(32))
    seq: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(8))


# ============================================================================
# Datasource & metadata (modo_datasource / modo_metadata_*)
# ============================================================================


class Datasource(Base):
    __tablename__ = "modo_datasource"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column("ds_name", String(64))
    label: Mapped[str | None] = mapped_column("ds_label", String(128))
    ds_acct: Mapped[str | None] = mapped_column(String(128))
    ds_auth: Mapped[str | None] = mapped_column(String(512))
    ds_category: Mapped[str | None] = mapped_column(String(32))
    ds_type: Mapped[str | None] = mapped_column(String(32))
    ds_version: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(256))
    ds_conf: Mapped[str | None] = mapped_column(String(512))
    state: Mapped[str | None] = mapped_column(String(32))
    create_user: Mapped[str | None] = mapped_column(String(64))
    create_date: Mapped[str | None] = mapped_column(String(32))
    last_upd_date: Mapped[str | None] = mapped_column(String(32))
    update_user: Mapped[str | None] = mapped_column(String(64))


class GridDatasource(Base):
    __tablename__ = "modo_grid_datasource"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column("ds_name", String(64))
    label: Mapped[str | None] = mapped_column("ds_label", String(128))
    ds_acct: Mapped[str | None] = mapped_column(String(128))
    ds_auth: Mapped[str | None] = mapped_column(String(512))
    ds_category: Mapped[str | None] = mapped_column(String(32))
    ds_type: Mapped[str | None] = mapped_column(String(32))
    ds_version: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(256))
    ds_conf: Mapped[str | None] = mapped_column(String(512))
    state: Mapped[str | None] = mapped_column(String(32))
    sort_num: Mapped[int | None] = mapped_column(Integer)


class DsCategory(Base):
    __tablename__ = "modo_ds_category"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    category_name: Mapped[str | None] = mapped_column(String(32))
    category_label: Mapped[str | None] = mapped_column(String(64))
    sorted: Mapped[int | None] = mapped_column(Integer)


class DsFormField(Base):
    __tablename__ = "modo_ds_form_field"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ds_type: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(64))
    widget: Mapped[str | None] = mapped_column(String(64))
    sorted: Mapped[int | None] = mapped_column(Integer)
    default_value: Mapped[str | None] = mapped_column(String(1024))
    invisible: Mapped[int | None] = mapped_column(Integer)
    is_conf: Mapped[int | None] = mapped_column(Integer)
    options: Mapped[str | None] = mapped_column(String(4000))
    place_hold: Mapped[str | None] = mapped_column(String(1024))
    regex: Mapped[str | None] = mapped_column(String(1024))
    request_api: Mapped[str | None] = mapped_column(String(256))
    required: Mapped[int | None] = mapped_column(Integer)
    tooltip: Mapped[str | None] = mapped_column(String(4000))
    valid_info: Mapped[str | None] = mapped_column(String(1024))
    ds_version: Mapped[str | None] = mapped_column(String(32))


class DsType(Base):
    __tablename__ = "modo_ds_type"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ds_type: Mapped[str | None] = mapped_column(String(32))
    ds_type_label: Mapped[str | None] = mapped_column(String(64))
    ds_category: Mapped[str | None] = mapped_column(String(32))
    img: Mapped[str | None] = mapped_column(Text)
    sorted: Mapped[int | None] = mapped_column(Integer)
    is_support: Mapped[str | None] = mapped_column(String(32))


class DsVersion(Base):
    __tablename__ = "modo_ds_version"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ds_type: Mapped[str | None] = mapped_column(String(32))
    version_name: Mapped[str | None] = mapped_column(String(64))
    version_value: Mapped[str | None] = mapped_column(String(32))
    sorted: Mapped[int | None] = mapped_column(Integer)


class MetadataTable(Base):
    __tablename__ = "modo_metadata_table"
    __table_args__ = (
        Index("idx_metadata_datasource_id", "datasource_id"),
        Index("idx_metadata_table_name", "table_name"),
        Index("unique_metadata_table_key", "datasource_id", "schema_name", "table_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_name: Mapped[str | None] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_type: Mapped[str | None] = mapped_column(String(32))
    table_comment: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    table_size: Mapped[int | None] = mapped_column(BigInteger)
    create_time: Mapped[str | None] = mapped_column(String(32))
    update_time: Mapped[str | None] = mapped_column(String(32))
    collection_time: Mapped[str] = mapped_column(String(32), nullable=False)
    collection_config: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(16), default="active")


class MetadataColumn(Base):
    __tablename__ = "modo_metadata_column"
    __table_args__ = (
        Index("idx_metadata_column_table_id", "metadata_table_id"),
        Index("idx_metadata_column_name", "column_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    metadata_table_id: Mapped[str] = mapped_column(String(32), nullable=False)
    column_name: Mapped[str] = mapped_column(String(256), nullable=False)
    column_type: Mapped[str | None] = mapped_column(String(128))
    data_type: Mapped[str | None] = mapped_column(String(64))
    column_length: Mapped[int | None] = mapped_column(Integer)
    column_precision: Mapped[int | None] = mapped_column(Integer)
    column_scale: Mapped[int | None] = mapped_column(Integer)
    is_nullable: Mapped[str | None] = mapped_column(String(3), default="YES")
    column_default: Mapped[str | None] = mapped_column(Text)
    column_comment: Mapped[str | None] = mapped_column(Text)
    ordinal_position: Mapped[int | None] = mapped_column(Integer)
    is_primary_key: Mapped[int | None] = mapped_column(Integer, default=0)
    is_unique: Mapped[int | None] = mapped_column(Integer, default=0)
    collection_time: Mapped[str] = mapped_column(String(32), nullable=False)


# ============================================================================
# Scheduling (modo_cron_task / modo_job / modo_job_queue)
# ============================================================================


class CronTask(Base):
    __tablename__ = "modo_cron_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(128))
    next_fire_time: Mapped[str | None] = mapped_column(String(32))
    cron_expression: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32))
    task_class: Mapped[str | None] = mapped_column(String(512))
    fire_params: Mapped[str | None] = mapped_column(Text)
    queue_name: Mapped[str | None] = mapped_column(String(128))


class Job(Base):
    __tablename__ = "modo_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(64))
    task_class: Mapped[str | None] = mapped_column(String(512))
    queue_name: Mapped[str | None] = mapped_column(String(128))
    task_params: Mapped[str | None] = mapped_column(Text)
    trigger_type: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(64))
    start_time: Mapped[object | None] = mapped_column(DateTime)
    end_time: Mapped[object | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    log_path: Mapped[str | None] = mapped_column(String(1024))
    error_message: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class JobQueue(Base):
    __tablename__ = "modo_job_queue"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    queue_name: Mapped[str | None] = mapped_column(String(128))
    queue_label: Mapped[str | None] = mapped_column(String(128))
    queue_size: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(64))
    create_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


# ============================================================================
# Logging (modo_oper_log / modo_sync_* / modo_interface_log / modo_system_message)
# ============================================================================


class OperLog(Base):
    __tablename__ = "modo_oper_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    user_name: Mapped[str | None] = mapped_column(String(64))
    team_name: Mapped[str | None] = mapped_column(String(64))
    oper_time: Mapped[str | None] = mapped_column(String(32))
    oper_type: Mapped[str | None] = mapped_column(String(32))
    client_id: Mapped[str | None] = mapped_column(String(64))
    server_id: Mapped[str | None] = mapped_column(String(64))
    server_port: Mapped[int | None] = mapped_column(Integer)
    oper_url: Mapped[str | None] = mapped_column(String(512))
    menu_id: Mapped[str | None] = mapped_column(String(64))
    oper_content: Mapped[str | None] = mapped_column(Text)


class SyncLog(Base):
    __tablename__ = "modo_sync_log"
    __table_args__ = (
        Index("idx_sync_log_sync_type", "sync_type"),
        Index("idx_sync_log_status", "sync_status"),
        Index("idx_sync_log_create_time", "create_time"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sync_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_full_sync: Mapped[str] = mapped_column(String(1), nullable=False, default="0")
    page_num: Mapped[int | None] = mapped_column(Integer)
    page_size: Mapped[int | None] = mapped_column(Integer)
    total_count: Mapped[int | None] = mapped_column(Integer)
    success_count: Mapped[int | None] = mapped_column(Integer)
    failed_count: Mapped[int | None] = mapped_column(Integer)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    query_start_time: Mapped[str | None] = mapped_column(String(32))
    trigger_type: Mapped[str | None] = mapped_column(String(16))
    job_id: Mapped[str | None] = mapped_column(String(64))
    start_time: Mapped[object | None] = mapped_column(DateTime)
    end_time: Mapped[object | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    create_user: Mapped[str | None] = mapped_column(String(64))
    create_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    response_data: Mapped[str | None] = mapped_column(Text)


class SyncDetail(Base):
    __tablename__ = "modo_sync_detail"
    __table_args__ = (
        Index("idx_sync_detail_log_id", "sync_log_id"),
        Index("idx_sync_detail_sync_type", "sync_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sync_log_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(16), nullable=False)
    data_code: Mapped[str | None] = mapped_column(String(64))
    data_name: Mapped[str | None] = mapped_column(String(128))
    sync_action: Mapped[str | None] = mapped_column(String(16))
    create_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class InterfaceLog(Base):
    __tablename__ = "modo_interface_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100))
    label: Mapped[str | None] = mapped_column(String(200))
    start_time: Mapped[object | None] = mapped_column(DateTime)
    end_time: Mapped[object | None] = mapped_column(DateTime)
    exec_params: Mapped[str | None] = mapped_column(Text)
    exec_time: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(32))
    exec_result: Mapped[str | None] = mapped_column(Text)
    log_source: Mapped[str | None] = mapped_column(String(20), default="internal")
    request_method: Mapped[str | None] = mapped_column(String(10))
    request_path: Mapped[str | None] = mapped_column(String(500))
    request_headers: Mapped[object | None] = mapped_column(JSON)
    request_body: Mapped[str | None] = mapped_column(Text)


class Seq(Base):
    __tablename__ = "modo_seq"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq_type: Mapped[str | None] = mapped_column(String(64))
    dayly: Mapped[str | None] = mapped_column(String(1))  # '0' or '1'
    seq_val: Mapped[int | None] = mapped_column(Integer)


# ============================================================================
# Files (modo_sys_file)
# ============================================================================


class SysFile(Base):
    __tablename__ = "modo_sys_file"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_extension: Mapped[str | None] = mapped_column(String(50))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    storage_type: Mapped[str] = mapped_column(String(50), nullable=False)  # s3/sftp/local
    ds_name: Mapped[str | None] = mapped_column(String(64))
    bucket_name: Mapped[str | None] = mapped_column(String(128))
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_module: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    create_date: Mapped[str | None] = mapped_column(String(32))
    update_date: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32), default="1")  # 1-正常, 0-已删除


class OperationDoc(Base):
    """Operation/usage documentation entries (kept: framework asset)."""

    __tablename__ = "modo_operation_doc"
    __table_args__ = (
        Index("idx_operation_doc_team_id", "team_id"),
        Index("idx_operation_doc_category", "category"),
        Index("idx_operation_doc_sys_file_id", "sys_file_id"),
        Index("idx_operation_doc_published", "is_published"),
        Index("idx_operation_doc_sort_order", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[str | None] = mapped_column(String(500))
    sys_file_id: Mapped[str | None] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_published: Mapped[str] = mapped_column(String(1), nullable=False, default="1")
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    create_date: Mapped[str | None] = mapped_column(String(32))
    update_date: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32), default="1")


# ============================================================================
# AI chat (ai_chat_conversation / ai_chat_message) — reused by the QA feature
# ============================================================================


class AiChatConversation(Base):
    __tablename__ = "ai_chat_conversation"
    __table_args__ = (Index("idx_ai_chat_conversation_user_update", "user_id", "update_time"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(128))
    team_name: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="新会话")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    create_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    update_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


class AiChatMessage(Base):
    __tablename__ = "ai_chat_message"
    __table_args__ = (Index("idx_ai_chat_message_conversation", "conversation_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user/assistant/system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    feedback: Mapped[str | None] = mapped_column(String(16))
    create_time: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )


__all__ = [
    "User",
    "UserRole",
    "UserRoleRela",
    "Team",
    "TeamMember",
    "Menu",
    "RoleMenuRela",
    "TeamDsMap",
    "Dim",
    "Datasource",
    "GridDatasource",
    "DsCategory",
    "DsFormField",
    "DsType",
    "DsVersion",
    "MetadataTable",
    "MetadataColumn",
    "CronTask",
    "Job",
    "JobQueue",
    "OperLog",
    "SyncLog",
    "SyncDetail",
    "InterfaceLog",
    "Seq",
    "SysFile",
    "OperationDoc",
    "AiChatConversation",
    "AiChatMessage",
]
