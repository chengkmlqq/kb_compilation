"""WeKnora API 只读代理 —— 把 agent-gateway 技能构建的 wiki 数据暴露到本系统。

测试链路：kb_compilation Worker(KbAgentGatewayTask) → agent-gateway →
「监督管理制度文档转wiki」技能 → WeKnora wiki 页面（实体/长句/关键词/规则）。
本路由用 MASTER_API_KEY 只读访问 WeKnora（X-API-Key 头），前端即可浏览
这些外部构建的 wiki 页面/图谱/统计，验证测试产出。

端点全部透传 WeKnora 的 JSON 响应（成功 / 错误原样返回），本路由不做业务判断。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query

from api.config import get_settings
from api.services.identity import decode_identity_cookie

router = APIRouter(prefix="/weknora", tags=["weknora"])

_client = httpx.Client(timeout=30.0)


def _require_user_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _proxy(path: str, params: dict | None = None) -> dict:
    settings = get_settings()
    base = settings.WEKNORA_BASE_URL.rstrip("/")
    headers = {"X-API-Key": settings.WEKNORA_API_KEY} if settings.WEKNORA_API_KEY else {}
    try:
        resp = _client.get(f"{base}/api/v1/{path.lstrip('/')}", params=params, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WeKnora 不可达: {e}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:300])
    return resp.json()


@router.get("/kbs")
def list_kbs(_user_id: str = Depends(_require_user_id)) -> dict:
    """WeKnora 知识库列表（供前端选择器）。"""
    return _proxy("knowledge-bases")


@router.get("/kbs/{kb_id}/stats")
def kb_stats(kb_id: str, _user_id: str = Depends(_require_user_id)) -> dict:
    """wiki 统计（页数/类型分布/链接数）。"""
    return _proxy(f"knowledgebase/{kb_id}/wiki/stats")


@router.get("/kbs/{kb_id}/pages")
def kb_pages(
    kb_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user_id: str = Depends(_require_user_id),
) -> dict:
    """wiki 页面列表（时间倒序由 WeKnora 决定，可翻页）。"""
    return _proxy(
        f"knowledgebase/{kb_id}/wiki/pages",
        {"page": page, "page_size": page_size},
    )


@router.get("/kbs/{kb_id}/pages/{slug:path}")
def kb_page(kb_id: str, slug: str, _user_id: str = Depends(_require_user_id)) -> dict:
    """单页详情（含双向链接 in_links/out_links）。"""
    return _proxy(f"knowledgebase/{kb_id}/wiki/pages/{slug}")
