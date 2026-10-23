"""Tests for run→ORCH job status sync (worker/tasks/orch_runner.py#_sync_orch_job_status).

2026-10-21 fix: _handle_run 对 execute_run 异步投递立即返回 True → scheduler
无条件标 ORCH_RUN_* job SUCCESS；run 真实终态只写 kb_tape_run，从不回写
modo_job → 任务监控「子任务失败但 job 显示成功」。本测试覆盖该回写逻辑：
- run 终态 failed → 关联 job 置 FAILED + error_message
- run 终态 success → 关联 job 置 SUCCESS
- 找不到 job 行（手动执行无承接）→ 跳过不抛
- STOPPED 保护：用户已停止的 job 不被覆盖
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.models.framework import Base, Job
from worker.tasks import orch_runner


@pytest.fixture()
def fw_db(monkeypatch):
    """框架库 in-memory sqlite + 把 api.db.get_sessionmaker 指向它。

    _sync_orch_job_status 函数体内 `from api.db import get_sessionmaker`
    是延迟导入——须 patch api.db 模块属性（patch orch_runner 上的是无效的）。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    def _sm():
        return Session

    import api.db

    monkeypatch.setattr(api.db, "get_sessionmaker", _sm)
    yield db
    db.close()


def _add_job(db, job_id: str, run_id: str | None, state: str = "SUCCESS") -> Job:
    params = json.dumps({"runId": run_id}) if run_id else "{}"
    job = Job(
        id=job_id,
        task_id=job_id,
        task_class="KbOrchestrationRunTask",
        queue_name="orch",
        task_params=params,
        trigger_type="API",
        state=state,
        run_id=run_id,
    )
    db.add(job)
    db.commit()
    return job


def test_sync_failed_run_marks_job_failed(fw_db) -> None:
    """run failed → job 置 FAILED + error_message 带步骤错误。"""
    _add_job(fw_db, "ORCH_RUN_fail1", "run-failed-id")
    orch_runner._sync_orch_job_status(
        "run-failed-id", "failed", "tape::n06: No such file: fulltext.txt"
    )
    fw_db.expire_all()
    job = fw_db.get(Job, "ORCH_RUN_fail1")
    assert job.state == "FAILED"
    assert "fulltext.txt" in (job.error_message or "")
    assert job.end_time is not None


def test_sync_success_run_marks_job_success(fw_db) -> None:
    """run success → job 置 SUCCESS。"""
    _add_job(fw_db, "ORCH_RUN_ok1", "run-ok-id", state="RUNNING")
    orch_runner._sync_orch_job_status("run-ok-id", "success", None)
    fw_db.expire_all()
    job = fw_db.get(Job, "ORCH_RUN_ok1")
    assert job.state == "SUCCESS"
    assert not job.error_message


def test_sync_missing_job_skips(fw_db) -> None:
    """无承接 job（手动执行）→ 不抛异常。"""
    orch_runner._sync_orch_job_status("orphan-run-id", "failed", "boom")
    # 无 job 可断言——正常返回即通过


def test_sync_keeps_stopped_state(fw_db) -> None:
    """用户手动 STOPPED 的 job 不被 run 终态覆盖。"""
    _add_job(fw_db, "ORCH_RUN_stop1", "run-stop-id", state="STOPPED")
    orch_runner._sync_orch_job_status("run-stop-id", "failed", "boom")
    fw_db.expire_all()
    assert fw_db.get(Job, "ORCH_RUN_stop1").state == "STOPPED"


def test_sync_fallback_to_task_params_like(fw_db) -> None:
    """旧数据 run_id 冗余列为空时按 task_params LIKE 兜底命中。"""
    job = Job(
        id="ORCH_RUN_legacy1",
        task_id="ORCH_RUN_legacy1",
        task_class="KbOrchestrationRunTask",
        queue_name="orch",
        task_params=json.dumps({"runId": "legacy-run-id"}),
        trigger_type="API",
        state="SUCCESS",
        run_id=None,  # 模拟冗余列回填前的旧行
    )
    fw_db.add(job)
    fw_db.commit()
    orch_runner._sync_orch_job_status("legacy-run-id", "failed", "boom")
    fw_db.expire_all()
    assert fw_db.get(Job, "ORCH_RUN_legacy1").state == "FAILED"