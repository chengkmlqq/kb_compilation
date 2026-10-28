"""进程内短 TTL 聚合缓存（2026-10-23 性能审计）。

背景：知识库/总览页的计数与聚合 SQL 会全扫含 MEDIUMTEXT content 的大表
（doc_chunk/wiki_page），远端 MySQL 缓冲池仅 128MB、被多平台共享，冷索引页
读取秒级、热时 100-300ms——执行计划已最优（Using index 全覆盖），波动纯粹是
缓冲池冷/热效应。看板/卡片类统计可容忍短时陈旧，故在服务层缓存聚合结果：

- TTL 默认 30s（list_kbs 计数用 15s，上传/删除后可较快刷新）；
- key 由调用方构造（聚合结果按 kb 集合/可见范围区分），整值缓存；
- `agg_cached`：TTL 内命中即返；过期后同步重算（首个请求会支付 DB 耗时）。
- `agg_cached_swr`：stale-while-revalidate——过期后**先返回旧值**（最快 300s
  陈旧上限），同时单飞后台线程用独立 session 重算；页面请求恒定毫秒级，
  统计短暂陈旧（≤TTL+刷新耗时）对看板可接受。fn 必须自行创建/管理 DB
  session（不得捕获请求 session，后台线程无请求上下文）。
- 进程内 dict + threading.Lock，单 uvicorn 进程部署下即为全局生效；
  多副本部署时各副本独立缓存（TTL 窗口内可能短暂不一致，可接受）。

只缓存「统计/看板」类数据。列表页（jobs/quality 明细等）不走本缓存。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_refreshing: dict[str, bool] = {}
_refresh_lock = threading.Lock()

# 聚合缓存持久化文件：docker restart/进程重启后从文件恢复旧值，重启后首访
# 走 stale 快返回（配合启动预热线程，彻底消除冷启动同步计算窗口）。
_PERSIST_PATH = os.environ.get("AGG_CACHE_FILE", "/srv/kb/data/agg_cache.json")


def _persist() -> None:
    """把内存缓存写入本地文件（聚合值均为 JSON 可序列化标量）。"""
    try:
        import os as _os

        _os.makedirs(_os.path.dirname(_PERSIST_PATH), exist_ok=True)
        tmp = _PERSIST_PATH + ".tmp"
        with _lock:
            snapshot = {k: v for k, v in _cache.items()}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        _os.replace(tmp, _PERSIST_PATH)
    except Exception:
        pass  # 持久化失败不影响缓存功能


def _load_persisted() -> None:
    """启动时从文件恢复缓存项（**含已过期项**）。

    恢复过期项是刻意的：agg_cached_swr 的 stale 分支需要"旧值存在"才能
    快返回 + 后台刷新；只恢复未过期项会导致重启后高频键（如 kbs_counts
    ttl=15s）在内存中缺失 → 首访退回同步计算（冷缓冲秒级）。过期项恢复
    后由 agg_cached_swr 判断（staleness_ok 默认 1h 内返回旧值并后台刷新）。

    expires_at 是 time.monotonic()（Linux CLOCK_MONOTONIC，系统级时钟）——
    同主机跨进程/重启一致。容器重建（CI 部署）丢文件时由启动预热线程兜底。
    """
    try:
        with open(_PERSIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            for k, (exp, v) in data.items():
                try:
                    _cache[k] = (float(exp), v)
                except (TypeError, ValueError):
                    continue
    except Exception:
        return


_load_persisted()


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
    _persist()
    return value


def agg_cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """缓存模板：命中直接返回；未命中执行 fn 并写缓存（fn 可捕获请求 session）。"""
    hit = agg_cache_get(key)
    if hit is not None:
        return hit
    return agg_cache_set(key, fn(), ttl)


def agg_cached_swr(
    key: str,
    ttl: float,
    fn: Callable[[Any], Any],
    db: Any = None,
    staleness_ok: float = 3600.0,
) -> Any:
    """stale-while-revalidate 缓存：TTL 过期后先返回旧值并后台刷新。

    fn 签名为 fn(db)：同步路径传入当前请求 db（测试 monkeypatch 兼容）；
    后台刷新线程无请求上下文，自建 session 传入。
    - TTL 内命中：直接返回（毫秒级）。
    - 过期但旧值存在且未超 staleness_ok（默认 1h）：返回旧值 + 单飞后台
      线程重算（刷新失败保留旧值，下个请求再试）——页面请求恒定毫秒级，
      统计陈旧上限 = ttl + 刷新耗时（后台刷新完成后立即恢复新鲜）。
    - 无旧值（重启后首访）或陈旧超上限：同步计算（fn(db)）并写缓存——
      该路径由 api/main.py 启动预热兜底（重启后聚合缓存自动预填）。
    """
    with _lock:
        item = _cache.get(key)
    now = time.monotonic()
    if item is not None:
        expires_at, value = item
        if now <= expires_at:
            return value
        if now - (expires_at - ttl) <= staleness_ok:
            _spawn_refresh(key, ttl, fn)
            return value
    return agg_cache_set(key, fn(db), ttl)


def _spawn_refresh(key: str, ttl: float, fn: Callable[[Any], Any]) -> None:
    """单飞后台刷新：同一 key 同时至多一个刷新线程在跑（自建 session）。"""
    with _refresh_lock:
        if _refreshing.get(key):
            return
        _refreshing[key] = True

    def _run() -> None:
        try:
            from api.db import get_sessionmaker

            s = get_sessionmaker()()
            try:
                value = fn(s)
            finally:
                s.close()
            agg_cache_set(key, value, ttl)
        except Exception:
            pass  # 刷新失败保留旧值，下个请求再触发
        finally:
            with _refresh_lock:
                _refreshing.pop(key, None)

    threading.Thread(target=_run, daemon=True, name=f"agg-refresh-{key[:24]}").start()


def agg_cache_clear() -> None:
    """清空全部缓存（测试用 / 手动刷新）。"""
    with _lock:
        _cache.clear()
    _persist()


def agg_cache_invalidate(key: str) -> None:
    """删除单个缓存项（写操作后即时失效，避免统计卡陈旧到 TTL 才刷新）。

    任务监控页 stop/delete 改变任务状态后调用，使下次 statistics 请求
    走同步重算（agg_cached_swr TTL 内本应返旧值）——配合前端常驻轮询，
    手动操作即时反映到顶部统计卡。仅清指定 key，不影响其他聚合缓存。
    """
    with _lock:
        _cache.pop(key, None)
    _persist()