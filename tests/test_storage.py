"""storage 层测试：本地磁盘 / MinIO 双后端（路径解析 + 本地读写 + 远端桩）。"""
from __future__ import annotations

import os

import pytest

from api.services import storage as st


def test_is_remote_and_parse_build() -> None:
    assert st.is_remote("minio://kb/abc/x.md") is True
    assert st.is_remote("/data/kb_documents/x.md") is False
    assert st.is_remote(None) is False
    b, k = st.parse_remote("minio://kb-compilation/sys_files/a/b.pdf")
    assert b == "kb-compilation"
    assert k == "sys_files/a/b.pdf"
    assert st.build_remote("kb", "x/y") == "minio://kb/x/y"


def test_parse_remote_invalid() -> None:
    with pytest.raises(ValueError):
        st.parse_remote("minio://onlybucket")


def test_local_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KB_STORAGE_DIR", str(tmp_path))
    p = str(tmp_path / "docs" / "a.md")
    st.put_bytes(p, b"hello kb")
    assert st.exists(p) is True
    assert st.get_bytes(p) == b"hello kb"
    assert st.stat_size(p) == 8
    assert st.delete(p) is True
    assert st.exists(p) is False
    # 幂等删除
    assert st.delete(p) is False


def test_local_relative_path(tmp_path, monkeypatch) -> None:
    # get_settings() 是 lru_cache 的，直接改 env 无效 → patch storage 模块里的引用
    monkeypatch.setattr(
        st, "get_settings", lambda: type("S", (), {"kb_storage_dir": str(tmp_path)})()
    )
    st.put_bytes("rel/x.md", b"rel-data")
    assert (tmp_path / "rel" / "x.md").exists()
    assert st.get_bytes("rel/x.md") == b"rel-data"


def test_resolve_new_path_prefers_remote(tmp_path, monkeypatch) -> None:
    # 强制 remote（不看配置）
    p = st.resolve_new_path("kb_documents/kb1/d.md", prefer_remote=True)
    assert p.startswith("minio://")
    assert p.endswith("kb_documents/kb1/d.md")
    # 强制本地
    monkeypatch.setenv("KB_STORAGE_DIR", str(tmp_path))
    p2 = st.resolve_new_path("kb_documents/kb1/d.md", prefer_remote=False)
    assert not p2.startswith("minio://")
    assert p2.endswith(os.path.join("kb_documents", "kb1", "d.md"))


class _FakeObj:
    def __init__(self, size):
        self.size = size


class _FakeMinio:
    """极简 MinIO 桩：内存里存对象，覆盖 put/get/stat/remove/bucket。"""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.buckets: set[str] = set()

    def bucket_exists(self, b):
        return b in self.buckets

    def make_bucket(self, b):
        self.buckets.add(b)

    def put_object(self, bucket, key, stream, length=None, content_type=None):
        self.objects[(bucket, key)] = stream.read()

    def get_object(self, bucket, key):
        data = self.objects[(bucket, key)]
        outer = self

        class _Resp:
            def read(self, n=-1):
                return data if n is None or n < 0 else data[:n]

            def close(self):
                pass

            def release_conn(self):
                pass

        if (bucket, key) not in self.objects:
            raise FileNotFoundError(key)
        return _Resp()

    def stat_object(self, bucket, key):
        if (bucket, key) not in self.objects:
            raise FileNotFoundError(key)
        return _FakeObj(len(self.objects[(bucket, key)]))

    def remove_object(self, bucket, key):
        self.objects.pop((bucket, key), None)


def test_minio_roundtrip_with_fake_client(monkeypatch) -> None:
    fake = _FakeMinio()
    monkeypatch.setattr(st, "_get_client", lambda: fake)
    monkeypatch.setattr(st, "_bucket_ready", set())  # 强制走 ensure_bucket
    remote = "minio://kb-compilation/kb_documents/kb1/x.md"
    st.put_bytes(remote, b"remote-bytes")
    assert fake.bucket_exists("kb-compilation")
    assert st.exists(remote) is True
    assert st.get_bytes(remote) == b"remote-bytes"
    assert st.stat_size(remote) == 12
    st.put_bytes(remote, b"remote-bytes-2")
    assert st.get_bytes(remote) == b"remote-bytes-2"
    assert st.delete(remote) is True
    assert st.exists(remote) is False


def test_minio_missing_object_read_raises(monkeypatch) -> None:
    fake = _FakeMinio()
    monkeypatch.setattr(st, "_get_client", lambda: fake)
    monkeypatch.setattr(st, "_bucket_ready", set())
    remote = "minio://kb-compilation/nope.md"
    with pytest.raises(Exception):
        st.get_bytes(remote)


def test_remote_enabled_by_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_ENDPOINT", "")
    # get_settings 是 lru 缓存的，直接 patch 底层 os.getenv 不影响缓存 → 用 patch 目标函数
    import api.config as cfg

    orig = cfg.get_settings
    class S:
        MINIO_ENDPOINT = ""
    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    monkeypatch.setattr(st, "get_settings", lambda: S())
    assert st.remote_enabled() is False
    monkeypatch.setattr(st, "get_settings", lambda: type("S2", (), {"MINIO_ENDPOINT": "minio:9000"})())
    assert st.remote_enabled() is True
    monkeypatch.setattr(cfg, "get_settings", orig)