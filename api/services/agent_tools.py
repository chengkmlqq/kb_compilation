"""Agent QA 内置工具：知识库检索（kb_search）。

让 LLM 在 agent 多轮循环中按需再次检索当前知识库（对齐 WeKnora 的检索类
工具），工具结果以文本回填，驱动下一轮推理，形成真正的多轮工具调用。

工具注入门禁：
- kb_search 不依赖外部 provider，只要智能体有可用知识库即可注入（默认）；
- 白名单裁剪由路由层（api/routers/agents.py）按 AgentConfig.allowed_tools
  统一处理——非空白名单时仅注入白名单内已装配的工具，空白名单 = 全部允许。
"""

from __future__ import annotations

import json
from typing import Callable

KB_SEARCH_TOOL = "kb_search"

MAX_KB_SEARCH_TOP_K = 20
HIT_TEXT_CHARS = 400


def build_kbsearch_tool(top_k: int) -> list[dict]:
    """kb_search function-calling 负载（OpenAI 兼容）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": KB_SEARCH_TOOL,
                "description": (
                    "知识库检索：给定 query 在当前知识库内做混合检索（向量+关键词），"
                    "返回命中的文档片段（含文档名与分数）。当问题需要查证知识库中的"
                    "具体资料、或需要比前置检索更精确地定位内容时使用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词/问题"},
                        "top_k": {
                            "type": "integer",
                            "description": f"返回片段数（1-{MAX_KB_SEARCH_TOP_K}，默认 {top_k}）",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    ]


def _hits_to_text(hits) -> str:
    """把检索命中转成精简文本回填给 LLM（文档名 + 分数 + 片段前 400 字）。"""
    if not hits:
        return "（知识库检索无命中）"
    lines = []
    for i, h in enumerate(hits, 1):
        title = (h.document_title or "").strip() or (h.document_id or "")[:8] or f"chunk-{i}"
        content = (h.content or "").strip()
        if len(content) > HIT_TEXT_CHARS:
            content = content[:HIT_TEXT_CHARS] + "…"
        lines.append(f"[{i}] {title} (score={h.score:.3f})\n{content}")
    return "\n\n".join(lines)


def agent_kbsearch_tool_context(
    db,
    agent,
    *,
    kb,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> tuple[list[dict], Callable[[dict], str] | None]:
    """按 AgentConfig 装配 kb_search 工具上下文（绑定当前知识库）。

    返回 (tools, executor)。检索配置走 agent overrides（resolve_agent_qa_overrides）
    → KB 策略 → 全局参数的既有优先级链；query embedding 失败时降级纯关键词检索，
    不阻塞工具调用。
    """
    from api.services.agents import config_from_dict, resolve_agent_qa_overrides

    cfg = config_from_dict(agent.config or {})
    top_k = max(1, min(int(cfg.embedding_top_k or 10), MAX_KB_SEARCH_TOP_K))
    tools = build_kbsearch_tool(top_k)

    def executor(tool_call: dict) -> str:
        name = str(tool_call.get("name") or "")
        if name != KB_SEARCH_TOOL:
            return f"未授权的工具调用: {name!r}"
        try:
            args = json.loads(str(tool_call.get("arguments") or "{}"))
        except (TypeError, json.JSONDecodeError):
            return "工具参数解析失败：arguments 不是合法 JSON"
        query = str(args.get("query") or "").strip()
        if not query:
            return "kb_search 缺少 query 参数"
        try:
            from api.services.retrieval import config_from_kb, hybrid_search

            overrides = resolve_agent_qa_overrides(agent)
            cfg_retrieval = config_from_kb(kb, overrides)
            query_embedding = None
            if getattr(cfg_retrieval, "vector_enabled", True):
                try:
                    from api.services.embedding import get_embedding_client

                    query_embedding = get_embedding_client(
                        db,
                        user_id=caller_user_id,
                        team_name=caller_team_name,
                        is_sys_admin=is_sys_admin,
                    ).embed_query(query)
                except Exception:  # noqa: BLE001 — embedding 失败降级关键词检索
                    query_embedding = None
            hits = hybrid_search(db, kb.id, query, query_embedding, cfg_retrieval)
        except Exception as exc:  # noqa: BLE001 — 工具失败回传文本，不炸 SSE
            return f"知识库检索失败: {exc}"
        return _hits_to_text(hits)

    return tools, executor
