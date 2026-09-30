"""System config cache — in-process snapshot of modo_dim (SYSTEM_CONFIG group).

Ported from data-synth src/lib/config-cache.ts:
- TTL expiry (60s) so reads hit DB at most once per TTL per process
- single-flight: concurrent misses share one DB query (cache stampede guard)
- invalidate() after writes so the next read sees fresh values
- DB failure is NOT cached — next call retries
- Multi-instance caveat: invalidation is per-process (same as TS original)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models.framework import Dim

logger = logging.getLogger(__name__)

CACHE_TTL_MS = 60_000

_cache: dict | None = None
_inflight: Optional[dict] = None
_lock = threading.Lock()


def invalidate_system_config_cache() -> None:
    """Force immediate expiry (call after config writes)."""
    global _cache
    with _lock:
        _cache = None


def get_system_config_rows(db: Session) -> dict[str, str]:
    """Return SYSTEM_CONFIG (state=1) dimCode -> dimValue snapshot.

    Mirrors the TS single-flight: only one DB query even with concurrent
    callers; callers pass a session so Celery workers can use their own.
    """
    global _cache, _inflight
    now_ms = int(time.time() * 1000)
    if _cache and _cache["expires_at"] > now_ms:
        return _cache["map"]

    # single-flight: reuse an in-flight load instead of querying again
    if _inflight is not None:
        return _inflight

    with _lock:
        if _cache and _cache["expires_at"] > now_ms:
            return _cache["map"]
        if _inflight is not None:
            return _inflight
        _inflight = _load_system_config(db)

    result = _inflight
    _inflight = None
    return result


def _load_system_config(db: Session) -> dict[str, str]:
    config_map: dict[str, str] = {}
    try:
        rows = (
            db.execute(
                select(Dim.dim_code, Dim.dim_value).where(
                    Dim.dim_group == "SYSTEM_CONFIG",
                    Dim.state == "1",
                    Dim.dim_value.isnot(None),
                )
            )
            .all()
        )
        for dim_code, dim_value in rows:
            code = str(dim_code or "").strip()
            value = str(dim_value or "").strip()
            if code and value:
                config_map[code] = value
        global _cache
        _cache = {"map": config_map, "expires_at": int(time.time() * 1000) + CACHE_TTL_MS}
    except Exception:
        logger.exception("[ConfigCache] failed to load system config, fallback to empty map")
        _cache = None
    return config_map
