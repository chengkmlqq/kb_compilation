"""SDK trace 收集与落盘（agent 任务 span）。

agents SDK 默认不导出 trace（openai-tracing 需密钥）；此前 runtime 只拿到
trace_id 就丢弃（span 数据随内存消失）。本模块注册一个内存收集 processor：
- on_span_end 时把 span 序列化（Span.export()）按 trace_id 聚合
- 任务结束时由 agent_worker 取走（pop）落盘 logs/traces/{job_id}.json，
  并把摘要写进任务日志（任务监控可读）

进程级单例收集，进程内并发任务按 trace_id 隔离。
"""
from __future__ import annotations

import datetime
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_spans_by_trace: dict[str, list[dict]] = {}
_installed = False
_latest_trace_id: str = ""


class CollectingTraceProcessor:
    """收集 span 的内存处理器（不导出外部 trace 服务）。"""

    def on_trace_start(self, trace) -> None:  # noqa: D102 - trace 级生命周期不处理
        pass

    def on_trace_end(self, trace) -> None:  # noqa: D102
        pass

    def on_span_start(self, span) -> None:  # noqa: D102 - 开始不处理
        pass

    def on_span_end(self, span) -> None:  # type: ignore[override]
        global _latest_trace_id
        try:
            data = span.export()
        except Exception as exc:  # noqa: BLE001
            logger.debug("span export failed: %s", exc)
            return
        if not isinstance(data, dict):
            return
        tid = data.get("trace_id") or ""
        if not tid:
            return
        with _lock:
            _spans_by_trace.setdefault(tid, []).append(data)
        _latest_trace_id = tid

    def shutdown(self) -> None:  # noqa: D102
        pass

    def force_flush(self) -> None:  # noqa: D102
        pass


def install() -> None:
    """注册收集处理器（幂等，进程内一次）。"""
    global _installed
    if _installed:
        return
    try:
        from agents.tracing import set_trace_processors

        set_trace_processors([CollectingTraceProcessor()])
        _installed = True
        logger.info("agent trace processor installed (in-memory collect)")
    except Exception as exc:  # noqa: BLE001 - SDK 缺 tracer 不阻断任务
        logger.warning("trace processor install failed: %s", exc)


def pop_trace_spans(trace_id: str) -> list[dict]:
    """取出并清空某 trace 的全部 span（任务结束调用）。"""
    if not trace_id:
        return []
    with _lock:
        return _spans_by_trace.pop(trace_id, [])


def peek_trace_spans(trace_id: str) -> list[dict]:
    """查看某 trace 当前已收集的 span 快照（不清空）。"""
    if not trace_id:
        return []
    with _lock:
        return list(_spans_by_trace.get(trace_id, []))


def latest_trace_id() -> str:
    """最近一次收到 span 的 trace_id（运行中任务用）。"""
    return _latest_trace_id


def flush_to_file(trace_id: str, path: str) -> int:
    """把当前已收集的 span 原子写入 path（运行中增量快照）。

    返回写入的 span 数；trace 尚未产生任何 span 时返回 0。
    """
    import json
    import os

    spans = peek_trace_spans(trace_id)
    if not spans:
        return 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(spans, f, ensure_ascii=False)
    os.replace(tmp, path)
    return len(spans)


def summarize_spans(spans: list[dict]) -> dict:
    """生成可读摘要（写任务日志用）。span 为 SDK export 结构（span_data 内嵌）。"""
    total_ms = 0.0
    tools: list[str] = []
    llm_calls = 0
    for sp in spans:
        sd = sp.get("span_data") or {}
        stype = str(sd.get("type") or "")
        if stype == "function":
            fn = str(sd.get("name") or "")
            if fn and fn not in tools:
                tools.append(fn)
        elif stype == "generation":
            llm_calls += 1
        try:
            start = datetime.datetime.fromisoformat(str(sp.get("started_at")).replace("Z", "+00:00"))
            end = datetime.datetime.fromisoformat(str(sp.get("ended_at")).replace("Z", "+00:00"))
            total_ms += max(0, (end - start).total_seconds() * 1000)
        except Exception:  # noqa: BLE001
            pass
    return {
        "span_count": len(spans),
        "duration_ms": round(total_ms),
        "llm_calls": llm_calls,
        "tools": tools,
    }