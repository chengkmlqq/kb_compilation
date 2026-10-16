#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排中间产物（MinIO reg-cache/）定期清理（KbRegCacheCleanupTask）。

背景：编排 14 个 script 节点的中间产物（chunks.json / fulltext.txt /
ls_all.json / terms.json / terms_filtered.json / esm.json / catids.json）
2026-10-16 起改存 MinIO（bucket=kb-compilation，前缀 reg-cache/reg_<kid8>/），
以避免容器重建丢失。产物只在 run 推进期间有用（run 终态后即成为垃圾），
本任务按保留时长定期清理过期目录。

fire_params（cron 任务参数 JSON）::

    {
      "retention_days": 7,        # 保留天数，默认 7（用户 2026-10-16 指定一周）
      "prefix": "reg-cache/",     # 清理前缀，默认 reg-cache/
      "dry_run": false,           # true 只统计不删除
      "max_delete": 5000          # 单次最多删对象数（防误删海量时的安全阀）
    }

清理粒度：按 reg_<kid8>/ 目录（对象名前两段）聚合，目录内所有对象
的 last_modified 都早于 cutoff 才删（避免删掉仍在推进的 run 的产物）。

建议 cron：每天 04:00（避开业务高峰与日志清理 03:00）。``0 4 * * *``。
"""
from __future__ import annotations

import datetime
import json
import logging

logger = logging.getLogger(__name__)

TASK_CLASS_REG_CACHE_CLEANUP = "KbRegCacheCleanupTask"

DEFAULT_RETENTION_DAYS = 7
DEFAULT_PREFIX = "reg-cache/"
DEFAULT_MAX_DELETE = 5000


def _pos_int(v: object, default: int) -> int:
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n


def cleanup_reg_cache(task_params: dict | None = None) -> dict:
    """按保留天数清理 MinIO 编排中间产物。返回删除统计。"""
    from minio import Minio

    from api.config import get_settings

    params = task_params or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            params = {}

    dry_run = bool(params.get("dry_run"))
    retention_days = _pos_int(params.get("retention_days"), DEFAULT_RETENTION_DAYS)
    prefix = str(params.get("prefix") or DEFAULT_PREFIX)
    max_delete = _pos_int(params.get("max_delete"), DEFAULT_MAX_DELETE)

    st = get_settings()
    if not st.MINIO_ENDPOINT:
        return {"success": False, "error": "MINIO_ENDPOINT 未配置，跳过清理"}

    client = Minio(
        st.MINIO_ENDPOINT,
        access_key=st.MINIO_ACCESS_KEY,
        secret_key=st.MINIO_SECRET_KEY,
        secure=st.MINIO_SECURE,
    )
    bucket = st.MINIO_BUCKET
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)

    # 按目录（reg-cache/reg_xxx/）聚合：目录内所有对象都过期才删
    dirs: dict[str, list] = {}
    total = 0
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        name = obj.object_name
        parts = name.split("/")
        dkey = "/".join(parts[:2]) if len(parts) >= 2 else name
        dirs.setdefault(dkey, []).append(obj)
        total += 1

    deletable: list[str] = []
    for dkey, objs in dirs.items():
        lm = [o.last_modified for o in objs if o.last_modified]
        if lm and max(lm) < cutoff:  # 目录内最新的对象也早于 cutoff
            deletable.extend(o.object_name for o in objs)

    stats: dict[str, object] = {
        "dry_run": dry_run,
        "retention_days": retention_days,
        "prefix": prefix,
        "bucket": bucket,
        "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_objects": total,
        "total_dirs": len(dirs),
        "deletable_objects": len(deletable),
    }
    if dry_run or not deletable:
        return stats

    deleted = 0
    errors = 0
    for name in deletable[:max_delete]:
        try:
            client.remove_object(bucket, name)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("reg-cache 删除失败 %s: %s", name, exc)
    stats["deleted_objects"] = deleted
    stats["delete_errors"] = errors
    if len(deletable) > max_delete:
        stats["note"] = f"单次上限 {max_delete}，剩余 {len(deletable) - max_delete} 待下次清理"
    logger.info("reg-cache cleanup done (dry_run=%s): %s", dry_run, stats)
    return stats


def _handle_reg_cache_cleanup(job_id: str, task_params: dict | None = None) -> dict:
    """execute_modo_job 分发的 handler：清理过期编排中间产物。"""
    result = cleanup_reg_cache(task_params)
    return {"success": True, "output": f"reg-cache cleanup: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_REG_CACHE_CLEANUP] = _handle_reg_cache_cleanup
