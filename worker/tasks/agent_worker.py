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
    """把 agent 运行进度写入 MinIO logs/<job_id>.log + error_message（best-effort）。"""
    from worker.tasks.log_sink import append_job_log

    append_job_log(job_id, message)


def _kb_owner_context(db, kb_id: str) -> dict:
    """知识库权限基准：创建者/拥有者的个人+团队上下文。

    wiki 构建是后台任务（无登录用户），可用的权限基准 = 知识库的
    owner_user_id / owner_team_name（创建时由 scope 校验写入）。worker 按
    此解析 MCP 与技能：拥有者个人 OR 拥有者团队 OR (系统且拥有者是管理员)。
    """
    from sqlalchemy import select as _select

    from api.models.knowledge import KbDatasource

    kb = db.execute(_select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
    if not kb:
        return {"user_id": "", "team_name": "", "is_admin": False}
    owner_uid = str(getattr(kb, "owner_user_id", "") or "")
    owner_team = str(
        getattr(kb, "owner_team_name", "") or getattr(kb, "team_name", "") or ""
    )
    try:
        from api.services.scope import is_admin

        admin = bool(owner_uid and is_admin(db, owner_uid))
    except Exception:  # noqa: BLE001
        admin = False
    return {"user_id": owner_uid, "team_name": owner_team, "is_admin": admin}


def _attach_skill_zip(config: dict[str, Any], kb_id: str = "") -> None:
    """1A：从 kb_skill 表取知识库绑定技能的 ZIP（bytes）。

    可见性按 **知识库创建者的个人/团队/系统权限** 过滤（owner_uid/owner_team
    由 _kb_owner_context 提供）——不是空上下文+sys_admin（那只能拿到系统级
    技能，个人/团队的技能会被漏掉）。
    """
    skill_name = _first_str(config, "skill")
    if not skill_name:
        return
    try:
        from api.services.skills import get_skill_package, list_skills, read_skill_zip

        db = get_sessionmaker()()
        try:
            ctx = _kb_owner_context(db, kb_id) if kb_id else {
                "user_id": "", "team_name": "", "is_admin": False,
            }
            item = next(
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
            if item:
                row = get_skill_package(db, item["id"])
                pkg = read_skill_zip(row) if row else None
                if pkg:
                    config["skill_zip"] = pkg
                    config["skill"] = item["name"]
            else:
                logger.warning(
                    "skill not visible to kb owner (kb=%s skill=%s owner=%s)",
                    kb_id, skill_name, ctx["user_id"] or "(none)",
                )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to load skill zip for %s: %s", skill_name, exc)


def _attach_mcp_servers(config: dict[str, Any], kb_id: str = "") -> None:
    """按知识库创建者的个人/团队权限解析启用 MCP，注入任务级配置。

    现状缺口：此前内联 worker 完全没挂 MCP（runtime mcp_servers=[]），外部
    gateway 路径也只传空上下文+sys_admin → 个人/团队的 MCP 永远不可见。
    """
    try:
        from api.services.mcps import resolve_task_mcp_servers

        db = get_sessionmaker()()
        try:
            ctx = _kb_owner_context(db, kb_id) if kb_id else {
                "user_id": "", "team_name": "", "is_admin": False,
            }
            mcps = resolve_task_mcp_servers(
                db,
                caller_user_id=ctx["user_id"],
                caller_team_name=ctx["team_name"],
                is_sys_admin=ctx["is_admin"],
            )
        finally:
            db.close()
        if mcps:
            config["mcp_servers"] = mcps
            logger.info("agent_mcp_attached kb=%s count=%s", kb_id, len(mcps))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to resolve mcp servers for kb=%s: %s", kb_id, exc)


def _resolve_llm_config(config: dict[str, Any], kb_id: str = "") -> None:
    """补齐 LLM 配置（model/base_url/api_key），优先级：

    1. 任务已带（_enqueue_wiki_skill_task 从 WIKI_LLM_* 注入）→ 不动
    2. **KB 级绑定**（WeKnora wiki_config.synthesis_model_id）
    3. 模型注册表默认 chat 模型（resolve_model_config）
    4. env 兜底：WIKI_LLM_* → AI_CHAT_* → LLM_*

    第 4 步不可省：后台任务无用户上下文，personal scope 的模型不可见；而
    system 默认模型常处于 state=0（禁用），此时注册表解析返回 None —— 没有 env
    兜底就会重演 d9affbc 的 `WIKI_LLM_API_KEY 未配置`（agent 拿不到凭证直接失败）。
    """
    if config.get("model") and config.get("base_url") and config.get("api_key"):
        return

    def _set(base: str, model: str, key: str) -> bool:
        if base and model and key:
            config.setdefault("base_url", base)
            config.setdefault("model", model)
            config.setdefault("api_key", key)
            return True
        return False

    # 2) KB 级绑定模型
    if kb_id:
        try:
            from sqlalchemy import select as _select

            from api.models.knowledge import KbDatasource

            db = get_sessionmaker()()
            try:
                kb = db.execute(_select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
            finally:
                db.close()
            bound = ((getattr(kb, "wiki_config", None) or {}).get("synthesis_model_id") or "") if kb else ""
            if bound:
                from api.services.models import resolve_model_config

                db = get_sessionmaker()()
                try:
                    r = resolve_model_config(db, "chat", model_id=bound)
                finally:
                    db.close()
                if r and _set(r.get("base_url", ""), r.get("model", ""), r.get("api_key", "")):
                    logger.info("agent llm from kb binding kb=%s model=%s", kb_id, bound)
                    return
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb-level model binding failed kb=%s: %s", kb_id, exc)

    # 3) 模型注册表默认（后台任务无用户上下文，可能解析不到 → 落到 env）
    try:
        from api.services.models import resolve_model_config

        db = get_sessionmaker()()
        try:
            resolved = resolve_model_config(db, "chat", is_sys_admin=True)
        finally:
            db.close()
        if resolved and _set(
            resolved.get("base_url") or "", resolved.get("model") or "", resolved.get("api_key") or ""
        ):
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to resolve chat model for agent: %s", exc)

    # 4) env 兜底
    import os

    base = (os.getenv("WIKI_LLM_BASE_URL") or os.getenv("AI_CHAT_API_ENDPOINT") or os.getenv("LLM_BASE_URL") or "").strip()
    model = (os.getenv("WIKI_LLM_MODEL") or os.getenv("AI_CHAT_MODEL") or os.getenv("LLM_MODEL") or "").strip()
    key = (os.getenv("WIKI_LLM_API_KEY") or os.getenv("AI_CHAT_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if _set(base, model, key):
        logger.info("agent llm from env fallback model=%s", model)
    else:
        logger.error(
            "agent LLM 未配置：注册表无 system 默认 chat 模型且 env(WIKI_LLM_*/AI_CHAT_*/LLM_*) 为空"
        )


def _handle_agent_wiki_build(job_id: str, task_params: str | None) -> dict[str, Any]:
    """worker 内联执行 agent（openai-agents SDK），完成 wiki 构建。"""
    params = _params(task_params)
    config: dict[str, Any] = dict(params.get("config") or {})
    config["job_id"] = job_id

    _attach_skill_zip(config, kb_id=str(config.get("kb_id") or ""))
    _attach_mcp_servers(config, kb_id=str(config.get("kb_id") or ""))
    _resolve_llm_config(config, kb_id=str(config.get("kb_id") or ""))

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
    # ── 失败如实标记（2026-10-06）：agent 汇报"构建未完成/两次超时"等 → 标失败
    if result.get("success"):
        _out = str(result.get("output") or "")
        _fail_kw = ("构建未完成", "两次超时", "均确认失败", "执行结果：构建失败",
                    "构建失败", "未完成（两次超时）", "两次运行均已确认失败")
        if any(k in _out for k in _fail_kw):
            result["success"] = False
            result["error"] = f"agent 汇报构建失败: {_out[:200]}"
            _write_progress(job_id, f"[agent] 构建未完成（agent 自报失败），任务标记 FAILED")
    if result.get("success"):
        logger.info("agent wiki build done job=%s runs_ms=%s", job_id, result.get("runs_ms"))
        _write_progress(job_id, f"[agent] 完成（{result.get('runs_ms')}ms）")
    else:
        _write_progress(job_id, f"[agent] 失败: {str(result.get('error'))[:300]}")

    # ── agent span 落盘（trace 收集 → logs/traces/{job_id}.json + 任务日志摘要）──
    try:
        tid = str(result.get("sdk_trace_id") or "")
        if tid:
            from worker.agent.trace_store import summarize_spans, pop_trace_spans

            spans = pop_trace_spans(tid)
            if spans:
                from api.config import get_settings
                from pathlib import Path

                settings = get_settings()

                tf = Path(settings.kb_storage_dir) / f"logs/traces/{job_id}.json"
                tf.parent.mkdir(parents=True, exist_ok=True)
                tf.write_text(json.dumps(spans, ensure_ascii=False, default=str), encoding="utf-8")
                summary = summarize_spans(spans)
                line = (
                    f"Agent Trace: trace_id={tid} spans={summary['span_count']} "
                    f"耗时={summary['duration_ms']}ms LLM调用={summary['llm_calls']} "
                    f"工具={','.join(summary['tools']) or '-'}"
                )
                logger.info("agent trace saved job=%s %s", job_id, line)
                _write_progress(job_id, line)
    except Exception as exc:  # noqa: BLE001 - trace 落盘失败不阻塞任务
        logger.warning("agent trace persist failed job=%s: %s", job_id, exc)

    # ── 技能 LLM 事件聚合（logs/events/{job_id}.jsonl → 摘要 + 事件文件）──
    try:
        from api.config import get_settings as _gs
        from pathlib import Path as _P

        ev_file = _P(_gs().kb_storage_dir) / f"logs/events/{job_id}.jsonl"
        if ev_file.exists():
            evs = []
            for line in ev_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        evs.append(json.loads(line))
                    except Exception:
                        pass
            ok_evs = [e for e in evs if e.get("kind") == "ok"]
            err_evs = [e for e in evs if e.get("kind") == "error"]
            step_evs = [e for e in evs if e.get("kind") == "step"]
            # 步骤时间线：start→done/fail 配对，输出有序 [{step, status, ms}]
            steps: list[dict] = []
            step_open: dict[str, dict] = {}
            for e in step_evs:
                st = str(e.get("step") or "?")
                status = str(e.get("status") or "")
                if status == "start":
                    step_open[st] = {"step": st, "status": "running", "ms": 0}
                elif status in ("done", "fail") and st in step_open:
                    prev = step_open.pop(st)
                    prev["status"] = "done" if status == "done" else "fail"
                    prev["ms"] = int(e.get("ms") or 0)
                    steps.append(prev)
                elif status in ("done", "fail"):
                    steps.append({"step": st, "status": "done" if status == "done" else "fail",
                                  "ms": int(e.get("ms") or 0)})
            for st in step_open:
                steps.append({**step_open[st], "status": "interrupted"})
            retries = sum(len(e.get("retry_events") or []) for e in evs)
            retry_reasons: dict[str, int] = {}
            backoff_total = 0
            for e in evs:
                for r in e.get("retry_events") or []:
                    retry_reasons[r.get("reason", "?")] = retry_reasons.get(r.get("reason", "?"), 0) + 1
                    backoff_total += int(r.get("backoff_s") or 0)
            wait_total = sum(int(e.get("wait_ms") or 0) for e in evs) / 1000
            llm_total = sum(int(e.get("llm_ms") or 0) for e in evs) / 1000
            phases: dict[str, int] = {}
            for e in evs:
                p = e.get("phase") or "?"
                phases[p] = phases.get(p, 0) + 1
            ev_summary = {
                "total": len(evs), "ok": len(ok_evs), "error": len(err_evs),
                "retries": retries, "retry_reasons": retry_reasons,
                "backoff_total_s": backoff_total, "wait_total_s": round(wait_total, 1),
                "llm_total_s": round(llm_total, 1), "phases": phases,
                "steps": steps,
            }
            ev_out = _P(_gs().kb_storage_dir) / f"logs/events/{job_id}.summary.json"
            ev_out.write_text(json.dumps(ev_summary, ensure_ascii=False), encoding="utf-8")
            slowest = max(steps, key=lambda s: s.get("ms") or 0) if steps else None
            ev_line = (
                f"LLM 明细: 调用{len(ok_evs)} 失败{len(err_evs)} 重试{retries}次"
                f"(429:{retry_reasons.get('http_429', 0)} 超时:{retry_reasons.get('network', 0)}) "
                f"退避等待{backoff_total}s 节流等待{ev_summary['wait_total_s']}s LLM耗时{ev_summary['llm_total_s']}s"
            )
            if steps:
                done_steps = [s for s in steps if s.get("status") in ("done", "fail")]
                step_ms = sum(s.get("ms") or 0 for s in done_steps) / 1000
                ev_line += (
                    f" | 步骤{len(done_steps)}步 合计{step_ms:.0f}s"
                    + (f" 最慢={slowest['step']} {round((slowest['ms'] or 0) / 1000)}s" if slowest else "")
                )
            _write_progress(job_id, ev_line)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent events aggregate failed job=%s: %s", job_id, exc)
    return result


def register_task_handlers() -> None:
    TASK_CLASS_REGISTRY[TASK_CLASS_AGENT_WIKI_BUILD] = _handle_agent_wiki_build


register_task_handlers()
