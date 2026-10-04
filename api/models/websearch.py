"""Scoped WebSearch provider registry ORM model.

业务归属语义与 kb_model / kb_mcp_server / kb_skill 完全一致：
- personal: 仅创建者可看/可用
- team:     创建者所在团队全员可看/可用
- system:   仅管理员可看/可用

提供方配置（provider）是数据源注册（谁是搜索服务、用什么 API Key），
执行器（search）按 provider_type 调用对应搜索 API，统一返回
[{title, url, snippet}] 结构。

API Key 属于敏感值，落库前用 api.lib.crypto.aes_encrypt 加密，
读取时脱敏（**** 掩码），仅执行器内部解密后使用。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


class WebSearchProvider(Base):
    __tablename__ = "kb_websearch_provider"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # personal | team | system
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 提供方名称（唯一，用于引用）
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    # tavily | serper | bing | exa | generic
    provider_type: Mapped[str] = mapped_column(String(32), default="generic")
    # 搜索服务 API Key（AES 加密落库）
    api_key: Mapped[str | None] = mapped_column(String(512))
    # 自定义端点（generic 必填；其余类型可为空走官方端点）
    base_url: Mapped[str | None] = mapped_column(Text)
    # 扩展配置（JSON；generic 的请求体模板 / 额外参数等）
    extra_config: Mapped[dict | None] = mapped_column(JSON)

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


__all__ = ["WebSearchProvider"]
