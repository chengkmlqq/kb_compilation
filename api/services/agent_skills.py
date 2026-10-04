"""Agent 会话（问答）的技能白名单消费 —— run_skill_script 工具注入。

对齐 WeKnora 的 CustomAgentConfig 技能分区：智能体配置里的
skills_enabled / skills_selection_mode(none|selected|all) / selected_skills
决定该智能体在问答 LLM 调用上能使用哪些技能包。技能包唯一来源是 kb_skill 表
（api/services/skills.read_skill_zip：MinIO storage_path 优先、package_zip 兜底）。

与 worker/agent/skill_tool.py（wiki 构建 worker 用，依赖 openai-agents SDK）的区别：
API 进程不装 agents SDK，故不注册 function_tool，而是
  1) 把白名单技能编成 OpenAI 兼容 function-calling 的 tools 负载
     （run_skill_script），交给 api/services/chat.py 的 ChatClient 下发；
  2) LLM 回调该工具时，由 run_skill_tool_call 在会话进程内把技能包解压到临时目录
     （执行完即删，对齐 install_task_skill_zip 的路径穿越防护），执行技能 scripts/
     下的 Python 脚本并把 stdout/stderr 文本回传给 LLM。

默认关闭：skills_enabled=False（或 skills_selection_mode=none/未知模式）时白名单为空，
不注入任何工具，问答输出与接入前完全一致。
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models.knowledge import KbAgent
from api.models.mcp_skill import KbSkill
from api.services.agents import AgentConfig, config_from_dict
from api.services.scope import visible_clauses
from api.services.skills import read_skill_zip

logger = logging.getLogger(__name__)

# skills_selection_mode 取值（与前端 AgentEditorModal / WeKnora 一致）
SKILLS_SELECTION_NONE = "none"
SKILLS_SELECTION_SELECTED = "selected"
SKILLS_SELECTION_ALL = "all"

# 技能脚本执行超时（秒）：会话问答是交互式流式，不像 worker 的 wiki 构建可以等到
# 1800s；这里控制在 60s 内，超时回传文本结果而不是挂死整个 SSE 流。
SKILL_SCRIPT_TIMEOUT_S = 60.0

# 注入的工具名（LLM 可见；与 worker skill_tool 的 run_skill_script 同名同语义）
RUN_SKILL_SCRIPT_TOOL = "run_skill_script"


def sys_executable() -> str:
    """运行技能脚本用的解释器（与 worker/agent/skill_tool.sys_executable 一致）。"""
    return os.environ.get("PYTHON", "python3")


# ---------------------------------------------------------------------------
# 白名单解析
# ---------------------------------------------------------------------------


def resolve_skill_whitelist(
    db: Session,
    cfg: AgentConfig,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> list[KbSkill]:
    """按 AgentConfig 技能配置解析白名单技能行（空列表 = 不注入任何技能）。

    - skills_enabled=False            → 空（默认关闭，不改变既有问答行为）
    - skills_selection_mode=none      → 空
    - skills_selection_mode=selected  → 仅 selected_skills 命中的技能（按技能名，
      与前端 Select 的 value=name 一致）；选中的技能名不存在则自然过滤
    - skills_selection_mode=all       → 全部可见技能（与技能列表页同一可见性口径）
    - 未知模式                        → 按 none 防御处理（脏配置不放大权限）

    可见性按调用方身份（user_id/team_name/is_admin）过滤，与技能管理列表同口径，
    避免越权把 personal/system 级技能暴露给无关问答者。
    """
    if not cfg.skills_enabled:
        return []
    mode = (cfg.skills_selection_mode or SKILLS_SELECTION_NONE).strip().lower()
    if mode == SKILLS_SELECTION_NONE:
        return []
    if mode not in (SKILLS_SELECTION_SELECTED, SKILLS_SELECTION_ALL):
        logger.warning("未知 skills_selection_mode=%r，按 none 处理", cfg.skills_selection_mode)
        return []

    stmt = select(KbSkill).where(KbSkill.state == "1")
    if mode == SKILLS_SELECTION_SELECTED:
        names = {str(s).strip() for s in (cfg.selected_skills or []) if str(s).strip()}
        if not names:
            return []
        stmt = stmt.where(KbSkill.name.in_(names))
    # 可见性过滤（无身份上下文时不过滤，如系统内置 agent）
    clauses = visible_clauses(caller_user_id, caller_team_name, is_sys_admin, KbSkill)
    if clauses:
        stmt = stmt.where(*clauses)
    rows = db.execute(stmt.order_by(KbSkill.name)).scalars().all()
    return list(rows)


def build_skill_tools(skills: list[KbSkill]) -> list[dict]:
    """把白名单技能编成 OpenAI 兼容 tools 负载（空白名单 → 空列表，不注入）。

    最小注入形态：单个 run_skill_script 工具，description 里枚举白名单技能名，
    让 LLM 知道只能调用哪些技能（白名单即金口，工具本身再次校验）。
    """
    if not skills:
        return []
    names = sorted({s.name for s in skills if s.name})
    description = (
        "运行已授权技能 scripts/ 目录下的 Python 脚本并返回其 stdout/stderr。"
        "脚本非交互、有超时；仅可运行以下白名单内的技能："
        + "、".join(names)
        + "。先确认 skill_name 与 script 文件名，再传 args 参数。"
    )
    return [
        {
            "type": "function",
            "function": {
                "name": RUN_SKILL_SCRIPT_TOOL,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "技能名（必须属于白名单）",
                        },
                        "script": {
                            "type": "string",
                            "description": "技能 scripts/ 目录下的脚本文件名（如 run.py）",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "传给脚本的命令行参数",
                        },
                    },
                    "required": ["skill_name", "script"],
                },
            },
        }
    ]


def agent_skill_tool_context(
    db: Session,
    agent: KbAgent,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> tuple[list[KbSkill], list[dict] | None, int]:
    """路由侧聚合：agent 配置 → (白名单技能, tools 负载, 最大工具回合)。

    tools 为 None 表示本 agent 未启用技能（调用方按旧链路走，不注入任何工具）。
    返回的 max_tool_iterations 供问答工具循环使用（AgentConfig.max_iterations）。
    """
    cfg = config_from_dict(agent.config or {})
    skills = resolve_skill_whitelist(
        db, cfg,
        caller_user_id=caller_user_id,
        caller_team_name=caller_team_name,
        is_sys_admin=is_sys_admin,
    )
    tools = build_skill_tools(skills) if skills else None
    max_iterations = max(1, int(cfg.max_iterations or 3))
    return skills, tools, max_iterations


# ---------------------------------------------------------------------------
# 工具调用执行（会话进程内：临时解压 → 跑脚本 → 回传 stdout）
# ---------------------------------------------------------------------------


def run_skill_tool_call(
    skills: list[KbSkill],
    tool_call: dict,
    timeout: float = SKILL_SCRIPT_TIMEOUT_S,
) -> str:
    """执行一次 LLM 发起的工具调用（白名单内），返回文本结果。

    tool_call 形如 {"id","name","arguments"}（arguments 为 JSON 字符串）。
    幂等校验：工具名必须为 run_skill_script、skill_name 必须在白名单内 ——
    即使 LLM 幻觉出白名单外的技能名，也只会得到错误提示，不会执行。
    """
    name = str(tool_call.get("name") or "")
    if name != RUN_SKILL_SCRIPT_TOOL:
        return f"未授权的工具调用: {name!r}"
    try:
        args = json.loads(str(tool_call.get("arguments") or "{}"))
    except json.JSONDecodeError:
        return "工具参数解析失败：arguments 不是合法 JSON"
    if not isinstance(args, dict):
        return "工具参数格式错误：期望 JSON 对象"
    skill_name = str(args.get("skill_name") or "").strip()
    skill = next((s for s in skills if s.name == skill_name), None)
    if skill is None:
        available = "、".join(sorted({s.name for s in skills})) or "无"
        return f"技能不在白名单内: {skill_name}。可用技能: {available}"
    script = str(args.get("script") or "").strip()
    raw_args = args.get("args") or []
    script_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
    return execute_skill_script(skill, script, script_args, timeout=timeout)


def execute_skill_script(
    skill: KbSkill,
    script: str,
    args: list[str] | None = None,
    timeout: float = SKILL_SCRIPT_TIMEOUT_S,
) -> str:
    """执行技能 scripts/ 下的 Python 脚本，回传 stdout/stderr 文本。

    技能包临时解压到每调用独立目录（对齐 install_task_skill_zip 的约束：路径穿越
    防护 + 失败/成功都清理临时目录，不落盘常驻）。脚本名仅允许文件名（拒绝路径分隔
    与 '..'），防止绕过 scripts/ 目录读取任意文件。
    """
    if args is None:
        args = []
    # 脚本名校验：必须落在 scripts/ 目录内（文件名形式）
    if not script or "/" in script or "\\" in script or script.startswith(".") or not script.endswith(".py"):
        return f"非法脚本名: {script!r}（仅允许 scripts/ 目录下的 .py 文件名）"

    data = read_skill_zip(skill)
    if not data:
        return f"技能包不可用: {skill.name}（未找到技能 ZIP）"

    tmp_root = Path(tempfile.mkdtemp(prefix="kb-agent-skill-"))
    try:
        root_dir = _extract_skill_zip(data, tmp_root)
        script_path = root_dir / "scripts" / script
        if not script_path.is_file():
            scripts_dir = root_dir / "scripts"
            available = (
                ", ".join(sorted(p.name for p in scripts_dir.glob("*.py") if p.is_file()))
                if scripts_dir.is_dir()
                else ""
            )
            return f"脚本不存在: {skill.name}/scripts/{script}。可用脚本: {available or '无'}"
        argv = [sys_executable(), str(script_path), *(str(a) for a in args)]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return f"脚本超时(>{timeout:g}s): {script}"
        except Exception as exc:  # noqa: BLE001 — 执行失败回传文本，不炸整个 SSE 流
            logger.exception("skill script launch failed: %s", argv)
            return f"脚本执行出错: {exc!r}"
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        ret = f"(exit {proc.returncode})"
        if out:
            ret += f"\nstdout:\n{out}"
        if err:
            ret += f"\nstderr:\n{err}"
        return ret
    except ValueError as exc:  # 包结构错误（无 SKILL.md / 路径穿越）
        return f"技能包解析失败: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("skill execute failed: %s", skill.name)
        return f"技能执行出错: {exc!r}"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _extract_skill_zip(data: bytes, tmp_root: Path) -> Path:
    """把技能 ZIP 安全解压到临时目录，返回技能根目录（含 SKILL.md）。

    结构兼容 <skill>/SKILL.md 与根级 SKILL.md。路径穿越防护与
    worker/agent/skill_tool.install_task_skill_zip 一致：clean 后必须仍落在
    tmp_root 内（校验带 os.sep，防止前缀匹配绕过）。
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是有效的 ZIP: {exc}") from exc
    resolved_root = str(tmp_root.resolve())
    for member in z.namelist():
        target = (tmp_root / member).resolve()
        if not str(target).startswith(resolved_root + os.sep):
            raise ValueError(f"非法技能包路径: {member}")
        if member.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    # 兼容根级 SKILL.md 或 <skill>/SKILL.md
    if (tmp_root / "SKILL.md").is_file():
        return tmp_root
    for skill_dir in sorted(tmp_root.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
            return skill_dir
    raise ValueError("ZIP 内未找到含 SKILL.md 的技能目录")


__all__ = [
    "RUN_SKILL_SCRIPT_TOOL",
    "SKILLS_SELECTION_ALL",
    "SKILLS_SELECTION_NONE",
    "SKILLS_SELECTION_SELECTED",
    "SKILL_SCRIPT_TIMEOUT_S",
    "agent_skill_tool_context",
    "build_skill_tools",
    "execute_skill_script",
    "resolve_skill_whitelist",
    "run_skill_tool_call",
    "sys_executable",
]