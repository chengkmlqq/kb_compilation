#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排中间产物 MinIO 存取 shim（2026-10-16 新增）。

背景：编排 14 个 script 节点的中间产物（chunks.json / fulltext.txt /
ls_all.json / terms.json / terms_filtered.json / esm.json / catids.json）
此前写在容器本地 /tmp/reg_<kid8>/——容器重建即全部丢失，曾导致 1296 个
在途 run 批量 failed（global OOM 后重建 worker 触发）。

改造：产物改存 MinIO（bucket=kb-compilation，前缀 reg-cache/reg_<kid8>/），
容器重建/宿主重启不再丢；由 KbRegCacheCleanupTask 定时清理（保留 7 天）。

用法（编排脚本里）::

    import reg_cache as rc
    DIR = bindings.get('reg_dir', f"reg-cache/reg_{_kid[:8]}")
    # 写
    rc.write_json(DIR, 'chunks.json', chunks)
    rc.write_text(DIR, 'fulltext.txt', full_text)
    # 读
    chunks = rc.read_json(DIR, 'chunks.json')
    full_text = rc.read_text(DIR, 'fulltext.txt')
    # 存在性/删除
    if rc.exists(DIR, 'terms.json'): ...
    rc.delete(DIR, 'old.json')

DIR 是 MinIO key 前缀（相对 key，不含 minio:// 和 bucket 名）；
bucket 固定用系统的 MINIO_BUCKET（kb-compilation），与文档对象隔离。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# bucket 用系统默认（compose MINIO_BUCKET=kb-compilation），
# 与文档对象同桶、目录前缀隔离。
try:
    from api.services.storage import (  # noqa: F401
        delete as _delete,
        exists as _exists,
        get_bytes as _get_bytes,
        put_bytes as _put_bytes,
    )
    _MINIO_OK = True
except Exception:  # pragma: no cover - 兜底：storage 不可用时走本地（调试）
    _MINIO_OK = False


def _bucket() -> str:
    """系统默认 bucket（compose MINIO_BUCKET=kb-compilation）。"""
    try:
        from api.config import get_settings
        return get_settings().MINIO_BUCKET or "kb-compilation"
    except Exception:  # pragma: no cover
        return "kb-compilation"


def _key(dir_prefix: str, name: str) -> str:
    """DIR 前缀 + 文件名 → MinIO 对象 key（minio://bucket/reg-cache/reg_xxx/name）。

    必须带 minio:// 前缀——api.services.storage 只对 minio:// 开头的
    storage_path 走 MinIO，裸路径会落到本地磁盘（2026-10-16 实测踩坑）。
    """
    return f"minio://{_bucket()}/{dir_prefix.rstrip('/')}/{name.lstrip('/')}"


def write_json(dir_prefix: str, name: str, obj: Any) -> None:
    """写 JSON 对象。"""
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    _put_bytes(_key(dir_prefix, name), data, content_type="application/json")


def read_json(dir_prefix: str, name: str) -> Any:
    """读 JSON 对象（不存在抛 FileNotFoundError）。"""
    raw = _get_bytes(_key(dir_prefix, name))
    return json.loads(raw.decode("utf-8"))


def write_text(dir_prefix: str, name: str, text: str) -> None:
    """写 UTF-8 文本。"""
    _put_bytes(_key(dir_prefix, name), text.encode("utf-8"), content_type="text/plain")


def read_text(dir_prefix: str, name: str) -> str:
    """读 UTF-8 文本（不存在抛 FileNotFoundError）。"""
    return _get_bytes(_key(dir_prefix, name)).decode("utf-8")


def exists(dir_prefix: str, name: str) -> bool:
    """对象是否存在。"""
    return _exists(_key(dir_prefix, name))


def delete(dir_prefix: str, name: str) -> bool:
    """删除对象（幂等）。"""
    return _delete(_key(dir_prefix, name))


def minio_ok() -> bool:
    """storage 封装是否可用。"""
    return _MINIO_OK
