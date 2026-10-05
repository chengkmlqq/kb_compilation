"""Agent trace API 测试：get_job_trace 读 logs/traces/{job_id}.json。

覆盖：文件不存在返回 has_trace=false；存在返回 spans+summary。
"""
from __future__ import annotations

import json

import pytest

from api.routers.jobs import get_job_trace


class _FakeSettings:
    kb_storage_dir = ""


def test_trace_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("api.routers.jobs.get_settings", lambda: _FakeSettings())
    # 覆盖 kb_storage_dir 用 tmp_path：路由拼的是相对路径，直接模拟路径不存在
    import api.routers.jobs as jobs_mod

    class S:
        kb_storage_dir = str(tmp_path)

    monkeypatch.setattr(jobs_mod, "get_settings", lambda: S())
    res = get_job_trace("WIKI_xyz", _user_id="u")
    assert res["success"] is True
    assert res["data"]["has_trace"] is False
    assert res["data"]["spans"] == []


def test_trace_present(tmp_path, monkeypatch) -> None:
    import api.routers.jobs as jobs_mod

    class S:
        kb_storage_dir = str(tmp_path)

    monkeypatch.setattr(jobs_mod, "get_settings", lambda: S())
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "traces").mkdir()
    (tmp_path / "logs" / "traces" / "WIKI_abc.json").write_text(
        json.dumps(
            [
                {
                    "trace_id": "t1",
                    "span_id": "s1",
                    "name": "agent",
                    "started_at": "2026-10-05T00:00:00Z",
                    "ended_at": "2026-10-05T00:00:01Z",
                    "span_data": {"type": "agent", "name": "kb-agent-worker"},
                },
                {
                    "trace_id": "t1",
                    "span_id": "s2",
                    "started_at": "2026-10-05T00:00:01Z",
                    "ended_at": "2026-10-05T00:00:01.5Z",
                    "span_data": {"type": "function", "name": "run_skill_script"},
                },
            ]
        ),
        encoding="utf-8",
    )
    res = get_job_trace("WIKI_abc", _user_id="u")
    assert res["success"] is True
    d = res["data"]
    assert d["has_trace"] is True
    assert len(d["spans"]) == 2
    assert d["summary"]["span_count"] == 2
    assert "run_skill_script" in d["summary"]["tools"]
