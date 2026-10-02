"""Scoped model registry ORM model.

Model configurations with three business scopes:

- personal: only the owning user can see and use
- team:     every member of the owning team can see and use
- system:   only administrators can see and use

Stored on the framework relational store (Base) like kb_agent; the API
surface mirrors WeKnora's /models management (CRUD + default + debug),
with secrets AES-encrypted at rest (api.lib.crypto.aes_encrypt) and
masked on every read.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


class KbModel(Base):
    __tablename__ = "kb_model"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # personal | team | system
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 模型名（调用时使用的 model id，如 deepseek-v4-pro）
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    # chat | embedding（与 WeKnora 前端类型名对齐；rerank/vllm/asr 预留）
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # remote | local（Ollama 预留；当前仅 remote）
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="remote")
    # 厂商标识：openai / dashscope / deepseek / siliconflow / zhipu / generic ...
    provider: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    # OpenAI 兼容 /v1 地址
    base_url: Mapped[str | None] = mapped_column(Text)
    # AES 加密落库，读取时解密并在响应中掩码
    api_key: Mapped[str | None] = mapped_column(Text)
    # 接口类型（openai 等），默认 openai
    interface_type: Mapped[str | None] = mapped_column(String(32))
    # embedding 向量维度
    dimension: Mapped[int | None] = mapped_column(Integer)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    # 附加请求头 {name: value}
    custom_headers: Mapped[dict | None] = mapped_column(JSON)

    # --- scope ownership ---
    # personal 归属用户（modo_user.user_id）
    owner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # team 归属团队（modo_team.team_name）
    owner_team_name: Mapped[str | None] = mapped_column(String(64), index=True)

    # 每 (scope, type) 至多一个默认模型
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # chat/embedding/vllm 并发上限（0/空 = 沿用全局默认）
    max_concurrency: Mapped[int | None] = mapped_column(Integer)
    # chat 深度思考控制（off / auto / on，映射到请求参数）
    thinking_control: Mapped[str | None] = mapped_column(String(16))
    # active | downloading | download_failed（Ollama 预留；remote 恒 active）
    status: Mapped[str] = mapped_column(String(32), default="active")
    # 1=active 0=deleted
    state: Mapped[str] = mapped_column(String(8), default="1")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow
    )


__all__ = ["KbModel"]
