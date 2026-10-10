"""重新解析（reparse）入队幂等性回归测试。

回归背景：`_enqueue_document_process` 用固定 job_id=DOC_{document_id} 直接
insert modo_job，首次上传已有该行 → 重新解析时撞主键 IntegrityError(1062)，
入队失败且 Celery send_task 未执行。修复为「先查后改」的 upsert。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Job


@pytest.fixture()
def job_db(monkeypatch):
    """内存库 + 拦截 celery send_task，返回 (session_factory, sent_calls)。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    sent: list[tuple[str, object]] = []

    import worker.celery_app as celery_mod

    def fake_send_task(name, args=None, task_id=None, queue=None, **kwargs):
        sent.append((name, (args or [None])[0]))

    monkeypatch.setattr(celery_mod.celery_app, "send_task", fake_send_task, raising=False)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session, raising=False)

    yield Session, sent
    engine.dispose()


def test_enqueue_creates_job_first_time(job_db) -> None:
    from api.routers.kbs import _enqueue_document_process

    Session, sent = job_db
    doc_id = "doc-new"

    _enqueue_document_process("kb1", doc_id)

    db = Session()
    row = db.get(Job, f"DOC_{doc_id}")
    assert row is not None
    assert row.state == "PENDING"
    assert row.task_id == doc_id
    assert row.trigger_type == "API"
    assert len(sent) == 1 and sent[0][1] == f"DOC_{doc_id}"


def test_enqueue_is_idempotent_on_reparse(job_db) -> None:
    """核心回归：同一 document_id 重复入队不撞主键，重置为 PENDING 且只有一行。"""
    from api.routers.kbs import _enqueue_document_process

    Session, sent = job_db
    doc_id = "doc-dup"
    job_id = f"DOC_{doc_id}"

    # 首次上传入队
    _enqueue_document_process("kb1", doc_id)

    # 模拟 worker 已把该 job 跑完（SUCCESS + 有耗时/错误痕迹）
    db = Session()
    row = db.get(Job, job_id)
    row.state = "SUCCESS"
    row.duration_ms = 1234
    row.error_message = "stale error"
    db.commit()
    db.close()

    # 重新解析再次入队 —— 修复前这里抛 IntegrityError(1062)
    _enqueue_document_process("kb1", doc_id)

    db = Session()
    rows = db.execute(select(Job).where(Job.id == job_id)).scalars().all()
    assert len(rows) == 1  # 没有产生重复行
    row = rows[0]
    assert row.state == "PENDING"
    assert row.duration_ms is None  # 上一轮痕迹已清
    assert row.error_message is None
    assert len(sent) == 2  # 两次都成功投递 Celery