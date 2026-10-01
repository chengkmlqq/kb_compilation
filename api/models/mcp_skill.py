"""Scoped MCP server registry ORM model.

Business scoping mirrors kb_model:
- personal: only the owning user can see/use
- team:     every member of the owning team can see/use
- system:   only administrators can see/use

The MCP server *configuration* lives here (kb_compilation is the source of
truth); the agent-gateway acts purely as an executor — task submission
carries the config the task needs (2A), so the gateway never stores MCP
config itself.

Secret values (headers values that look like keys/tokens) are AES-encrypted
at rest via api.lib.crypto.aes_encrypt and masked on read.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


class KbMcpServer(Base):
    __tablename__ = "kb_mcp_server"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # personal | team | system
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 服务名（网关侧 server name，唯一；agent 按名引用）
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # streamable_http | stdio
    type: Mapped[str] = mapped_column(String(32), default="streamable_http")
    url: Mapped[str | None] = mapped_column(Text)
    # 额外请求头 {X-API-Key/Authorization/...}; 敏感值加密存
    headers: Mapped[dict | None] = mapped_column(JSON)
    # stdio 启动配置（command/args/env），streamable_http 时为空
    command: Mapped[str | None] = mapped_column(String(255))
    args: Mapped[list | None] = mapped_column(JSON)
    env: Mapped[dict | None] = mapped_column(JSON)

    # --- scope ownership ---
    owner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_team_name: Mapped[str | None] = mapped_column(String(64), index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 1=active 0=deleted
    state: Mapped[str] = mapped_column(String(8), default="1")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


class KbSkill(Base):
    __tablename__ = "kb_skill"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # personal | team | system
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 技能名（frontmatter name，与 ZIP 内 SKILL.md 一致）
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(32))
    # ZIP 安装包（原始上传字节，base 存储；上传/编辑时整包替换）
    # MEDIUMBLOB（16MB）：技能包含 scripts/templates 可达数 MB，BLOB(64KB) 放不下
    package_zip: Mapped[bytes | None] = mapped_column(LargeBinary(length=16 * 1024 * 1024))
    package_size: Mapped[int | None] = mapped_column(Integer)

    # --- scope ownership ---
    owner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_team_name: Mapped[str | None] = mapped_column(String(64), index=True)

    state: Mapped[str] = mapped_column(String(8), default="1")  # 1=active 0=deleted
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


__all__ = ["KbMcpServer", "KbSkill"]