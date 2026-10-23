"""进程内短 TTL 聚合缓存（2026-10-23 性能审计）。

背景：知识库/总览页的计数与聚合 SQL 会全扫含 MEDIUMTEXT content 的大表
（doc_chunk/wiki_page），远端 MySQL 缓冲池仅 128MB、被多平台共享，冷索引页
读取秒级、热时 100-300ms——执行计划已最优（Using index 全覆盖），波动纯粹是
缓冲池冷/热效应。看板/卡片类统计可容忍短时陈旧，故在服务层缓存聚合结果：

- TTL 默认 30s（list_kbs 计数用 15s，上传/删除后可较快刷新）；
- key 由调用方构造（聚合结果按 kb 集合/可见范围区分），整值缓存；
- 进程内 dict + threading.Lock，单 uvicorn 进程部署下即为全局生效；
  多副本部署时各副本独立缓存（TTL 窗口内可能短暂不一致，可接受）。

只缓存「统计/看板」类数据。列表页（jobs/quality 明细等）不走本缓存。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def agg_cache_get(key: str) -> Any | None:
    """取缓存值；过期或缺失返回 None（不删除过期项，留待写入覆盖）。"""
    with _lock:
        item = _cache.get(key)
    if item is None:
        return None
    expires_at, value = item
    if time.monotonic() > expires_at:
        return None
    return value


def agg_cache_set(key: str, value: Any, ttl: float = 30.0) -> Any:
    """写入缓存并返回原值（便于 `return agg_cache_set(key, fn(...))` 一行接入）。"""
    with _lock:
        _cache[key] = (time.monotonic() + ttl, value)
    return value


def agg_cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """缓存模板：命中直接返回；未命中执行 fn 并写缓存。"""
    hit = agg_cache_get(key)
    if hit is not None:
        return hit
    return agg_cache_set(key, fn(), ttl)


def agg_cache_clear() -> None:
    """清空全部缓存（测试用 / 手动刷新）。"""
    with _lock:
        _cache.clear()