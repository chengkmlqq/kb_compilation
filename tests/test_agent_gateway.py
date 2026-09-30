"""Agent-gateway bridge tests — pure logic, gateway HTTP mocked (no network).

The bridge (worker/tasks/agent_gateway.py) submits ``POST /tasks`` and polls
``GET /tasks/{id}`` on the agent-gateway. The httpx client is replaced by a
scripted fake so terminal-status handling, parameter aliases and the
timeout -> cancel fallback are verified deterministically.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

import worker.tasks.agent_gateway as gw


class _Resp:
    def __init__(self, code: int, payload):
        self.status_code = code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Scripted stand-in for httpx.Client (context-manager + get/post)."""

    def __init__(self, submit_payload, poll_statuses, poll_bodies=None, cancel_code=202):
        self._submit = submit_payload
        self._statuses = list(poll_statuses)
        self._bodies = poll_bodies or [
            {"task_id": "t-1", "status": s} for s in self._statuses
        ]
        self._cancel_code = cancel_code
        self.cancel_calls: list[str] = []
        self.poll_count = 0

    # -- context manager -----------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    # -- http verbs ----------------------------------------------------------
    def post(self, url, json=None, timeout=None):
        if url.endswith("/cancel"):
            self.cancel_calls.append(url)
            return _Resp(self._cancel_code, {})
        return _Resp(202, self._submit)

    def get(self, url, timeout=None):
        self.poll_count += 1
        i = min(self.poll_count - 1, len(self._bodies) - 1)
        return _Resp(200, self._bodies[i])


def _settings(**over):
    base = {
        "AGENT_GATEWAY_BASE_URL": "http://gateway.test:8080",
        "AGENT_GATEWAY_TIMEOUT_S": 9999,
        "AGENT_GATEWAY_POLL_INTERVAL_S": 0,
    }
    base.update(over)
    return mock.Mock(**base)


def _run(monkeypatch, fake, params, settings=None, wait=None):
    """Drive ``_handle_agent_gateway`` with the gateway HTTP layer mocked."""
    monkeypatch.setattr(gw, "get_settings", lambda: settings or _settings())
    # gw.httpx is the global httpx module; patch its Client attribute so
    # agent_gateway uses our fake when it calls httpx.Client(...).
    monkeypatch.setattr(gw.httpx, "Client", lambda timeout=None: fake)
    monkeypatch.setattr(gw.httpx, "HTTPError", RuntimeError)
    if wait is not None:
        monkeypatch.setattr(gw, "wait_for_task", wait)
    return gw._handle_agent_gateway("job-1", json.dumps(params))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # poll_interval is 0 in the stub settings; guarantee no real sleep anyway.
    monkeypatch.setattr(gw.time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# parameter handling
# ---------------------------------------------------------------------------
def test_missing_input_rejected(monkeypatch):
    result = _run(monkeypatch, FakeClient({"task_id": "t"}, []), {"agentName": "a"})
    assert result["success"] is False
    assert "input" in result["error"]


def test_aliases_camel_and_snake(monkeypatch):
    fake = FakeClient({"task_id": "t-1"}, ["succeeded"])
    result = _run(
        monkeypatch, fake,
        {"input": "编译 wiki", "agent_name": "agent-x", "instructions": "system"},
    )
    # snake_case accepted alongside camelCase; handler maps to gateway keys.
    assert result["success"] is True
    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# terminal statuses
# ---------------------------------------------------------------------------
def test_succeeded_returns_success(monkeypatch):
    fake = FakeClient({"task_id": "t-1"}, ["running", "succeeded"])
    result = _run(monkeypatch, fake, {"input": "编译 wiki"})
    assert result["success"] is True
    assert result["gateway_task_id"] == "t-1"
    assert result["status"] == "succeeded"
    assert fake.cancel_calls == []


def test_failed_returns_error_detail(monkeypatch):
    bodies = [{"task_id": "t-1", "status": "failed", "error_detail": "boom"}]
    fake = FakeClient({"task_id": "t-1"}, ["failed"], poll_bodies=bodies)
    result = _run(monkeypatch, fake, {"input": "x"})
    assert result["success"] is False
    assert result["status"] == "failed"
    assert "boom" in result["error"]


def test_cancelled_is_terminal(monkeypatch):
    fake = FakeClient({"task_id": "t-1"}, ["running", "cancelled"])
    result = _run(monkeypatch, fake, {"input": "x"})
    assert result["success"] is False
    assert result["status"] == "cancelled"


# ---------------------------------------------------------------------------
# timeout -> best-effort cancel
# ---------------------------------------------------------------------------
def test_timeout_cancels_task(monkeypatch):
    # Real wait_for_task does NOT cancel — it just returns (record, True) when
    # the deadline hits. The handler is the one that issues the best-effort
    # cancel, so the fake must mirror that division of responsibility.
    def wait(client, task_id, base_url, timeout_s, poll_interval_s):
        return {"task_id": task_id, "status": "running"}, True

    fake = FakeClient({"task_id": "t-1"}, [])
    result = _run(monkeypatch, fake, {"input": "x"}, wait=wait)
    assert result["success"] is False
    assert "timed out" in result["error"]
    assert len(fake.cancel_calls) == 1


def test_wait_for_task_terminal(monkeypatch):
    fake = FakeClient({"task_id": "t-1"}, ["running", "succeeded"])
    record, timed_out = gw.wait_for_task(fake, "t-1", "http://gateway.test:8080", 60, 0)
    assert timed_out is False
    assert record["status"] == "succeeded"


def test_wait_for_task_times_out_on_nonterminal(monkeypatch):
    # Deadline already in the past -> first poll is running, loop exits.
    fake = FakeClient({"task_id": "t-2"}, ["running"])
    record, timed_out = gw.wait_for_task(fake, "t-2", "http://gateway.test:8080", -1, 0)
    assert timed_out is True
    assert record["status"] == "running"


# ---------------------------------------------------------------------------
# registry wiring
# ---------------------------------------------------------------------------
def test_registry_contains_gateway_task():
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY
    assert "KbAgentGatewayTask" in TASK_CLASS_REGISTRY


def test_params_parser():
    assert gw._params('{"a": 1}') == {"a": 1}
    assert gw._params(None) == {}
    assert gw._params("not-json") == {}
