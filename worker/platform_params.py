"""worker 侧平台运行参数读取（页面可改，DB 优先）。

参数存在 `modo_dim`（dim_group=PLATFORM_CONFIG），由「系统 → 平台参数」页面
维护（api/services/system_config.py 的 get/save_platform_config）。worker 进程
按 TTL 缓存读取，避免每篇构建都查库；页面改动 ≤15s 生效。

优先级：DB 参数 > 容器 env > 代码默认值。
"""
from __future__ import annotations

import os
import time

_TTL_S = 15.0
_cache: dict[str, tuple[str, float]] = {}


def _load_from_db(codes: list[str]) -> dict[str, str]:
    """读 modo_dim 的 PLATFORM_CONFIG 组（任何异常都按未配置处理）。"""
    try:
        from sqlalchemy import select

        from api.db import get_sessionmaker
        from api.models.framework import Dim

        db = get_sessionmaker()()
        try:
            rows = (
                db.execute(
                    select(Dim.dim_code, Dim.dim_value).where(
                        Dim.dim_group == "PLATFORM_CONFIG",
                        Dim.dim_code.in_(codes),
                        Dim.state == "1",
                    )
                )
                .all()
            )
            return {code: (value or "") for code, value in rows}
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — DB 不可用时回落 env
        return {}


def get_platform_param(code: str, env_name: str = "", default: str = "") -> str:
    """取平台参数：DB（15s 缓存） > env > default。"""
    now = time.time()
    hit = _cache.get(code)
    if hit and now - hit[1] < _TTL_S:
        return hit[0]
    db_map = _load_from_db([code])
    value = (db_map.get(code) or "").strip()
    if not value and env_name:
        value = (os.environ.get(env_name) or "").strip()
    if not value:
        value = default
    if code == "WIKI_BUILD_MODE":
        # 历史命名归一化：inline/空 → direct
        value = "direct" if value.lower() in ("", "inline") else value.lower()
        if value not in ("direct", "agent", "gateway"):
            value = default or "direct"
    _cache[code] = (value, now)
    return value


def get_platform_int(code: str, env_name: str = "", default: int = 0) -> int:
    raw = get_platform_param(code, env_name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default