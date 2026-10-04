"""worker 内联的 agent 运行时：把 openai-agents 的 agent 循环跑在 Celery worker 里。

对应 agent-gateway 的 `runner.run_task`（docker/gateway/runner.py），但去掉了
HTTP 服务层与 Tortoise ORM：
  - 任务来源：Celery `task_params`（不再是 gateway DB 的 agent_tasks 表）
  - 状态回写：返回值交给 `KbAgentGatewayTask` handler 写 modo_job（不再 validate_transition）
  - 排队/取消：Celery broker + Flower 负责（不再需要 TaskPool / POST /tasks/{id}/cancel）

保留 gateway 的全部 agent 能力：env 上下文注入（WEKNORA_*）、任务级技能 ZIP 随包
下发执行（1A）、任务级 LLM client（并发安全，不做全局替换）、SDK trace。

同步桥：Celery handler 是同步的，SDK 是 async 的 → `run_agent_sync` 用
asyncio.run 包装整个 agent 循环。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any

from agents import Agent, Runner
from agents.tracing import trace as trace_ctx

from worker.agent import config
from worker.agent.skill_tool import (
    build_skill_tools,
    cleanup_task_skills,
    install_task_skill_zip,
    set_task_skills,
)

logger = logging.getLogger(__name__)


def _decode_skill_zips(cfg: dict) -> list[bytes]:
    """解析任务级技能 ZIP（1A：kb_compilation 从 kb_skill 表取 package_zip 后 base64 下发）。"""
    raw = cfg.get("skill_zip_base64") or cfg.get("skill_zip")
    out: list[bytes] = []
    if not raw:
        return out
    try:
        if isinstance(raw, str):
            out.append(base64.b64decode(raw))
        elif isinstance(raw, list):
            for item in raw:
                out.append(base64.b64decode(item) if isinstance(item, str) else bytes(item))
        elif isinstance(raw, (bytes, bytearray)):
            out.append(bytes(raw))
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill_zip_base64 decode failed: %s", exc)
    return out


def _inject_task_env(cfg: dict) -> None:
    """任务级上下文注入进程环境变量：技能脚本（run_skill_script 子进程）读取。

    - kb_id / knowledge_id / doc_name → WEKNORA_*：本次任务的目标知识库与文档
    - model / base_url / api_key    → WEKNORA_LLM_*：任务级 LLM（优先于容器 env）
    - skill                         → WEKNORA_SKILL：任务绑定技能
    """
    for k in ("kb_id", "knowledge_id", "doc_name"):
        if cfg.get(k):
            os.environ[f"WEKNORA_{k.upper()}"] = str(cfg[k])
    for k in ("model", "base_url", "api_key"):
        if cfg.get(k):
            os.environ[f"WEKNORA_LLM_{k.upper()}"] = str(cfg[k])
    if cfg.get("skill"):
        os.environ["WEKNORA_SKILL"] = str(cfg["skill"])


def _build_task_model(cfg: dict):
    """任务级 LLM client：每任务自建 AsyncOpenAI，不做 set_default_openai_client 全局替换
    （多 worker 槽并发会互相覆盖全局 client）。无任务级配置则用容器 env 默认模型。"""
    model = str(cfg.get("model") or config.LLM_MODEL)
    base_url = str(cfg.get("base_url") or config.LLM_BASE_URL)
    api_key = str(cfg.get("api_key") or config.LLM_API_KEY)
    if cfg.get("model") or cfg.get("base_url") or cfg.get("api_key"):
        from openai import AsyncOpenAI
        from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=config.LLM_MAX_RETRIES,
        )
        return OpenAIChatCompletionsModel(model=model, openai_client=client), model
    return model, model


async def run_agent(payload: dict[str, Any], task_id: str = "") -> dict[str, Any]:
    """跑一次 agent 循环。payload = {input, instructions, agent_name, config{...}}。

    返回 {success, output, error, runs_ms, sdk_trace_id}。
    """
    t0 = time.monotonic()
    cfg = dict(payload.get("config") or {})
    task_id = task_id or str(cfg.get("job_id") or "task")

    _inject_task_env(cfg)

    task_skill_zips = _decode_skill_zips(cfg)
    if task_skill_zips:
        specs = []
        for i, data in enumerate(task_skill_zips):
            try:
                specs.extend(install_task_skill_zip(data, f"{task_id}-{i}"))
            except ValueError as exc:
                logger.warning("task_skill_zip_invalid job=%s: %s", task_id, exc)
        if specs:
            set_task_skills({s.name: s for s in specs})
            logger.info("task_skill_loaded job=%s skills=%s", task_id, [s.name for s in specs])

    sdk_trace_id: str | None = None
    try:
        tools = build_skill_tools()
        instructions = str(payload.get("instructions") or cfg.get("instructions") or "你是一个通用助手。")
        if tools:
            instructions += "\n\n你可使用以下技能工具（list_skills 查看可用技能）。"
        agent_model, _ = _build_task_model(cfg)
        agent = Agent(
            name=str(payload.get("agent_name") or cfg.get("agent_name") or "kb-agent-worker"),
            instructions=instructions,
            model=agent_model,
            tools=tools if tools else None,
            # 主 agent 循环不挂 MCP（与 gateway 2026-09-25 修复一致：MCP 工具会干扰
            # LLM 选错工具；技能脚本内部自行连 mcp-gateway）。
            mcp_servers=[],
        )
        with trace_ctx("kb agent workflow") as active_trace:
            sdk_trace_id = getattr(active_trace, "trace_id", None)
            result = await Runner.run(
                agent,
                input=str(payload.get("input") or ""),
                max_turns=config.AGENT_MAX_TURNS,
            )
        runs_ms = int((time.monotonic() - t0) * 1000)
        logger.info("agent_run_end job=%s runs_ms=%s", task_id, runs_ms)
        return {
            "success": True,
            "output": result.final_output,
            "runs_ms": runs_ms,
            "sdk_trace_id": sdk_trace_id,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        runs_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("agent_run_error job=%s", task_id)
        return {
            "success": False,
            "error": repr(exc),
            "runs_ms": runs_ms,
            "sdk_trace_id": sdk_trace_id,
        }
    finally:
        if task_skill_zips:
            try:
                cleanup_task_skills(task_id)
                logger.info("task_skill_cleaned job=%s", task_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("task_skill_cleanup_failed job=%s: %s", task_id, exc)


def run_agent_sync(payload: dict[str, Any], task_id: str = "") -> dict[str, Any]:
    """同步桥：Celery handler（同步）调用 SDK agent 循环（async）。"""
    return asyncio.run(run_agent(payload, task_id=task_id))
