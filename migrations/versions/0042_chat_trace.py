"""Create chat_trace — 智能问答过程追踪（思考过程 / 工具调用链 / 检索命中明细）。

背景（2026-10-23 分析需求）：智能问答的历史分析需要定位「知识库知识质量 / 模型能力」
的影响。此前记录面：
- chat_message：content + refs（引用）+ thinking（仅 quick 问答存；agent 问答不存思考全文）
- kb_llm_usage：每次 LLM 调用的 tokens/耗时/重试/模型
缺失：agent 问答的思考过程全文、工具调用链明细（名称/参数/结果/命中/耗时）、
检索命中明细、回合级时序 —— 全部只在 SSE 事件流里出现，前端用完即弃。

chat_trace 按「assistant 消息」一条记录（无正文/空响应也记录，供失败分析）：
- thinking：reasoning_content 全文
- events：时序事件数组 [{t:stage|context|thinking|tool_call|tool_result|done, ms:相对耗时,…}]
- hits：检索命中明细（chunk_id/document_id/kb_id/score/content 截断）
- summary：汇总 JSON（rounds/tool_calls/retrieval_hits/dur_ms/error）

迁移幂等：表已存在则跳过（dev 双表由 create_all 按模型补建）。

Revision ID: 0042_chat_trace
Revises: 0041_quality_eval_file_tables
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0042_chat_trace"
down_revision: Union[str, None] = "0041_quality_eval_file_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chat_trace" in set(insp.get_table_names()):
        return
    op.create_table(
        "chat_trace",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("team_name", sa.String(64), nullable=True),
        sa.Column("kb_id", sa.String(64), nullable=True),
        sa.Column("agent_id", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="quick"),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("thinking", sa.Text(), nullable=True),
        sa.Column("events", sa.Text(), nullable=True),
        sa.Column("hits", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=True),
    )
    op.create_index("ix_chat_trace_session", "chat_trace", ["session_id"])
    op.create_index("ix_chat_trace_message", "chat_trace", ["message_id"])
    op.create_index("ix_chat_trace_user_created", "chat_trace", ["user_id", "created_at"])
    op.create_index("ix_chat_trace_kb", "chat_trace", ["kb_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chat_trace" not in set(insp.get_table_names()):
        return
    op.drop_table("chat_trace")