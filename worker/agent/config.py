"""worker/agent（内联 agent 运行时）配置。

对齐 agent-gateway 的 gateway/config.py，取值来源改为 kb_compilation 的
容器 env（前缀 WORKER_AGENT_*）。默认值参考 gateway，技能路径改到
kb_compilation 的代码根 /srv/kb（agent worker 镜像的工作目录）。

注意：kb_compilation 的技能**不预装**在这里——唯一来源是 kb_skill 表，
任务提交时随 config 下发 ZIP，由 skill_tool.install_task_skill_zip 解压到
per-task 临时目录执行完即删（1A）。这里的 SKILLS_DIR 仅作为可选的预装扫描源。
"""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Skill 配置（skill-as-tool：读 SKILL.md 的技能包）
# --------------------------------------------------------------------------- #
# 只读预装目录（本部署通常为空，技能走任务级 ZIP 下发）。
SKILLS_DIR: str = os.environ.get("WORKER_AGENT_SKILLS_DIR", "/srv/kb/agent-skills")
# 技能安装目录（可写）：与只读预装目录共同构成扫描源。
SKILLS_INSTALL_DIR: str = os.environ.get(
    "WORKER_AGENT_SKILLS_INSTALL_DIR", "/srv/kb/data/agent-skills"
)
# 启用的 skill 白名单；空列表 = 全部启用。
SKILLS_ENABLED: list[str] = [
    s.strip()
    for s in os.environ.get("WORKER_AGENT_SKILLS_ENABLED", "").split(",")
    if s.strip()
]
# skill scripts 运行超时（秒）。wiki 构建脚本可能较长，默认放宽到 1800s。
SKILL_SCRIPT_TIMEOUT_S: float = float(
    os.environ.get("WORKER_AGENT_SKILL_SCRIPT_TIMEOUT_S", "1800")
)

# --------------------------------------------------------------------------- #
# LLM 端点（OpenAI 兼容网关）
# 任务级 model/base_url/api_key（由 _handle_agent_wiki_build 从 kb_model 注册表
# 解析后注入 config）优先于这里的容器默认值。
# --------------------------------------------------------------------------- #
LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "https://inferaiapi.com/v1")
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
# OpenAI SDK 429/5xx 自动重试次数（InferAI 限流时 429 瞬时）。
LLM_MAX_RETRIES: int = _int("LLM_MAX_RETRIES", 5)

# --------------------------------------------------------------------------- #
# Agent 运行
# --------------------------------------------------------------------------- #
# 单任务最大 agent 回合数（openai-agents Runner max_turns）。技能类任务
# （读操作指引 + 跑脚本）远超 SDK 默认 10 回合。
AGENT_MAX_TURNS: int = _int("WORKER_AGENT_MAX_TURNS", 60)  # env/默认（DB 覆盖见 agent_max_turns()）


def skill_script_timeout_s() -> float:
    """技能脚本/命令执行超时（秒）：平台参数 > env > 默认 1800。"""
    from worker.platform_params import get_platform_int

    return float(
        get_platform_int(
            "AGENT_SKILL_SCRIPT_TIMEOUT", env_name="WORKER_AGENT_SKILL_SCRIPT_TIMEOUT_S", default=1800
        )
    )


def agent_llm_max_retries() -> int:
    """OpenAI SDK 的 429/5xx 重试次数：平台参数 > env > 默认 5。"""
    from worker.platform_params import get_platform_int

    return get_platform_int(
        "AGENT_LLM_MAX_RETRIES", env_name="LLM_MAX_RETRIES", default=LLM_MAX_RETRIES
    )


def agent_max_turns() -> int:
    """agent 编排回合上限：平台参数（页面可改） > env > 默认 60。

    平台参数存 modo_dim（PLATFORM_CONFIG.AGENT_MAX_TURNS），由「系统 → 平台
    参数」页面维护；worker 侧 15s TTL 缓存，改动即时生效（无需重启）。
    """
    from worker.platform_params import get_platform_int

    return get_platform_int(
        "AGENT_MAX_TURNS", env_name="WORKER_AGENT_MAX_TURNS", default=AGENT_MAX_TURNS
    )
