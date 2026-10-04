"""KbAgentWikiBuildTask —— worker 内联 agent 执行 wiki 构建（不再经外部 agent-gateway）。

与 `worker/tasks/agent_gateway.py`（KbAgentGatewayTask，HTTP 提交外部网关）并存：
  - 本任务（KbAgentWikiBuildTask）：agent 运行时内联在 worker 进程里（worker/agent/），
    跑 openai-agents SDK 的完整 agent 循环 + 技能执行，Celery/Flower 原生监控与扩容。
  - 外部网关任务（KbAgentGatewayTask）：保留作回退/并行路径，走 HTTP 提交给独立部署的
    agent-gateway 容器。投递目标由 doc_process._enqueue_wiki_skill_task 决定。

任务参数（task_params JSON）：
    {"input": "...", "agentName": "...", "instructions": "...",
     "config": {"skill": "kb-wiki-builder", "kb_id": "...", "doc_name": "...",
                "kb_base_url": "...", "model": "...", "base_url": "...", "api_key": "..."}}
"""
from __future__ import annotations

import json
import logging
from typing import Any

from api.db import get_sessionmaker
from worker.tasks.scheduler import TASK_CLASS_REGISTRY

logger = logging.getLogger(__name__)

TASK_CLASS_AGENT_WIKI_BUILD = "KbAgentWikiBuildTask"


def _params(task_params: str | None) -> dict[str, Any]:
    if not task_params:
        return {}
    try:
        return json.loads(task_params)
    except (TypeError, json.JSONDecodeError):
        return {}


def _first_str(d: dict, *keys: str) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _write_progress(job_id: str, message: str) -> None:
    """把 agent 运行进度写入 modo_job.error_message（任务监控页实时可见）。"""
    try:
        from api.models.framework import Job
        from sqlalchemy import select

        db = get_sessionmaker()()
        try:
            job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
            if job and job.state in ("PENDING", "RUNNING"):
                job.error_message = message[:2000]
                db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 进度写入是 best-effort，绝不影响任务
        logger.debug("progress write failed job=%s", job_id, exc_info=True)


def _attach_skill_zip(config: dict[str, Any]) -> None:
    """1A：从 kb_skill 表取技能 ZIP（bytes）挂到 config.skill_zip（内联路径无需 base64）。"""
    skill_name = _first_str(config, "skill")
    if not skill_name:
        return
    try:
        from api.services.skills import get_skill_package, list_skills

        db = get_sessionmaker()()
        try:
            item = next(
                (
                    s
                    for s in list_skills(db, caller_user_id="", caller_team_name="", is_sys_admin=True)
                    if s["name"] == skill_name
                ),
                None,
            )
            if item:
                row = get_skill_package(db, item["id"])
                if row and row.package_zip:
                    config["skill_zip"] = bytes(row.package_zip)
                    config["skill"] = item["name"]
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to load skill zip for %s: %s", skill_name, exc)


def _resolve_llm_config(config: dict[str, Any]) -> None:
    """LLM 配置兜底：从 kb_model 注册表解析 chat 模型（对齐今天的教训——
    不再依赖手工 env WIKI_LLM_API_KEY）。任务已带 model/base_url/api_key 时不覆盖。"""
    if config.get("model") and config.get("base_url") and config.get("api_key"):
        return
    try:
        from api.services.models import resolve_model_config

        db = get_sessionmaker()()
        try:
            resolved = resolve_model_config(db, "chat", is_sys_admin=True)
        finally:
            db.close()
        if resolved:
            config.setdefault("base_url", resolved.get("base_url") or "")
            config.setdefault("model", resolved.get("model") or "")
            config.setdefault("api_key", resolved.get("api_key") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to resolve chat model for agent: %s", exc)


def _handle_agent_wiki_build(job_id: str, task_params: str | None) -> dict[str, Any]:
    """worker 内联执行 agent（openai-agents SDK），完成 wiki 构建。"""
    params = _params(task_params)
    config: dict[str, Any] = dict(params.get("config") or {})
    config["job_id"] = job_id

    _attach_skill_zip(config)
    _resolve_llm_config(config)

    payload = {
        "input": str(params.get("input") or ""),
        "config": config,
    }
    agent_name = _first_str(params, "agentName", "agent_name")
    if agent_name:
        payload["agent_name"] = agent_name
    instructions = _first_str(params, "instructions")
    if instructions:
        payload["instructions"] = instructions

    _write_progress(job_id, f"[agent] 启动内联 agent，skill={config.get('skill') or '-'}")

    # 延迟 import：openai-agents SDK 只装在 agent worker 镜像里，解析 worker
    # 若误跑这个任务类也能给出清晰错误而不是 ImportError 崩溃。
    try:
        from worker.agent.runtime import run_agent_sync
    except ImportError as exc:
        msg = f"agent 运行时不可用（openai-agents SDK 未安装？）: {exc}"
        logger.error(msg)
        return {"success": False, "error": msg, "job_id": job_id}

    _write_progress(job_id, "[agent] agent 循环运行中…")
    try:
        result = run_agent_sync(payload, task_id=job_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent wiki build crashed job=%s", job_id)
        return {"success": False, "error": repr(exc), "job_id": job_id}

    result["job_id"] = job_id
    if result.get("success"):
        logger.info("agent wiki build done job=%s runs_ms=%s", job_id, result.get("runs_ms"))
        _write_progress(job_id, f"[agent] 完成（{result.get('runs_ms')}ms）")
    else:
        _write_progress(job_id, f"[agent] 失败: {str(result.get('error'))[:300]}")
    return result


def register_task_handlers() -> None:
    TASK_CLASS_REGISTRY[TASK_CLASS_AGENT_WIKI_BUILD] = _handle_agent_wiki_build


register_task_handlers()
