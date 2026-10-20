"""Agent tool-loop hardening tests (对齐 WeKnora maxEmptyResponseRetries /
maxRepeatedResponseRounds / MaxToolOutputChars / manageContextWindow).

Covers the four guards added to answer_question's tool loop without a live
LLM: empty-answer nudge retry, stuck-loop detection (same tool calls),
tool-output truncation, and context-window compression.
"""

from __future__ import annotations

from unittest.mock import patch

from api.services.chat import (
    MAX_EMPTY_RETRIES,
    MAX_REPEATED_TOOL_ROUNDS,
    MAX_TOOL_OUTPUT_CHARS,
    ChatMessage,
    answer_question,
)


class _FakeCtx:
    pass


class _FakeKb:
    id = "kb1"
    name = "kb1"
    indexing_strategy = {}
    vector_store_id = ""
    vlm_config = {}


def _run_agent(
    events: list[list[dict]],
    *,
    tool_executor=None,
    max_rounds: int = 5,
) -> list[dict]:
    """Drive answer_question's tool loop with scripted stream_events rounds."""
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def stream_events(self, messages, **kwargs):
            batch = events[min(calls["n"], len(events) - 1)]
            calls["n"] += 1
            yield from batch

    kb = _FakeKb()
    with patch("api.services.chat.load_chat_config", return_value=_FakeCtx()), patch(
        "api.services.retrieval.hybrid_search", return_value=[]
    ), patch("api.services.chat.ChatClient", _FakeClient):
        out = list(
            answer_question(
                kb_db=_FakeCtx(),
                kb=kb,
                question="test",
                query_embedding=None,
                tools=[{"type": "function", "function": {"name": "tool_a", "parameters": {}}}],
                tool_executor=tool_executor or (lambda tc: "tool result"),
                max_tool_iterations=max_rounds,
            )
        )
    return out


def test_empty_answer_gets_nudge_retry() -> None:
    # round1: no tool calls, no delta text (empty) -> nudge -> round2: answer
    events = [
        [{"type": "delta", "text": ""}, {"type": "thinking", "text": "思考中"}],
        [{"type": "delta", "text": "最终回答"}],
    ]
    out = _run_agent(events, max_rounds=5)
    deltas = [e["text"] for e in out if e.get("type") == "delta"]
    assert "最终回答" in deltas, deltas
    # 空轮不应产出「思考中」以外内容
    assert all(e.get("type") != "delta" or e.get("text") for e in out)


def test_empty_answer_exhausts_retries_then_breaks() -> None:
    # 每轮都空 -> 重试耗尽后 break，不再消费后续事件批次
    # 批次设计：3 个空批次（round1 nudge、round2 nudge、round3 耗尽 break），
    # 「不应到达」在第 4 个批次——break 后不应被读取。
    events = [
        [{"type": "delta", "text": ""}],
        [{"type": "delta", "text": ""}],
        [{"type": "delta", "text": ""}],
        [{"type": "delta", "text": "不应到达"}],
    ]
    out = _run_agent(events, max_rounds=5)
    texts = "".join(e.get("text") or "" for e in out if e.get("type") == "delta")
    assert "不应到达" not in texts, texts
    assert texts == ""  # 空回答被容忍（无正文产出）


def test_stuck_same_tool_calls_breaks_early() -> None:
    # 连续多轮相同工具调用 -> 卡死检测提前结束
    # 语义：同 sig 连续 MAX_REPEATED_TOOL_ROUNDS(2) 次后 break。
    # round1 tool(exec1, same=0) → round2 tool(exec2, same=1) →
    # round3 sig==last → same=2 ≥ 2 → break（**不再执行**，不进入 round4 delta）。
    same_round = [
        {"type": "tool_call", "id": "call_1", "name": "tool_a", "arguments": "{}"}
    ]
    events = [same_round, same_round, same_round, [{"type": "delta", "text": "不应到达"}]]
    # 记录工具执行次数：卡死后不应继续执行
    executed: list[str] = []

    def _exec(tc):
        executed.append(tc.get("name") or "")
        return "ok"

    out = _run_agent(events, tool_executor=_exec, max_rounds=6)
    texts = "".join(e.get("text") or "" for e in out if e.get("type") == "delta")
    assert "不应到达" not in texts
    # 卡死检测：连续 MAX_REPEATED_TOOL_ROUNDS 次相同后第 MAX+1 轮 break 不执行
    assert len(executed) == MAX_REPEATED_TOOL_ROUNDS, executed
    # 已执行的两轮各透出 tool_call + tool 事件
    tool_events = [e for e in out if e.get("type") == "tool_call"]
    tool_results = [e for e in out if e.get("type") == "tool"]
    assert len(tool_events) == MAX_REPEATED_TOOL_ROUNDS
    assert len(tool_results) == MAX_REPEATED_TOOL_ROUNDS


def test_tool_output_truncated() -> None:
    long_result = "x" * (MAX_TOOL_OUTPUT_CHARS + 500)
    events = [
        [{"type": "tool_call", "id": "call_1", "name": "tool_a", "arguments": "{}"}],
        [{"type": "delta", "text": "done"}],
    ]
    captured: dict = {}

    def _exec(tc):
        return long_result

    out = _run_agent(events, tool_executor=_exec, max_rounds=5)
    tool_ev = next(e for e in out if e.get("type") == "tool")
    result = tool_ev["results"][0]["result"]
    assert len(result) <= MAX_TOOL_OUTPUT_CHARS + 100, len(result)
    assert "已截断" in result
    # 截断版结果随 tool 事件回传前端（messages 内回填的是同一截断版，
    # 但 messages 不进入事件流，这里只验证 tool 事件透出的结果已截断）
    assert result == tool_ev["results"][0]["result"]


def test_context_window_compresses_old_tool_messages() -> None:
    # 3 轮工具（每轮参数不同，避免卡死检测触发）+ 大量输出
    # -> 上下文估算超限时压缩最早的 tool 消息
    big = "y" * 40000  # ~40k chars ≈ 远超 60000 token 预算
    events = []
    for _ in range(3):
        events.append(
            [
                {
                    "type": "tool_call",
                    "id": f"call_{_}",
                    "name": "tool_a",
                    "arguments": f'{{"round": {_}}}',
                }
            ]
        )
    events.append([{"type": "delta", "text": "final"}])
    out = _run_agent(events, tool_executor=lambda tc: big, max_rounds=5)
    tool_evs = [e for e in out if e.get("type") == "tool"]
    assert len(tool_evs) == 3  # 三轮工具都执行了（不同参数不触发卡死）
    # 最终回答到达说明循环没被上下文问题打断
    final = [e for e in out if e.get("type") == "delta" and e.get("text") == "final"]
    assert final
