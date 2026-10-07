"""Neo4j 知识图谱查询 API。

与写入侧（worker KbGraphBuildTask → api/services/graph.py Neo4jGraphStore）
配套，提供读取接口供前端图谱可视化：

- GET  /api/v1/graph/health                 Neo4j 连通性（enabled/available）
- GET  /api/v1/graph/engines                图谱引擎信息（展示用）
- GET  /api/v1/kbs/{kb_id}/graph            全库图（mode=overview）
- GET  /api/v1/kbs/{kb_id}/graph/stats      图规模统计
- POST /api/v1/kbs/{kb_id}/graph/search     节点名搜索（诱导子图）
- GET  /api/v1/kbs/{kb_id}/graph/ego        某节点的邻域下钻

注：wiki 链接图（wiki_page+wiki_link，走 pg）是另一套图谱，见
api/routers/kbs.py 的 /kbs/{kb_id}/wiki/graph，不依赖 Neo4j。本模块读的是
Neo4j 中的实体/关系知识图谱。
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.graph_query import get_graph_reader
from api.services.identity import decode_identity_cookie
from api.services.kb_admin import get_kb

router = APIRouter(tags=["graph"])


def _require_user(x_next_identity: str | None = Cookie(default=None, alias="x-next-identity")) -> str:
    """轻量登录校验：图谱接口只需登录态（数据按 kb_id 隔离）。"""
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _reader_or_503():
    reader = get_graph_reader()
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail="Neo4j 未配置（缺少 NEO4J_URI / NEO4J_PASSWORD）",
        )
    return reader


@router.get("/graph/health")
def graph_health() -> dict:
    reader = get_graph_reader()
    if reader is None:
        return {"success": True, "data": {"enabled": False, "available": False}}
    # GraphReader 无 health() 方法（原代码 AttributeError 致 500）——
    # 用最小查询 RETURN 1 探测连通性，失败报告不可用而非抛错。
    try:
        rows = reader._run("RETURN 1 AS ok", {})
        return {"success": True, "data": {"enabled": True, "available": rows is not None}}
    except Exception as exc:  # noqa: BLE001 - 探活端点不应自身抛 500（Neo4j 未部署/不可达时报告不可用）
        return {"success": True, "data": {"enabled": True, "available": False, "error": str(exc)[:200]}}


@router.get("/graph/engines")
def graph_engines() -> dict:
    reader = get_graph_reader()
    return {
        "success": True,
        "data": [
            {
                "name": "neo4j",
                "display_name": "Neo4j 知识图谱",
                "description": "实体/关系知识图谱存储与检索",
                "available": reader is not None,
                "reason": None if reader is not None else "Neo4j 未配置",
            }
        ],
    }


def _require_kb(db: Session, kb_id: str) -> None:
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")


@router.get("/kbs/{kb_id}/graph")
def get_kb_graph(
    kb_id: str,
    limit: int = 200,
    _: str = Depends(_require_user),
    db: Session = Depends(get_db),
) -> dict:
    """全库实体/关系图（overview）。"""
    _require_kb(db, kb_id)
    reader = _reader_or_503()
    data = reader.overview(kb_id, limit=max(1, min(limit, 1000)))
    return {"success": True, "data": data}


@router.get("/kbs/{kb_id}/graph/stats")
def get_kb_graph_stats(
    kb_id: str,
    _: str = Depends(_require_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_kb(db, kb_id)
    reader = _reader_or_503()
    return {"success": True, "data": reader.stats(kb_id)}


@router.get("/kbs/{kb_id}/graph/ego")
def get_kb_graph_ego(
    kb_id: str,
    center: str,
    depth: int = 1,
    limit: int = 200,
    _: str = Depends(_require_user),
    db: Session = Depends(get_db),
) -> dict:
    """以 center 节点为中心的 depth 跳邻域。"""
    _require_kb(db, kb_id)
    if not center:
        raise HTTPException(status_code=400, detail="center 不能为空")
    reader = _reader_or_503()
    data = reader.ego(kb_id, center, depth=depth, limit=max(1, min(limit, 1000)))
    return {"success": True, "data": data}


class GraphSearchRequest(BaseModel):
    q: str
    limit: int = 100


@router.post("/kbs/{kb_id}/graph/search")
def search_kb_graph(
    kb_id: str,
    req: GraphSearchRequest,
    _: str = Depends(_require_user),
    db: Session = Depends(get_db),
) -> dict:
    """按节点名片段搜索，返回诱导子图。"""
    _require_kb(db, kb_id)
    if not req.q.strip():
        raise HTTPException(status_code=400, detail="q 不能为空")
    reader = _reader_or_503()
    data = reader.search(kb_id, req.q, limit=max(1, min(req.limit, 1000)))
    return {"success": True, "data": data}