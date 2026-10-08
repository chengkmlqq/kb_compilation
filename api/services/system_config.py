"""System configuration services — model config (modo_dim) + agent-gateway proxies.

Three capabilities backing the frontend "模型配置 / MCP 管理 / 技能管理" pages:

1. Model config: read/write chat + embedding model settings on the
   `modo_dim` table (dim_group=SYSTEM_CONFIG). The chat/embedding clients
   already resolve DB > env, so writing here takes effect without restarting
   (60s TTL cache is invalidated on write).

2. MCP servers: proxy to the agent-gateway's /mcp/servers REST API (which
   persists to file and hot-reloads). Requires AGENT_GATEWAY_ADMIN_TOKEN.

3. Skills: proxy to the agent-gateway's /skills REST API (install/delete
   hot-refresh the skill registry).
"""

from __future__ import annotations

import logging
import time
import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models.framework import Dim
from api.services.config_cache import invalidate_system_config_cache
from api.services.runtime_config import is_sensitive_key, mask_sensitive_value, normalize_value

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model config (modo_dim, dim_group=SYSTEM_CONFIG)
# ---------------------------------------------------------------------------

MODEL_CONFIG_GROUP = "SYSTEM_CONFIG"

# (dim_code, 中文名, 是否敏感, 说明)
MODEL_CONFIG_FIELDS: list[tuple[str, str, bool, str]] = [
    ("AI_CHAT_API_ENDPOINT", "问答模型端点", False, "OpenAI 兼容 /v1 地址，用于问答与 wiki 实体抽取"),
    ("AI_CHAT_API_KEY", "问答模型密钥", True, "Bearer 密钥（回显掩码，留空=不变）"),
    ("AI_CHAT_MODEL", "问答模型", False, "如 deepseek-v4-pro"),
    ("EMBEDDING_BASE_URL", "向量模型端点", False, "OpenAI 兼容 /v1 地址，用于文档向量化"),
    ("EMBEDDING_API_KEY", "向量模型密钥", True, "Bearer 密钥（回显掩码，留空=不变）"),
    ("EMBEDDING_MODEL", "向量模型", False, "如 bge-m3"),
    ("EMBEDDING_DIM", "向量维度", False, "向量维度，默认 1024"),
    ("EMBEDDING_PROVIDER", "向量服务商", False, "如 openai / ollama / dashscope"),
]


def _dim_map(db: Session, codes: list[str], group: str = MODEL_CONFIG_GROUP) -> dict[str, str]:
    rows = (
        db.execute(
            select(Dim.dim_code, Dim.dim_value).where(
                Dim.dim_group == group,
                Dim.dim_code.in_(codes),
                Dim.state == "1",
            )
        )
        .all()
    )
    return {code: (value or "") for code, value in rows}


def get_model_config(db: Session) -> dict:
    """Return model config merged from DB and env (env is fallback when unset)."""
    env = get_settings()
    codes = [f[0] for f in MODEL_CONFIG_FIELDS]
    db_map = _dim_map(db, codes)
    out = []
    for code, label, sensitive, desc in MODEL_CONFIG_FIELDS:
        value = db_map.get(code, "")
        source = "db" if value else "env"
        if not value:
            value = getattr(env, code, "") or ""
        out.append(
            {
                "code": code,
                "label": label,
                "description": desc,
                "value": mask_sensitive_value(value) if sensitive else value,
                "sensitive": sensitive,
                "source": source,
            }
        )
    return {"items": out}


def save_model_config(db: Session, items: list[dict]) -> dict:
    """Upsert model config into modo_dim. Sensitive fields masked with
    '****xxxx' mean 'keep existing DB value' (like the gateway's MCP pattern)."""
    env = get_settings()
    allowed = {f[0] for f in MODEL_CONFIG_FIELDS}
    saved: list[str] = []
    for item in items:
        code = normalize_value(str(item.get("code", "")))
        if code not in allowed:
            continue
        raw_value = item.get("value")
        if raw_value is None:
            continue
        value = normalize_value(str(raw_value))
        sensitive = is_sensitive_key(code)
        # 掩码（**** 开头）= 保持不变 → 跳过（保留 DB 现值）
        if sensitive and value.startswith("****"):
            continue
        existing = (
            db.execute(
                select(Dim).where(Dim.dim_group == MODEL_CONFIG_GROUP, Dim.dim_code == code)
            )
            .scalars()
            .first()
        )
        if existing:
            existing.dim_value = value
        else:
            db.add(
                Dim(
                    id=uuid.uuid4().hex,
                    dim_code=code,
                    dim_group=MODEL_CONFIG_GROUP,
                    dim_value=value,
                    dim_desc=dict((f[0], f[3]) for f in MODEL_CONFIG_FIELDS).get(code, ""),
                    seq=len(saved),
                    state="1",
                )
            )
        saved.append(code)
    db.commit()
    invalidate_system_config_cache()
    return {"saved": saved, "count": len(saved)}


def test_model_endpoint(base_url: str, api_key: str, model: str, timeout: float = 10.0) -> dict:
    """Probe an OpenAI-compatible chat or embedding endpoint."""
    base = (base_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=400, detail="端点地址为空")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # 优先试 /models（chat）；不行再试 /embeddings
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base}/models", headers=headers)
            if resp.status_code < 400:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
                return {"ok": True, "kind": "chat", "models": models[:20], "model_matches": model in models}
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base}/embeddings",
                json={"model": model, "input": ["ping"]},
                headers=headers,
            )
            if resp.status_code < 400:
                return {"ok": True, "kind": "embedding", "model": model}
        return {"ok": False, "error": f"端点 {base} 无法识别为 chat 或 embedding 服务 (http {resp.status_code})"}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Agent-gateway proxy helpers
# ---------------------------------------------------------------------------


def _gateway_headers() -> dict:
    token = get_settings().AGENT_GATEWAY_ADMIN_TOKEN
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    return headers


def _gateway_url(path: str) -> str:
    base = get_settings().AGENT_GATEWAY_BASE_URL.rstrip("/")
    return f"{base}{path}"


def _proxy_get(path: str, timeout: float = 15.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(_gateway_url(path), headers=_gateway_headers())
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"agent-gateway {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"agent-gateway 不可达: {e}") from e


def _proxy_post(path: str, json_body: dict | None = None, timeout: float = 30.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(_gateway_url(path), json=json_body, headers=_gateway_headers())
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"agent-gateway {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"agent-gateway 不可达: {e}") from e


def _proxy_put(path: str, json_body: dict, timeout: float = 30.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.put(_gateway_url(path), json=json_body, headers=_gateway_headers())
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"agent-gateway {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"agent-gateway 不可达: {e}") from e


def _proxy_delete(path: str, timeout: float = 15.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.delete(_gateway_url(path), headers=_gateway_headers())
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"agent-gateway {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"agent-gateway 不可达: {e}") from e


def list_mcp_servers() -> dict:
    return _proxy_get("/mcp/servers")


def save_mcp_servers(servers: list[dict], verify: bool = False, force: bool = False) -> dict:
    params = ""
    if verify:
        params += "&verify=true" if params else "?verify=true"
    if force:
        params += "&force=true" if params else "?force=true"
    return _proxy_put(f"/mcp/servers{params}", {"servers": servers})


def test_mcp_server(item: dict) -> dict:
    return _proxy_post("/mcp/servers/test", item)


def delete_mcp_server(name: str) -> dict:
    from urllib.parse import quote
    return _proxy_delete(f"/mcp/servers/{quote(name)}")


def list_skills() -> dict:
    return _proxy_get("/skills")


def install_skill(file_bytes: bytes, filename: str, name: str | None = None) -> dict:
    """Install a skill ZIP via the gateway's multipart endpoint."""
    from urllib.parse import quote
    params = f"?name={quote(name)}" if name else ""
    try:
        with httpx.Client(timeout=60.0) as client:
            files = {"file": (filename, file_bytes, "application/zip")}
            resp = client.post(
                _gateway_url(f"/skills/install{params}"),
                files=files,
                headers={k: v for k, v in _gateway_headers().items() if k != "Content-Type"},
            )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"agent-gateway {resp.status_code}: {resp.text[:300]}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"agent-gateway 不可达: {e}") from e


def get_skill_detail(name: str) -> dict:
    from urllib.parse import quote
    return _proxy_get(f"/skills/{quote(name)}")


def delete_skill(name: str) -> dict:
    from urllib.parse import quote
    return _proxy_delete(f"/skills/{quote(name)}")


__all__ = [
    "get_model_config",
    "save_model_config",
    "test_model_endpoint",
    "list_mcp_servers",
    "save_mcp_servers",
    "test_mcp_server",
    "delete_mcp_server",
    "list_skills",
    "install_skill",
    "get_skill_detail",
    "delete_skill",
    "MODEL_CONFIG_FIELDS",
    "RETRIEVAL_CONFIG_FIELDS",
    "get_retrieval_config",
    "save_retrieval_config",
    "retrieval_config_dict",
]


# ---------------------------------------------------------------------------
# 平台运行参数（modo_dim, dim_group=PLATFORM_CONFIG）
#
# wiki 构建执行方式（直跑 / agent 编排 / 外部网关）与 agent 编排回合上限等
# 平台级运行参数，页面上可改、即时生效（worker 侧读取带 15s TTL 缓存）。
# 优先级：DB 参数 > 容器 env > 代码默认值。
# ---------------------------------------------------------------------------

PLATFORM_CONFIG_GROUP = "PLATFORM_CONFIG"

# dim_code -> (label, 类型, 默认值, 选项, 说明)
PLATFORM_CONFIG_FIELDS: dict[str, tuple[str, str, str, list[str], str]] = {
    "WIKI_BUILD_MODE": (
        "wiki 构建模式",
        "enum",
        "direct",
        ["direct", "agent", "gateway"],
        "direct=直跑（worker 直接执行技能脚本，推荐，速度与稳定性最好）｜"
        "agent=内联 agent 编排（LLM 逐步决策工具调用）｜"
        "gateway=外部 agent-gateway 网关（回退路径）",
    ),
    "AGENT_MAX_TURNS": (
        "agent 编排最大回合数",
        "int",
        "60",
        [],
        "仅 agent 模式生效：单次构建任务的 LLM 回合上限"
        "（openai-agents max_turns），超限报 MaxTurnsExceeded；直跑模式无此限制。"
        "取值 1-1000",
    ),
    "AGENT_SKILL_SCRIPT_TIMEOUT": (
        "agent 技能脚本超时(秒)",
        "int",
        "1800",
        [],
        "agent 模式下单次技能脚本/命令的执行超时，默认 1800 秒（30 分钟）；"
        "wiki 构建脚本较长时可放宽。取值 60-86400",
    ),
    "AGENT_LLM_MAX_RETRIES": (
        "agent LLM 调用重试次数",
        "int",
        "5",
        [],
        "agent 模式下 OpenAI SDK 对 429/5xx 的自动重试次数（InferAI 限流时兜底）；"
        "过大会拉长失败等待。取值 0-10",
    ),
    "WIKI_DIRECT_TIMEOUT": (
        "wiki 直跑总超时(秒)",
        "int",
        "10800",
        [],
        "直跑模式下单篇文档构建的整体超时，超时杀进程组并标记失败；"
        "长文档/大模型限流时需要放宽。取值 600-86400",
    ),
}


# int 型参数的取值范围（保存校验）
PLATFORM_INT_RANGES: dict[str, tuple[int, int]] = {
    "AGENT_MAX_TURNS": (1, 1000),
    "AGENT_SKILL_SCRIPT_TIMEOUT": (60, 86400),
    "AGENT_LLM_MAX_RETRIES": (0, 10),
    "WIKI_DIRECT_TIMEOUT": (600, 86400),
}


# 参数 → 容器 env 名（DB 未配置时回落到 env，再回落默认值）
PLATFORM_ENV_NAMES: dict[str, str] = {
    "WIKI_BUILD_MODE": "WIKI_AGENT_MODE",
    "AGENT_MAX_TURNS": "WORKER_AGENT_MAX_TURNS",
    "AGENT_SKILL_SCRIPT_TIMEOUT": "WORKER_AGENT_SKILL_SCRIPT_TIMEOUT_S",
    "AGENT_LLM_MAX_RETRIES": "LLM_MAX_RETRIES",
    "WIKI_DIRECT_TIMEOUT": "WIKI_DIRECT_TIMEOUT",
}


def get_platform_config(db: Session) -> dict:
    """平台运行参数列表（DB 值 > env 现值 > 默认值）。"""
    env = get_settings()
    db_map = _dim_map(db, list(PLATFORM_CONFIG_FIELDS), group=PLATFORM_CONFIG_GROUP)
    out = []
    for code, (label, vtype, default, options, desc) in PLATFORM_CONFIG_FIELDS.items():
        value = db_map.get(code, "")
        source = "db" if value else "default"
        if not value:
            # env 优先于代码默认值（容器 env 可能是部署方显式设置）
            env_name = PLATFORM_ENV_NAMES.get(code, "")
            raw = str(getattr(env, env_name, "") or "").strip()
            if raw:
                value = "direct" if (code == "WIKI_BUILD_MODE" and raw.lower() == "inline") else raw
                source = "env"
            else:
                value = default
        out.append({
            "code": code,
            "label": label,
            "value_type": vtype,
            "value": value,
            "default": default,
            "options": options,
            "description": desc,
            "source": source,
        })
    return {"items": out}


def validate_platform_value(code: str, value: str | None) -> str:
    """校验单个平台运行参数值（枚举白名单 / int 范围），返回标准化值。

    非法值 raise ValueError（供 dims 通用 CRUD 与 save_platform_config 复用，
    API 层统一转 400）。
    """
    code = normalize_value(code)
    spec = PLATFORM_CONFIG_FIELDS.get(code)
    if not spec:
        # 非平台参数：不强制校验（dims 通用参数可自由定义）
        return normalize_value(str(value if value is not None else ""))
    label, vtype, default, options, desc = spec
    v = normalize_value(str(value if value is not None else ""))
    if code == "WIKI_BUILD_MODE":
        v = "direct" if v.lower() == "inline" else v.lower()
        if v not in options:
            raise ValueError(f"{label} 取值非法：{v}（可选 {options}）")
        return v
    try:
        n = int(v)
    except ValueError:
        raise ValueError(f"{label} 必须是整数") from None
    lo, hi = PLATFORM_INT_RANGES.get(code, (1, 1000))
    if n < lo or n > hi:
        raise ValueError(f"{label} 取值范围 {lo}-{hi}")
    return str(n)


def save_platform_config(db: Session, items: list[dict]) -> dict:
    """Upsert 平台运行参数到 modo_dim（校验类型/枚举）。"""
    saved: list[str] = []
    for item in items:
        code = normalize_value(str(item.get("code", "")))
        spec = PLATFORM_CONFIG_FIELDS.get(code)
        if not spec:
            continue
        label, vtype, default, options, desc = spec
        value = validate_platform_value(code, item.get("value"))
        existing = (
            db.execute(
                select(Dim).where(
                    Dim.dim_group == PLATFORM_CONFIG_GROUP, Dim.dim_code == code
                )
            )
            .scalars()
            .first()
        )
        if existing:
            existing.dim_value = value
            existing.state = "1"
        else:
            db.add(
                Dim(
                    id=uuid.uuid4().hex,
                    dim_code=code,
                    dim_group=PLATFORM_CONFIG_GROUP,
                    dim_value=value,
                    dim_desc=f"{label}｜{desc}",
                    seq=len(saved),
                    state="1",
                )
            )
        saved.append(code)
    db.commit()
    return {"saved": saved, "count": len(saved)}


# ---------------------------------------------------------------------------
# 全局检索参数（modo_dim, dim_group=RETRIEVAL_CONFIG）
#
# 对齐 WeKnora 租户级 RetrievalSettings：top_k / threshold / RRF / 双路权重 /
# 向量·关键词·wiki 开关。页面可改、检索即时生效（config_from_kb 读此组）。
# 优先级：KB 策略 > 全局参数 > overrides（config_from_kb 内实现）。
# ---------------------------------------------------------------------------

RETRIEVAL_CONFIG_GROUP = "RETRIEVAL_CONFIG"

# dim_code -> (label, 类型, 默认值, [min,max,step] 或 [], 说明)
RETRIEVAL_CONFIG_FIELDS: dict[str, tuple[str, str, str, list[str], str]] = {
    "TOP_K": (
        "向量召回条数 top_k",
        "int",
        "10",
        ["1", "100", "1"],
        "每次检索从向量库召回的最大条数（WeKnora 默认 10）",
    ),
    "THRESHOLD": (
        "相似度阈值 threshold",
        "float",
        "0.2",
        ["0.0", "1.0", "0.05"],
        "相似度低于该值的分块不进入回答（0-1）",
    ),
    "RRF_K": (
        "RRF 融合常数 k",
        "int",
        "60",
        ["1", "1000", "1"],
        "向量/关键词混合排序的 Reciprocal Rank Fusion 常数",
    ),
    "VECTOR_WEIGHT": (
        "向量路权重",
        "float",
        "0.7",
        ["0.0", "1.0", "0.05"],
        "RRF 融合时向量排名的权重（与关键词权重之和建议=1）",
    ),
    "KEYWORD_WEIGHT": (
        "关键词路权重",
        "float",
        "0.3",
        ["0.0", "1.0", "0.05"],
        "RRF 融合时关键词排名的权重（与向量权重之和建议=1）",
    ),
    "VECTOR_ENABLED": (
        "启用向量检索",
        "bool",
        "true",
        [],
        "关闭后只走关键词与 wiki 检索",
    ),
    "KEYWORD_ENABLED": (
        "启用关键词检索",
        "bool",
        "true",
        [],
        "关闭后只走向量与 wiki 检索",
    ),
    "WIKI_ENABLED": (
        "启用 wiki 检索",
        "bool",
        "false",
        [],
        "是否把 wiki 页面纳入检索（新建知识库默认关闭）",
    ),
}


def get_retrieval_config(db: Session) -> dict:
    """全局检索参数（DB 值 > 默认值），返回字段元数据 + 当前值。"""
    db_map = _dim_map(db, list(RETRIEVAL_CONFIG_FIELDS), group=RETRIEVAL_CONFIG_GROUP)
    out = []
    for code, (label, vtype, default, range_, desc) in RETRIEVAL_CONFIG_FIELDS.items():
        value = db_map.get(code, "")
        source = "db" if value else "default"
        if not value:
            value = default
        out.append({
            "code": code,
            "label": label,
            "value_type": vtype,
            "value": value,
            "default": default,
            "range": range_,
            "description": desc,
            "source": source,
        })
    return {"items": out}


def _coerce_retrieval_value(code: str, spec: tuple, value: str) -> str:
    """类型化校验：int / float / bool（非法抛 HTTPException 400）。"""
    label, vtype, default, range_, desc = spec
    raw = str(value).strip()
    if vtype == "int":
        try:
            n = int(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label} 必须是整数") from None
        lo, hi, _step = (int(x) for x in range_)
        if n < lo or n > hi:
            raise HTTPException(status_code=400, detail=f"{label} 取值范围 {lo}-{hi}") from None
        return str(n)
    if vtype == "float":
        try:
            f = float(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label} 必须是数字") from None
        lo, hi, _step = (float(x) for x in range_)
        if f < lo or f > hi:
            raise HTTPException(status_code=400, detail=f"{label} 取值范围 {lo}-{hi}") from None
        return f"{f:.4f}".rstrip("0").rstrip(".")
    if vtype == "bool":
        low = raw.lower()
        if low in ("true", "1", "yes", "on"):
            return "true"
        if low in ("false", "0", "no", "off"):
            return "false"
        raise HTTPException(status_code=400, detail=f"{label} 必须是 true/false") from None
    return raw


def save_retrieval_config(db: Session, items: list[dict]) -> dict:
    """Upsert 全局检索参数到 modo_dim（RETRIEVAL_CONFIG 组，类型校验）。"""
    saved: list[str] = []
    for item in items:
        code = normalize_value(str(item.get("code", "")))
        spec = RETRIEVAL_CONFIG_FIELDS.get(code)
        if not spec:
            continue
        raw = item.get("value")
        value = _coerce_retrieval_value(code, spec, "" if raw is None else str(raw))
        existing = (
            db.execute(
                select(Dim).where(
                    Dim.dim_group == RETRIEVAL_CONFIG_GROUP, Dim.dim_code == code
                )
            )
            .scalars()
            .first()
        )
        label, vtype, default, range_, desc = spec
        if existing:
            existing.dim_value = value
            existing.state = "1"
        else:
            db.add(
                Dim(
                    id=uuid.uuid4().hex,
                    dim_code=code,
                    dim_group=RETRIEVAL_CONFIG_GROUP,
                    dim_value=value,
                    dim_desc=f"{label}｜{desc}",
                    seq=len(saved),
                    state="1",
                )
            )
        saved.append(code)
    db.commit()
    return {"saved": saved, "count": len(saved)}


def retrieval_config_dict(db: Session) -> dict:
    """读取为纯配置 dict（检索服务用）：code -> 类型化值。"""
    out: dict = {}
    for item in get_retrieval_config(db)["items"]:
        v = item["value"]
        if item["value_type"] == "int":
            out[item["code"].lower()] = int(v)
        elif item["value_type"] == "float":
            out[item["code"].lower()] = float(v)
        elif item["value_type"] == "bool":
            out[item["code"].lower()] = v == "true"
        else:
            out[item["code"].lower()] = v
    return out
