"""Redis-backed SSE event buffer for QA streams (WeKnora-style resume).

A QA request no longer OWNS the generation: the request spawns a background
thread that performs retrieval + LLM streaming and appends every SSE event to
a Redis list. The HTTP response merely tails that list, so navigating away
mid-answer does NOT cancel the generation — reconnecting with the stream id
replays the missed events from the caller's offset.

Keys (all TTL'd, best-effort cleanup):
  qa:stream:{stream_id}          list of serialized SSE event dicts (JSON)
  qa:stream:{stream_id}:meta     hash {user_id, session_id, kb_id, question}
  qa:stream:done:{stream_id}     marker string (TTL same as buffer)

Redis is optional: when REDIS_URL is unset or the client fails, callers fall
back to the in-process buffer so tests and single-node dev still work.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict
from typing import Any

from api.config import get_settings

STREAM_TTL_SECONDS = 3600
_client = None
_client_failed = False
_lock = threading.Lock()
# Fallback in-process buffers when Redis is unavailable (dev/tests only).
_mem: dict[str, list[dict]] = defaultdict(list)
_mem_meta: dict[str, dict] = {}
_mem_done: set[str] = set()


def _redis():
    global _client, _client_failed
    if _client is not None or _client_failed:
        return _client
    with _lock:
        if _client is not None or _client_failed:
            return _client
        url = get_settings().REDIS_URL
        if not url:
            _client_failed = True
            return None
        try:
            import redis  # imported lazily so tests need no redis

            _client = redis.Redis.from_url(url, decode_responses=True)
            _client.ping()
        except Exception:  # noqa: BLE001
            _client = None
            _client_failed = True
    return _client


def new_stream_id() -> str:
    return uuid.uuid4().hex


def start_stream(stream_id: str, meta: dict[str, Any]) -> None:
    r = _redis()
    if r is None:
        _mem_meta[stream_id] = {k: str(v) for k, v in meta.items()}
        return
    try:
        key = f"qa:stream:{stream_id}:meta"
        r.hset(key, mapping={k: str(v) for k, v in meta.items()})
        r.expire(key, STREAM_TTL_SECONDS)
        r.delete(f"qa:stream:{stream_id}")
    except Exception:  # noqa: BLE001
        _mem_meta[stream_id] = {k: str(v) for k, v in meta.items()}


def append_event(stream_id: str, event: dict) -> None:
    r = _redis()
    payload = json.dumps(event, ensure_ascii=False)
    if r is None:
        _mem[stream_id].append(event)
        return
    try:
        key = f"qa:stream:{stream_id}"
        r.rpush(key, payload)
        r.expire(key, STREAM_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        _mem[stream_id].append(event)


def mark_done(stream_id: str) -> None:
    r = _redis()
    if r is None:
        _mem_done.add(stream_id)
        return
    try:
        key = f"qa:stream:done:{stream_id}"
        r.set(key, str(time.time()), ex=STREAM_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        _mem_done.add(stream_id)


def is_done(stream_id: str) -> bool:
    r = _redis()
    if r is None:
        return stream_id in _mem_done
    try:
        return r.exists(f"qa:stream:done:{stream_id}") == 1
    except Exception:  # noqa: BLE001
        return stream_id in _mem_done


def read_events(stream_id: str, after: int = 0) -> list[dict]:
    """Events with index >= after (0-based index into the stream)."""
    r = _redis()
    if r is None:
        return _mem.get(stream_id, [])[after:]
    try:
        raw = r.lrange(f"qa:stream:{stream_id}", after, -1)
    except Exception:  # noqa: BLE001
        return _mem.get(stream_id, [])[after:]
    out: list[dict] = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except (TypeError, json.JSONDecodeError):
            continue
    return out


def stream_length(stream_id: str) -> int:
    r = _redis()
    if r is None:
        return len(_mem.get(stream_id, []))
    try:
        return int(r.llen(f"qa:stream:{stream_id}"))
    except Exception:  # noqa: BLE001
        return len(_mem.get(stream_id, []))


def get_meta(stream_id: str) -> dict:
    r = _redis()
    if r is None:
        return dict(_mem_meta.get(stream_id, {}))
    try:
        return dict(r.hgetall(f"qa:stream:{stream_id}:meta") or {})
    except Exception:  # noqa: BLE001
        return dict(_mem_meta.get(stream_id, {}))


__all__ = [
    "STREAM_TTL_SECONDS",
    "append_event",
    "get_meta",
    "is_done",
    "mark_done",
    "new_stream_id",
    "read_events",
    "start_stream",
    "stream_length",
]
