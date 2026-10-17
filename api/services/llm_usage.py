"""LLM 使用记录写入与业务动作分类（2026-10-16 LLM 使用看板）。

三处埋点共用本模块：
- 智能问答流式（api/services/chat.py）→ source="chat"
- wiki 构建管线（worker/builtin_engine/*/weknora_rpc.py llm_call）→ source="wiki_build"
- agent 任务（worker/tasks/agent_worker.py）→ source="agent"

写入约定：
- 一次逻辑调用一条记录；wiki 构建的重试收敛在一条里（retries 计数）。
- best-effort：任何写库失败只记日志，绝不抛异常阻断业务链路。
- 字段全可移植 SQL 类型，与业务表同存 framework store。

业务动作分类（看板操作类型下拉/分布图）：
operation_type 存 key（如 qa/summary/entity/relation），展示层映射中文标签。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from api.models.knowledge import LlmUsageRecord

logger = logging.getLogger(__name__)

# 业务动作枚举（key → 中文标签）；看板分布图/筛选下拉用
OP_TYPES: dict[str, str] = {
    "qa": "问答生成",          # 智能问答流式调用
    "agent": "智能体对话",     # agent 任务内联运行
    "parse": "文档解析",       # 长句切分/解析类 LLM 步骤
    "summary": "摘要提取",     # 摘要页生成
    "entity": "实体抽取",      # 术语/实体抽取
    "relation": "知识图谱生成",  # 关系构建/隐性关联挖掘
    "rule": "规则抽取",        # 规则/条款抽取
    "topic": "主题生成",       # 主题目录/主题知识生成
    "filter": "术语过滤",      # LLM 术语筛选
    "other": "其他处理",       # 未归类步骤
}

# wiki 构建 phase（_phase_ctx / set_phase 的值）→ 业务动作 key
PHASE_TO_OP: dict[str, str] = {
    "long_sentences": "parse",
    "terms": "entity",
    "extract_entities": "entity",
    "filter_terms": "filter",
    "extract_joint": "rule",
    "rules": "rule",
    "gen_summary": "summary",
    "build_relations": "relation",
    "describe_hidden_relations": "relation",
    "extract_rules": "rule",
    "gen_topic_pages": "topic",
    "backfill_ls_summary": "summary",
    "summary": "summary",
}

# 错误分类：error_msg 关键字 → status（明细页「错误请求」Tab 快速定位失败原因）
ERROR_CLASSIFY: list[tuple[tuple[str, ...], str]] = [
    (("context_length", "context length", "context window", "maximum context", "prompt is too long", "输入长度"), "context_overflow"),
    (("timeout", "timed out", "slow_stream", "slow stream", "挂起", "超时"), "timeout"),
]


def map_phase_to_operation(phase: str) -> str:
    """构建 phase → 业务动作 key；未命中归 other。"""
    if not phase:
        return "other"
    return PHASE_TO_OP.get(phase, "other")


def classify_error(error_msg: str) -> str:
    """错误消息 → status（context_overflow / timeout / error 兜底）。"""
    if not error_msg:
        return "error"
    lowered = error_msg.lower()
    for keywords, status in ERROR_CLASSIFY:
        if any(k in lowered for k in keywords):
            return status
    return "error"


def new_trace_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def _as_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def compute_tps(output_tokens: int, total_ms: Optional[int], ttft_ms: Optional[float]) -> Optional[float]:
    """输出 TPS = output_tokens / 生成耗时秒（total - ttft；缺失时用 total）。"""
    out = _as_int(output_tokens)
    if out <= 0 or not total_ms or total_ms <= 0:
        return None
    gen_ms = total_ms - (ttft_ms or 0)
    if gen_ms <= 0:
        gen_ms = total_ms
    return round(out / (gen_ms / 1000.0), 1)


def record_llm_usage(
    *,
    trace_id: str = "",
    source: str = "",
    kb_id: str = "",
    kb_name: str = "",
    doc_id: str = "",
    doc_name: str = "",
    model: str = "",
    operation_type: str = "",
    phase: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    ttft_ms: Optional[float] = None,
    total_ms: Optional[int] = None,
    status: str = "success",
    error_msg: str = "",
    retries: int = 0,
    ts: Optional[datetime] = None,
    db: Optional[Session] = None,
) -> None:
    """写入一条 LLM 使用记录。best-effort：失败仅日志，不抛异常。

    db 传入（测试用 sqlite 会话）时直写，否则用框架 sessionmaker。
    """
    try:
        record = LlmUsageRecord(
            trace_id=trace_id or new_trace_id(),
            ts=ts or datetime.now(timezone.utc).replace(tzinfo=None),
            source=(source or "")[:16],
            kb_id=(kb_id or "")[:64] or None,
            kb_name=(kb_name or "")[:128] or None,
            doc_id=(doc_id or "")[:64] or None,
            doc_name=(doc_name or "")[:255] or None,
            model=(model or "")[:128] or None,
            operation_type=(operation_type or "other")[:32],
            phase=(phase or "")[:48] or None,
            input_tokens=_as_int(input_tokens),
            output_tokens=_as_int(output_tokens),
            cache_read_tokens=_as_int(cache_read_tokens),
            cache_creation_tokens=_as_int(cache_creation_tokens),
            ttft_ms=ttft_ms,
            total_ms=total_ms,
            tps=compute_tps(output_tokens, total_ms, ttft_ms),
            status=(status or "success")[:24],
            error_msg=(error_msg or "")[:2000] or None,
            retries=_as_int(retries),
        )
        if db is not None:
            db.add(record)
            db.commit()
            return
        from api.db import get_sessionmaker

        sdb = get_sessionmaker()()
        try:
            sdb.add(record)
            sdb.commit()
        finally:
            sdb.close()
    except Exception as exc:  # noqa: BLE001 — 埋点绝不阻断业务
        logger.debug("record_llm_usage failed: %s", exc)
