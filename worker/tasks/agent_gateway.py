"""Agent-gateway Celery task — run hermes-agent skills via the gateway API.

The agent-gateway (separate deployment, default host port 8080) is an
asynchronous skill-execution service: ``POST /tasks`` enqueues an agent job
(input + optional agent_name/instructions/config) and returns a task id
immediately; ``GET /tasks/{id}`` reports its state. This module bridges that
service into the kb_compilation scheduler as a fourth task class, alongside
KbDocumentProcessTask / KbDocumentEmbedTask / KbWikiBuildTask /
KbGraphBuildTask:

- ``KbAgentGatewayTask`` — submit a gateway task and poll until a terminal
  status (``succeeded`` / ``failed`` / ``cancelled``) or a global timeout.

task_params (modo_cron_task.fire_params / modo_job.task_params, JSON):
    {
        "input": "把 进口药材管理办法 编译进 wiki",  # required — agent input
        "agentName": "...",        # optional, camelCase alias of agent_name
        "instructions": "...",    # optional system-prompt override
        "config": {...}            # optional, passed through to the agent
    }

Settings (env, see api/config.py):
    AGENT_GATEWAY_BASE_URL          default http://127.0.0.1:8080
    AGENT_GATEWAY_TIMEOUT_S        default 3600 (a full skill run takes 10-25
                                   min; mirrors the gateway's own
                                   SKILL_SCRIPT_TIMEOUT_S=3600)
    AGENT_GATEWAY_POLL_INTERVAL_S  default 15

On timeout the handler best-effort cancels the gateway task (POST
/tasks/{id}/cancel) so a slow agent loop does not outlive the Celery job.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from api.config import get_settings
from worker.tasks.scheduler import TASK_CLASS_REGISTRY

logger = logging.getLogger(__name__)

TASK_CLASS_AGENT_GATEWAY = "KbAgentGatewayTask"

# Terminal statuses in the gateway's agent_tasks table.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# Per-request HTTP timeout for a single gateway call (submit / poll / cancel).
_HTTP_REQUEST_TIMEOUT_S = 30.0


def _params(task_params: str | None) -> dict:
    try:
        return json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _first_str(params: dict, *keys: str) -> str | None:
    """Return the first non-empty string among keys (camelCase/snake_case)."""
    for key in keys:
        value = params.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def wait_for_task(
    client: httpx.Client,
    task_id: str,
    base_url: str,
    timeout_s: float,
    poll_interval_s: float,
    job_id: str | None = None,
) -> tuple[dict, bool]:
    """Poll GET /tasks/{id} until terminal status or deadline.

    Returns (last task record, timed_out). Transient HTTP errors during
    polling are logged and retried until the deadline instead of failing the
    whole job (the gateway may blip while the agent holds the task slot).

    When job_id is given, each poll writes a lightweight progress line into
    the modo_job.error_message slot (poll count, current gateway status,
    elapsed) so the task monitor page can show live progress for long
    agent-gateway runs (10-25 min typical).
    """
    base = base_url.rstrip("/")
    deadline = time.monotonic() + timeout_s
    last: dict = {"task_id": task_id, "status": "unknown"}
    polls = 0
    while True:
        polls += 1
        try:
            resp = client.get(f"{base}/tasks/{task_id}", timeout=_HTTP_REQUEST_TIMEOUT_S)
            resp.raise_for_status()
            record = resp.json() or {}
            if record.get("task_id"):
                last = record
            status = str(last.get("status") or "").lower()
            if status in TERMINAL_STATUSES:
                return last, False
        except httpx.HTTPError as e:
            logger.warning(
                "agent-gateway poll blip (task=%s, %s); retrying until deadline",
                task_id,
                e,
            )
        if job_id:
            try:
                from api.db import get_sessionmaker
                from api.models.framework import Job
                from sqlalchemy import select

                db = get_sessionmaker()()
                try:
                    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
                    if job and job.state not in ("SUCCESS", "FAILED"):
                        elapsed = int(time.monotonic() - (deadline - timeout_s))
                        status = str(last.get("status") or "unknown")
                        job.error_message = f"[poll {polls}] gateway status={status}, elapsed={elapsed}s"
                        db.commit()  # Session context manager does NOT commit — must persist explicitly
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                pass  # progress write is best-effort; never fail the poll
        if time.monotonic() >= deadline:
            return last, True
        time.sleep(poll_interval_s)


def _cancel_task(client: httpx.Client, base_url: str, task_id: str) -> None:
    base = base_url.rstrip("/")
    try:
        resp = client.post(f"{base}/tasks/{task_id}/cancel", timeout=_HTTP_REQUEST_TIMEOUT_S)
        logger.info("agent-gateway task %s cancel -> HTTP %s", task_id, resp.status_code)
    except httpx.HTTPError as e:
        logger.warning("agent-gateway task %s cancel failed (best-effort): %s", task_id, e)


def _handle_agent_gateway(job_id: str, task_params: str | None) -> dict:
    settings = get_settings()
    base_url = settings.AGENT_GATEWAY_BASE_URL
    timeout_s = float(settings.AGENT_GATEWAY_TIMEOUT_S)
    poll_interval_s = float(settings.AGENT_GATEWAY_POLL_INTERVAL_S)

    params = _params(task_params)
    user_input = _first_str(params, "input")
    if not user_input:
        return {"success": False, "job_id": job_id, "error": "task_params must include input"}

    payload: dict[str, Any] = {"input": user_input}
    agent_name = _first_str(params, "agentName", "agent_name")
    if agent_name:
        payload["agent_name"] = agent_name
    instructions = _first_str(params, "instructions")
    if instructions:
        payload["instructions"] = instructions
    config = params.get("config")
    if not isinstance(config, dict):
        config = {}

    # 1A/2A（2026-10-01）：技能与 MCP 配置数据在 kb_compilation（本地注册表），
    # 网关是无状态执行器——任务提交时把载荷随 config 下发：
    #   - skill_zip_base64: 技能 ZIP（base64），网关临时解压执行完即删
    #   - mcp_servers:      可见的启用 MCP 配置（解密密钥），网关任务级构建
    # 权限基准（2026-10-11 对齐）：wiki 构建任务的 config 带 kb_id，按**知识库
    # 创建者**的 owner_user_id/owner_team_name 解析——个人 + 团队 + (系统且拥有者
    # 是管理员)。此前一律"空上下文 + sys_admin"，个人/团队的技能与 MCP 永远不可见。
    # 无 kb_id 的通用 agent 任务沿用调用方传入的 userId/teamName。
    try:
        from api.db import get_sessionmaker
        from api.services.mcps import resolve_task_mcp_servers
        from api.services.skills import get_skill_package, list_skills

        from worker.tasks.agent_worker import _kb_owner_context

        with get_sessionmaker()() as db:
            kb_id = str(config.get("kb_id") or "")
            if kb_id:
                ctx = _kb_owner_context(db, kb_id)
            else:
                ctx = {
                    "user_id": str(params.get("userId") or ""),
                    "team_name": str(params.get("teamName") or ""),
                    "is_admin": False,
                }
            skill_name = _first_str(config, "skill")
            if skill_name:
                skill_item = next(
                    (
                        s
                        for s in list_skills(
                            db,
                            caller_user_id=ctx["user_id"],
                            caller_team_name=ctx["team_name"],
                            is_sys_admin=ctx["is_admin"],
                        )
                        if s["name"] == skill_name
                    ),
                    None,
                )
                if skill_item:
                    row = get_skill_package(db, skill_item["id"])
                    if row and row.package_zip:
                        import base64

                        config["skill_zip_base64"] = base64.b64encode(
                            bytes(row.package_zip)
                        ).decode("ascii")
                        config["skill"] = skill_item["name"]
            mcps = resolve_task_mcp_servers(
                db,
                caller_user_id=ctx["user_id"],
                caller_team_name=ctx["team_name"],
                is_sys_admin=ctx["is_admin"],
            )
            if mcps:
                config["mcp_servers"] = mcps
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to attach task-level skill/mcp payload: %s", e)

    payload["config"] = config

    result: dict[str, Any] = {"success": False, "job_id": job_id}
    with httpx.Client(timeout=_HTTP_REQUEST_TIMEOUT_S) as client:
        resp = client.post(
            f"{base_url.rstrip('/')}/tasks",
            json=payload,
            timeout=_HTTP_REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        gateway_task_id = str(resp.json().get("task_id") or "")
        if not gateway_task_id:
            return {**result, "error": "gateway submit returned no task_id"}
        result["gateway_task_id"] = gateway_task_id

        record, timed_out = wait_for_task(
            client, gateway_task_id, base_url, timeout_s, poll_interval_s,
            job_id=job_id,
        )
        if timed_out:
            _cancel_task(client, base_url, gateway_task_id)
            result.update(
                status=str(record.get("status") or "running"),
                error=f"gateway task {gateway_task_id} timed out after {timeout_s:.0f}s "
                f"(cancelled)",
            )
            return result

        status = str(record.get("status") or "unknown").lower()
        result["status"] = status
        output = record.get("output_text") or record.get("output")
        if output:
            result["output"] = output
        error_detail = record.get("error_detail")
        if status == "succeeded":
            result["success"] = True
        else:
            result["error"] = error_detail or f"gateway task ended with status={status}"
        if error_detail and status != "succeeded":
            result["error_detail"] = error_detail
        for key in ("runs_ms", "trace_id", "agent_name"):
            if record.get(key) is not None:
                result[key] = record[key]
        return result


def register_task_handlers() -> None:
    """Register the gateway handler into the scheduler registry."""
    TASK_CLASS_REGISTRY[TASK_CLASS_AGENT_GATEWAY] = _handle_agent_gateway


register_task_handlers()
