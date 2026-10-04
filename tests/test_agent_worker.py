"""KbAgentWikiBuildTask handler 测试：payload 构造 + 技能 ZIP 加载 + LLM 兜底 + 调内联 agent。"""
from __future__ import annotations

import json
import sys
from unittest import mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Job
import worker.tasks.agent_worker as aw


@pytest.fixture()
def job_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)
    yield Session


def test_register_handler() -> None:
    assert "KbAgentWikiBuildTask" in aw.TASK_CLASS_REGISTRY


def test_handle_runs_agent_sync(job_env, monkeypatch) -> None:
    """核心路径：payload 正确构造 + run_agent_sync 被调用 + 结果回传。"""
    Session = job_env
    db = Session()
    db.add(Job(id="agent-job-1", task_id="agent-job-1", task_class="KbAgentWikiBuildTask",
               queue_name="agent", task_params="{}", trigger_type="API", state="RUNNING"))
    db.commit()
    db.close()

    captured: dict = {}

    def fake_run_agent_sync(payload, task_id=None):
        captured["payload"] = payload
        captured["task_id"] = task_id
        return {"success": True, "output": "wiki done", "runs_ms": 999, "sdk_trace_id": "tr-1"}

    monkeypatch.setattr(aw, "_attach_skill_zip", lambda cfg: cfg.setdefault("skill_zip", b"ZIP"))
    monkeypatch.setattr(aw, "_resolve_llm_config", lambda cfg: None)
    # agent_worker 在函数体内 `from worker.agent.runtime import run_agent_sync`，
    # 调用时从源头模块解析 → patch worker.agent.runtime.run_agent_sync
    monkeypatch.setattr("worker.agent.runtime.run_agent_sync", fake_run_agent_sync)

    params = json.dumps({"input": "build wiki", "config": {"skill": "kb-wiki-builder", "kb_id": "kb-1"}})
    result = aw._handle_agent_wiki_build("agent-job-1", params)

    assert result["success"] is True
    assert result["output"] == "wiki done"
    assert result["job_id"] == "agent-job-1"
    # payload 正确构造
    assert captured["payload"]["config"]["skill"] == "kb-wiki-builder"
    assert captured["payload"]["config"]["skill_zip"] == b"ZIP"
    assert captured["payload"]["config"]["job_id"] == "agent-job-1"
    assert captured["task_id"] == "agent-job-1"


def test_handle_missing_sdk_returns_clear_error(job_env, monkeypatch) -> None:
    """openai-agents SDK 未安装（解析 worker 场景）→ 清晰错误而非 ImportError 崩溃。"""
    Session = job_env

    def boom_import():
        raise ImportError("No module named 'agents'")

    monkeypatch.setattr(sys, "path", sys.path)  # no-op, keeps fixture path
    monkeypatch.setitem(sys.modules, "worker.agent.runtime", None)
    # 更直接：monkeypatch builtins import
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "worker.agent.runtime":
            raise ImportError("No module named 'agents'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(aw, "_attach_skill_zip", lambda cfg: None)
    monkeypatch.setattr(aw, "_resolve_llm_config", lambda cfg: None)

    result = aw._handle_agent_wiki_build("agent-job-1", json.dumps({"input": "x"}))
    assert result["success"] is False
    assert "agent 运行时不可用" in result["error"]


def test_handle_crash_returned_as_failure(job_env, monkeypatch) -> None:
    """agent 循环崩溃 → 返回 failure 而非抛异常（任务监控可见）。"""
    Session = job_env

    def boom(payload, task_id=None):
        raise RuntimeError("agent crashed")

    monkeypatch.setattr("worker.agent.runtime.run_agent_sync", boom)
    monkeypatch.setattr(aw, "_attach_skill_zip", lambda cfg: None)
    monkeypatch.setattr(aw, "_resolve_llm_config", lambda cfg: None)

    result = aw._handle_agent_wiki_build("agent-job-1", json.dumps({"input": "x"}))
    assert result["success"] is False
    assert "RuntimeError" in result["error"]