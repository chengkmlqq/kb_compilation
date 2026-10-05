"""log_sink 单元测试：MinIO 日志追加 + error_message 槽位写入（best-effort）。

覆盖：
- append_job_log 写入 error_message 槽位（内存 DB）
- MinIO 分支：remote_enabled=True 时追加写 logs/<job_id>.log（读-改-写）
- 异常不抛出（远程写失败不影响任务）
"""
from __future__ import annotations

import types

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.models.framework import Base, Job


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add(Job(id="job-1", task_class="KbWikiBuildTask", state="RUNNING", queue_name="default"))
    db.commit()
    yield db
    db.close()


@pytest.fixture()
def fake_storage(monkeypatch):
    """假 storage：remote_enabled=True + 内存对象（读-改-写追加）。"""
    objs: dict[str, bytes] = {}

    class FakeStorage:
        def remote_enabled(self):
            return True

        def get_settings(self):
            return types.SimpleNamespace(MINIO_BUCKET="kb-compilation")

        @staticmethod
        def _key(path):
            # minio://bucket/rest → rest
            if path.startswith("minio://"):
                return path.split("//", 1)[1].split("/", 1)[1]
            return path

        def get_bytes(self, path):
            key = self._key(path)
            if key not in objs:
                raise FileNotFoundError(key)
            return objs[key]

        def stat_size(self, path):
            key = self._key(path)
            return len(objs.get(key, b""))

        def put_bytes(self, path, data, content_type=None):
            key = self._key(path)
            objs[key] = data
            return path

    fake = FakeStorage()
    import api.services.storage as st_mod
    monkeypatch.setattr(st_mod, "remote_enabled", fake.remote_enabled)
    monkeypatch.setattr(st_mod, "get_settings", fake.get_settings)
    monkeypatch.setattr(st_mod, "get_bytes", fake.get_bytes)
    monkeypatch.setattr(st_mod, "stat_size", fake.stat_size)
    monkeypatch.setattr(st_mod, "put_bytes", fake.put_bytes)
    return objs


def test_append_writes_error_slot(db_session, monkeypatch):
    """error_message 槽位被更新（任务监控页兼容）。"""
    import worker.tasks.log_sink as sink

    from api.db import get_sessionmaker
    import api.db as api_db
    from sqlalchemy.orm import sessionmaker

    # 绑定 fixture 的 engine，新建 sessionmaker 工厂
    bound = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(api_db, "get_sessionmaker", lambda: bound)
    # 远程写用假的 storage（避免依赖真 MinIO）
    monkeypatch.setattr(sink, "_append_remote", lambda *a, **k: None)
    sink.append_job_log("job-1", "[step 1/4] loaded chunks")
    db_session.expire_all()
    job = db_session.execute(select(Job).where(Job.id == "job-1")).scalars().first()
    assert job.error_message == "[step 1/4] loaded chunks"


def test_append_writes_minio_log(fake_storage, db_session, monkeypatch):
    """MinIO 日志对象 logs/<job_id>.log 追加写入（两次调用 → 两行）。"""
    import worker.tasks.log_sink as sink

    monkeypatch.setattr(sink, "_write_error_slot", lambda *a, **k: None)
    sink.append_job_log("job-1", "line1")
    sink.append_job_log("job-1", "line2")
    assert fake_storage["logs/job-1.log"] == b"line1\nline2\n"


def test_append_never_raises(fake_storage, db_session, monkeypatch):
    """远程写抛异常也不使任务失败（best-effort，走真实内部 try/except）。"""
    import worker.tasks.log_sink as sink

    # fake_storage.put_bytes 抛错（真实路径），log_sink 内部 try/except 应吞掉
    def boom_put(path, data, content_type=None):
        raise RuntimeError("minio down")

    class FailingStorage(fake_storage.__class__):
        def put_bytes(self, path, data, content_type=None):
            raise RuntimeError("minio down")

    monkeypatch.setattr(
        "api.services.storage.put_bytes",
        FailingStorage().put_bytes,
    )
    sink.append_job_log("job-1", "whatever")  # 不抛


def test_log_key_shape():
    from worker.tasks.log_sink import _log_key

    assert _log_key("job-abc") == "logs/job-abc.log"
