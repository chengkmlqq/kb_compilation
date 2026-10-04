"""Scoped WebSearch provider registry service + search executor.

kb_compilation 是联网搜索提供方配置的唯一数据源（创建/查看/修改/删除）；
search 执行器按 provider_type 调对应搜索 API：
- tavily: POST https://api.tavily.com/search   (api_key 放 JSON body)
- serper: POST https://google.serper.dev/search (X-API-KEY 头)
- bing:   GET  https://api.bing.microsoft.com/v7.0/search (Ocp-Apim-Subscription-Key 头)
- exa:    POST https://api.exa.ai/search        (x-api-key 头)
- generic: POST base_url（远端约定 JSON 请求/响应，兜底自定义搜索引擎）

统一返回 [{title, url, snippet}]；失败抛 ValueError（中文 message）。
API Key 落库前 AES 加密（api.lib.crypto.aes_encrypt），读接口脱敏，
保存以 '****' 开头的值 = 保持原值不变。
"""

from __future__ import annotations

import json
import uuid
from typing import Callable

import httpx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_decrypt, aes_encrypt
from api.models.websearch import WebSearchProvider
from api.services.runtime_config import mask_sensitive_value
from api.services.scope import ResourceRow, can_manage, can_see, validate_scope_request, visible_clauses

# 支持的提供方类型
PROVIDER_TYPES = ("tavily", "serper", "bing", "exa", "generic")

# 官方端点（- 表示无官方端点，必须给 base_url）
DEFAULT_ENDPOINTS: dict[str, str] = {
    "tavily": "https://api.tavily.com/search",
    "serper": "https://google.serper.dev/search",
    "bing": "https://api.bing.microsoft.com/v7.0/search",
    "exa": "https://api.exa.ai/search",
    "generic": "",
}

# API Key 掩码前缀：保存端传入该前缀开头 = 保留原值
MASK_PREFIX = "****"

_SEARCH_TIMEOUT = 20.0


def encrypt_api_key(value: str) -> str:
    """加密 API Key（空值直接返回空串）。"""
    if not value:
        return ""
    return aes_encrypt(value)


def decrypt_api_key(value: str | None) -> str:
    """解密 API Key；解不开返回原文（兼容历史明文存量）。"""
    if not value:
        return ""
    plain = aes_decrypt(value)
    return plain if plain else value


def websearch_to_dict(p: WebSearchProvider, *, with_secrets: bool = False) -> dict:
    """API 形状。默认 api_key 脱敏；仅执行器内部解密使用真实 Key。"""
    return {
        "id": p.id,
        "scope": p.scope,
        "name": p.name,
        "description": p.description or "",
        "provider_type": p.provider_type,
        "api_key": (
            decrypt_api_key(p.api_key)
            if with_secrets
            else (mask_sensitive_value(decrypt_api_key(p.api_key)) if p.api_key else "")
        ),
        "base_url": p.base_url or "",
        "extra_config": p.extra_config or {},
        "owner_user_id": p.owner_user_id or "",
        "owner_team_name": p.owner_team_name or "",
        "enabled": bool(p.enabled),
        "state": p.state,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def list_websearch_providers(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    scope: str | None = None,
) -> list[dict]:
    stmt = select(WebSearchProvider).where(
        WebSearchProvider.state == "1",
        *visible_clauses(caller_user_id, caller_team_name, is_sys_admin, WebSearchProvider),
    )
    if scope:
        stmt = stmt.where(WebSearchProvider.scope == scope)
    rows = db.execute(stmt.order_by(WebSearchProvider.scope, WebSearchProvider.name)).scalars().all()
    return [websearch_to_dict(p) for p in rows]


def get_websearch_provider(db: Session, provider_id: str) -> WebSearchProvider | None:
    return (
        db.execute(
            select(WebSearchProvider).where(
                and_(WebSearchProvider.id == provider_id, WebSearchProvider.state == "1")
            )
        )
        .scalars()
        .first()
    )


def create_websearch_provider(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    payload: dict,
) -> dict:
    scope, owner_user_id, owner_team_name = validate_scope_request(
        str(payload.get("scope") or "personal"),
        caller_user_id=caller_user_id,
        caller_team_name=caller_team_name,
        is_sys_admin=is_sys_admin,
    )
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("提供方名称不能为空")
    provider_type = str(payload.get("provider_type") or "generic").strip().lower()
    if provider_type not in PROVIDER_TYPES:
        raise ValueError(f"不支持的搜索服务类型: {provider_type}")
    dup = db.execute(
        select(WebSearchProvider).where(
            and_(WebSearchProvider.name == name, WebSearchProvider.state == "1")
        )
    ).scalars().first()
    if dup:
        raise ValueError(f"提供方名称已存在: {name}")

    p = WebSearchProvider(
        id=uuid.uuid4().hex[:36],
        scope=scope,
        name=name,
        description=str(payload.get("description") or "").strip() or None,
        provider_type=provider_type,
        api_key=encrypt_api_key(str(payload.get("api_key") or "")),
        base_url=str(payload.get("base_url") or "").strip() or None,
        extra_config=payload.get("extra_config") or None,
        owner_user_id=owner_user_id or None,
        owner_team_name=owner_team_name or None,
        enabled=bool(payload.get("enabled", True)),
        state="1",
    )
    db.add(p)
    db.commit()
    return websearch_to_dict(p)


def update_websearch_provider(
    db: Session,
    provider_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    payload: dict,
) -> dict:
    p = get_websearch_provider(db, provider_id)
    if not p:
        raise KeyError("搜索提供方不存在")
    if not can_manage(ResourceRow.from_obj(p), caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权修改该提供方")

    if "name" in payload:
        v = str(payload.get("name") or "").strip()
        if not v:
            raise ValueError("提供方名称不能为空")
        p.name = v
    if "description" in payload:
        p.description = str(payload.get("description") or "").strip() or None
    if "provider_type" in payload:
        v = str(payload.get("provider_type") or "generic").strip().lower()
        if v not in PROVIDER_TYPES:
            raise ValueError(f"不支持的搜索服务类型: {v}")
        p.provider_type = v
    if "base_url" in payload:
        p.base_url = str(payload.get("base_url") or "").strip() or None
    if "extra_config" in payload:
        p.extra_config = payload.get("extra_config") or None
    if "enabled" in payload:
        p.enabled = bool(payload.get("enabled", True))
    if "api_key" in payload:
        v = str(payload.get("api_key") or "").strip()
        if v.startswith(MASK_PREFIX):
            pass  # 保持原值
        elif v:
            p.api_key = encrypt_api_key(v)
        else:
            p.api_key = None
    db.commit()
    return websearch_to_dict(p)


def delete_websearch_provider(
    db: Session,
    provider_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> None:
    p = get_websearch_provider(db, provider_id)
    if not p:
        raise KeyError("搜索提供方不存在")
    if not can_manage(ResourceRow.from_obj(p), caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权删除该提供方")
    p.state = "0"
    db.commit()


def websearch_provider_visible(
    p: WebSearchProvider,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> bool:
    return can_see(ResourceRow.from_obj(p), caller_user_id, caller_team_name, is_sys_admin)


# ---------------------------------------------------------------------------
# 搜索执行器
# ---------------------------------------------------------------------------

def _endpoint_for(p: WebSearchProvider) -> str:
    """解析请求端点：base_url 优先，其次官方端点。"""
    if p.base_url and p.base_url.strip():
        return p.base_url.strip()
    default = DEFAULT_ENDPOINTS.get(p.provider_type or "", "")
    if not default:
        raise ValueError("generic 类型必须配置 base_url 端点")
    return default


def _post_json(
    url: str, *, json_body: dict | None = None, headers: dict | None = None, timeout: float = _SEARCH_TIMEOUT
) -> httpx.Response:
    """POST JSON，返回原始响应（错误判定交给 _read_payload）。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=json_body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise ValueError(f"搜索请求失败（网络错误）: {e}") from e


def _get_json(
    url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = _SEARCH_TIMEOUT
) -> httpx.Response:
    """GET JSON，返回原始响应（错误判定交给 _read_payload）。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.get(url, params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise ValueError(f"搜索请求失败（网络错误）: {e}") from e


def _read_payload(resp: httpx.Response) -> dict:
    """统一校验 HTTP 响应并解析 JSON；非 2xx / 非 JSON 时给出中文报错。"""
    text = resp.text or ""
    if resp.status_code >= 400:
        raise ValueError(f"搜索服务返回错误 {resp.status_code}: {text[:200]}")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        raise ValueError(f"搜索服务返回非 JSON 响应: {text[:200]}") from None
    if not isinstance(data, dict):
        raise ValueError("搜索服务返回格式异常（期望 JSON 对象）")
    return data


def _normalize_results(raw: list) -> list[dict]:
    """把各家搜索 API 的结果列表归一为 [{title, url, snippet}]。

    兼容字段名：title/name、url/link、snippet/content/description/text。
    """
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        snippet = str(
            item.get("snippet")
            or item.get("content")
            or item.get("description")
            or item.get("text")
            or item.get("summary")
            or ""
        ).strip()
        if not url and not title:
            continue
        out.append({"title": title, "url": url, "snippet": snippet})
    return out


def search(
    db: Session,
    provider_id: str,
    query: str,
    max_results: int = 5,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> list[dict]:
    """执行联网搜索，返回统一 [{title, url, snippet}]。

    先做可见性校验（与列表/详情同一套权限），再按 provider_type 分发。
    失败统一抛 ValueError（中文 message）。
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("搜索关键词不能为空")
    max_results = max(1, min(int(max_results or 5), 20))

    p = get_websearch_provider(db, provider_id)
    if not p:
        raise KeyError("搜索提供方不存在")
    if not websearch_provider_visible(
        p, caller_user_id=caller_user_id, caller_team_name=caller_team_name, is_sys_admin=is_sys_admin
    ):
        raise PermissionError("无权使用该搜索提供方")
    if not p.enabled:
        raise ValueError("该搜索提供方已停用")

    api_key = decrypt_api_key(p.api_key)
    provider_type = p.provider_type or "generic"
    endpoint = _endpoint_for(p)
    extra = p.extra_config or {}

    results: list[dict] = []
    if provider_type == "tavily":
        # Tavily：api_key 放 JSON body，返回 results[]（title/url/content）
        data = _read_payload(
            _post_json(
                endpoint,
                json_body={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": extra.get("search_depth") or "basic",
                },
            )
        )
        results = _normalize_results(data.get("results") or [])
    elif provider_type == "serper":
        # Serper：X-API-KEY 头，返回 organic[]（title/link/snippet）
        data = _read_payload(
            _post_json(
                endpoint,
                json_body={"q": query, "num": max_results},
                headers={"X-API-KEY": api_key} if api_key else {},
            )
        )
        results = _normalize_results(data.get("organic") or data.get("news") or data.get("images") or [])
    elif provider_type == "bing":
        # Bing Web Search：GET + Ocp-Apim-Subscription-Key 头，返回 webPages.value[]
        data = _read_payload(
            _get_json(
                endpoint,
                params={"q": query, "count": max_results, **(extra.get("params") or {})},
                headers={"Ocp-Apim-Subscription-Key": api_key} if api_key else {},
            )
        )
        pages = (data.get("webPages") or {}).get("value") or []
        results = _normalize_results(pages)
    elif provider_type == "exa":
        # Exa：x-api-key 头，返回 results[]（title/url/text）
        data = _read_payload(
            _post_json(
                endpoint,
                json_body={"query": query, "numResults": max_results, **(extra.get("body") or {})},
                headers={"x-api-key": api_key} if api_key else {},
            )
        )
        results = _normalize_results(data.get("results") or [])
    else:
        # generic：POST base_url，带 Authorization Bearer；响应兼容
        # {"results":[...]} / {"items":[...]} / 裸数组 三种形态
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        data = _read_payload(
            _post_json(
                endpoint,
                json_body={"query": query, "max_results": max_results, **(extra.get("body") or {})},
                headers={**headers, **(extra.get("headers") or {})},
            )
        )
        if isinstance(data.get("results"), list):
            raw = data["results"]
        elif isinstance(data.get("items"), list):
            raw = data["items"]
        elif isinstance(data.get("data"), list):
            raw = data["data"]
        else:
            raise ValueError("generic 搜索服务返回格式无法识别（期望 results/items/data 列表）")
        results = _normalize_results(raw)

    if not results:
        raise ValueError("搜索无结果")
    return results[:max_results]


# ---------------------------------------------------------------------------
# Agent 会话联网搜索工具注入（P1 消费点，模式对齐 P2 技能白名单注入：
# tools + executor 传给 chat.answer_question 的有界工具循环）。
# ---------------------------------------------------------------------------
WEB_SEARCH_TOOL = "web_search"


def build_websearch_tool(max_results: int) -> list[dict]:
    """run_web_search function-calling 负载（OpenAI 兼容）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": WEB_SEARCH_TOOL,
                "description": (
                    "联网搜索：给定 query 返回网页结果（标题/URL/摘要）。"
                    "当问题需要实时信息、或知识库检索未覆盖时使用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "max_results": {
                            "type": "integer",
                            "description": f"返回条数（1-{20}，默认 {max_results}）",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    ]


def agent_websearch_tool_context(
    db: Session,
    agent,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> tuple[list[dict], "Callable[[dict], str] | None"]:
    """按 AgentConfig.web_search_enabled 装配 web_search 工具上下文。

    返回 (tools, executor)：未启用 / 无可见 provider 时返回 ([], None)，问答链路
    不受影响。provider 解析：显式 web_search_provider_id（带可见性校验）；
    未指定时取调用方可见的第一个 system 级 provider（无则跳过注入）。
    """
    from api.services.agents import config_from_dict

    cfg = config_from_dict(agent.config or {})
    if not cfg.web_search_enabled:
        return [], None

    provider_id = str(cfg.web_search_provider_id or "").strip()
    provider = None
    if provider_id:
        provider = get_websearch_provider(db, provider_id)
        if provider and (
            provider.state != "1"
            or not websearch_provider_visible(
                provider,
                caller_user_id=caller_user_id,
                caller_team_name=caller_team_name,
                is_sys_admin=is_sys_admin,
            )
        ):
            provider = None
    if provider is None:
        visible = list_websearch_providers(
            db,
            caller_user_id=caller_user_id,
            caller_team_name=caller_team_name,
            is_sys_admin=is_sys_admin,
        )
        # list_* 返回 dict 项（websearch_to_dict 形状：id/scope/name/...）
        pick = next((p for p in visible if p.get("scope") == "system"), visible[0] if visible else None)
        if pick is None:
            return [], None
        provider_id = str(pick.get("id") or "")
        if not provider_id:
            return [], None

    max_results = max(1, min(int(cfg.web_search_max_results or 5), 20))
    tools = build_websearch_tool(max_results)

    def executor(tool_call: dict) -> str:
        name = str(tool_call.get("name") or "")
        if name != WEB_SEARCH_TOOL:
            return f"未授权的工具调用: {name!r}"
        try:
            args = json.loads(str(tool_call.get("arguments") or "{}"))
        except (TypeError, json.JSONDecodeError):
            return "工具参数解析失败：arguments 不是合法 JSON"
        query = str(args.get("query") or "").strip()
        if not query:
            return "web_search 缺少 query 参数"
        try:
            results = search(
                db,
                provider_id,
                query,
                max_results=int(args.get("max_results") or max_results),
                caller_user_id=caller_user_id,
                caller_team_name=caller_team_name,
                is_sys_admin=is_sys_admin,
            )
        except Exception as exc:  # noqa: BLE001 — 工具失败回传文本，不炸 SSE
            return f"联网搜索失败: {exc}"
        if not results:
            return "（联网搜索无结果）"
        lines = [
            f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
            for i, r in enumerate(results)
        ]
        return "\n".join(lines)

    return tools, executor


__all__ = [
    "PROVIDER_TYPES",
    "DEFAULT_ENDPOINTS",
    "list_websearch_providers",
    "get_websearch_provider",
    "create_websearch_provider",
    "update_websearch_provider",
    "delete_websearch_provider",
    "websearch_provider_visible",
    "websearch_to_dict",
    "search",
]