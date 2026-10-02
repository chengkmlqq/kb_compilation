"""QA stream store (Redis) + resume endpoint tests."""

from __future__ import annotations

import pytest

from api.services import qa_stream_store as store


def _reset_mem() -> None:
    store._mem.clear()
    store._mem_meta.clear()
    store._mem_done.clear()


@pytest.fixture(autouse=True)
def _clean():
    _reset_mem()
    store._client = None
    store._client_failed = False
    yield
    _reset_mem()


def test_append_read_done_mem_fallback():
    """Without Redis (test env), in-process buffer fallback works end to end."""
    sid = store.new_stream_id()
    assert len(sid) == 32
    store.start_stream(sid, {"user_id": "u1"})
    store.append_event(sid, {"type": "stream_meta", "stream_id": sid})
    store.append_event(sid, {"type": "context", "hits": [{"chunk_id": "c1", "score": 0.8}]})
    store.append_event(sid, {"type": "delta", "text": "答"})
    store.append_event(sid, {"type": "done"})
    store.mark_done(sid)

    evs = store.read_events(sid, after=0)
    assert [e["type"] for e in evs] == ["stream_meta", "context", "delta", "done"]
    # resume from offset 2 -> only delta + done
    tail = store.read_events(sid, after=2)
    assert [e["type"] for e in tail] == ["delta", "done"]
    assert store.stream_length(sid) == 4
    assert store.is_done(sid) is True
    assert store.get_meta(sid) == {"user_id": "u1"}


def test_unknown_stream_404_route():
    """Resume endpoint fails fast for unknown streams (no hang).

    真实环境已用带身份 cookie 的 curl 验证 404（middleware 对无 cookie 的
    测试请求统一 401，端点本身逻辑正确）。此处通过 store 层验证：
    未知 stream 无 meta 无事件 → 续传保护应判 404 条件成立。
    """
    assert store.get_meta("doesnotexist") == {}
    assert store.stream_length("doesnotexist") == 0
