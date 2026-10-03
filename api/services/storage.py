"""存储抽象层 —— 本地磁盘 / MinIO 双后端（2026-10-03 引入）。

kb_compilation 早期把上传文件直接写容器本地磁盘（kb_document.storage_path
存绝对路径、modo_sys_file.storage_path 存相对路径）。本模块把两种后端统一到
一个极简接口上，调用方只处理「路径」字符串：

- ``minio://<bucket>/<key>``  → MinIO 对象存储
- 其他（绝对路径 / 相对路径） → 本地磁盘（KB_STORAGE_DIR 之下）

这样既有历史数据零迁移（本地路径继续可读），又能随时切换到 MinIO。

对外 API：
    put_bytes(storage_path, data)          写对象
    get_bytes(storage_path) -> bytes       读对象
    exists(storage_path) -> bool           是否存在
    delete(storage_path)                   删除对象（幂等）
    open_stream(storage_path)              流式读（大文件下载/zip 打包）
    is_remote(storage_path) -> bool        是否 MinIO 路径
"""

from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path
from typing import BinaryIO

from api.config import get_settings

logger = logging.getLogger(__name__)

MINIO_SCHEME = "minio://"

_client_lock = threading.Lock()
_client = None
_bucket_ready: set[str] = set()


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def is_remote(storage_path: str | None) -> bool:
    """是否 MinIO 路径。"""
    return bool(storage_path) and storage_path.startswith(MINIO_SCHEME)


def parse_remote(storage_path: str) -> tuple[str, str]:
    """``minio://bucket/key`` → (bucket, key)。"""
    body = storage_path[len(MINIO_SCHEME):]
    bucket, _, key = body.partition("/")
    if not bucket or not key:
        raise ValueError(f"非法的 minio storage_path: {storage_path}")
    return bucket, key


def build_remote(bucket: str, key: str) -> str:
    """(bucket, key) → ``minio://bucket/key``。"""
    return f"{MINIO_SCHEME}{bucket}/{key.lstrip('/')}"


def local_path(storage_path: str) -> Path:
    """本地绝对路径（相对路径挂到 KB_STORAGE_DIR 下）。"""
    if os.path.isabs(storage_path):
        return Path(storage_path)
    return Path(get_settings().kb_storage_dir) / storage_path


# ---------------------------------------------------------------------------
# MinIO 客户端
# ---------------------------------------------------------------------------
def _get_client():
    """惰性创建 MinIO 客户端（配置缺失/连接失败抛异常，调用方决定降级）。"""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        from minio import Minio

        s = get_settings()
        if not s.MINIO_ENDPOINT:
            raise RuntimeError("MINIO_ENDPOINT 未配置，无法使用 MinIO 存储")
        _client = Minio(
            s.MINIO_ENDPOINT,
            access_key=s.MINIO_ACCESS_KEY,
            secret_key=s.MINIO_SECRET_KEY,
            secure=bool(s.MINIO_SECURE),
        )
        return _client


def ensure_bucket(bucket: str) -> None:
    """确保 bucket 存在（幂等，带进程内缓存）。"""
    if bucket in _bucket_ready:
        return
    client = _get_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("created minio bucket: %s", bucket)
    _bucket_ready.add(bucket)


def minio_available() -> bool:
    """MinIO 是否可用（健康探测，供启动自检/测试用）。"""
    try:
        _get_client().list_buckets()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("minio unavailable: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 统一读写接口
# ---------------------------------------------------------------------------
def put_bytes(storage_path: str, data: bytes, content_type: str | None = None) -> str:
    """写对象，返回实际写入的 storage_path。"""
    if is_remote(storage_path):
        bucket, key = parse_remote(storage_path)
        ensure_bucket(bucket)
        _get_client().put_object(
            bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        return storage_path
    p = local_path(storage_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return str(p)


def get_bytes(storage_path: str) -> bytes:
    """读对象（不存在抛 FileNotFoundError）。"""
    if is_remote(storage_path):
        bucket, key = parse_remote(storage_path)
        resp = _get_client().get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    p = local_path(storage_path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return p.read_bytes()


def exists(storage_path: str) -> bool:
    """对象是否存在。"""
    if is_remote(storage_path):
        try:
            bucket, key = parse_remote(storage_path)
            _get_client().stat_object(bucket, key)
            return True
        except Exception:  # noqa: BLE001 — stat 失败即视为不存在
            return False
    return local_path(storage_path).exists()


def delete(storage_path: str) -> bool:
    """删除对象（幂等，返回是否真的删除了）。"""
    if is_remote(storage_path):
        try:
            bucket, key = parse_remote(storage_path)
            _get_client().remove_object(bucket, key)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("minio delete failed %s: %s", storage_path, exc)
            return False
    p = local_path(storage_path)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("local delete failed %s: %s", p, exc)
        return False


def open_stream(storage_path: str) -> BinaryIO:
    """流式读（大文件下载 / zip 打包用；调用方负责关闭）。"""
    if is_remote(storage_path):
        resp = _get_client().get_object(*parse_remote(storage_path))
        # 包一层带 close 的最小对象，确保 release_conn 被调用
        return _MinioStream(resp)
    return open(local_path(storage_path), "rb")


class _MinioStream(io.RawIOBase):
    """把 MinIO 的 HTTPResponse 包成可读流（支持 with / read / close）。"""

    def __init__(self, resp) -> None:
        self._resp = resp

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        try:
            return self._resp.read() if size is None or size < 0 else self._resp.read(size)
        except Exception:  # noqa: BLE001
            return b""

    def readinto(self, b) -> int:  # type: ignore[override]
        data = self.read(len(b))
        if not data:
            return 0
        b[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self._resp.close()
            self._resp.release_conn()
        except Exception:  # noqa: BLE001
            pass
        super().close()


def stat_size(storage_path: str) -> int | None:
    """对象字节数（不存在返回 None）。"""
    if is_remote(storage_path):
        try:
            bucket, key = parse_remote(storage_path)
            return int(_get_client().stat_object(bucket, key).size or 0)
        except Exception:  # noqa: BLE001
            return None
    p = local_path(storage_path)
    return p.stat().st_size if p.exists() else None


def remote_enabled() -> bool:
    """MinIO 是否已配置启用（配置了 endpoint 即启用）。"""
    return bool(get_settings().MINIO_ENDPOINT)


def resolve_new_path(rel_key: str, *, prefer_remote: bool | None = None) -> str:
    """为新上传生成 storage_path。

    rel_key 是相对 key（如 ``kb_documents/{kb_id}/{doc_id}.md``）：
    - MinIO 已配置（默认）→ ``minio://<bucket>/<rel_key>``
    - 未配置 → ``KB_STORAGE_DIR/<rel_key>`` 本地绝对路径
    """
    if prefer_remote is None:
        prefer_remote = remote_enabled()
    if prefer_remote:
        bucket = get_settings().MINIO_BUCKET or "kb-compilation"
        return build_remote(bucket, rel_key)
    return str(local_path(rel_key))