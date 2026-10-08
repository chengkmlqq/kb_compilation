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


class CypherRequest(BaseModel):
    """Popoto 查询代理请求（只读）。"""

    statement: str
    parameters: dict = {}


# 只读白名单：仅允许查询类语句（Popoto 只读数据浏览/查询构建）
_CYPHER_READ_PREFIXES = ("MATCH", "OPTIONAL MATCH", "WITH", "RETURN", "UNWIND", "CALL db")
_CYPHER_WRITE_KEYWORDS = ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DROP", "LOAD CSV", "CREATE CONSTRAINT", "CREATE INDEX")


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


@router.post("/cypher")
def run_cypher(req: CypherRequest, _user: str = Depends(_require_user)) -> dict:
    """只读 Cypher 代理（Popoto.js 数据通道）。

    模拟 Neo4j HTTP transaction 响应格式（results/columns/data/meta），
    Popoto 直接将该端点配置为数据源即可。仅放行查询类语句（只读浏览
    与查询构建），写语句一律 400 拒绝。
    """
    stmt = req.statement.strip()
    if not stmt:
        raise HTTPException(status_code=400, detail="empty statement")
    upper = stmt.upper()
    if not upper.startswith(_CYPHER_READ_PREFIXES):
        raise HTTPException(status_code=400, detail="只允许查询语句（MATCH/OPTIONAL MATCH/WITH/RETURN/UNWIND）")
    for kw in _CYPHER_WRITE_KEYWORDS:
        if kw in upper:
            raise HTTPException(status_code=400, detail=f"不允许写语句: {kw.strip()}")

    reader = _reader_or_503()
    rows = reader._run(stmt, req.parameters or {})

    # 构造 Neo4j transaction 兼容响应
    columns: list[str] = []
    data: list[dict] = []
    for rec in rows:
        if isinstance(rec, dict):
            if not columns:
                columns = list(rec.keys())
            row = [_row_value(rec.get(c)) for c in columns]
            meta = [_meta_for(rec.get(c)) for c in columns]
            data.append({"row": row, "meta": meta})
    return {"results": [{"columns": columns, "data": data}], "errors": []}


def _row_value(value: object) -> object:
    """neo4j 对象转可 JSON 序列化值（Popoto 走 meta 拿节点标识，row 仅需可序列化）。"""
    import neo4j

    if isinstance(value, neo4j.graph.Node):
        return {"id": str(value.element_id), "labels": list(value.labels), "properties": dict(value)}
    if isinstance(value, neo4j.graph.Relationship):
        return {
            "id": str(value.element_id),
            "type": value.type,
            "start": str(value.start_node.element_id),
            "end": str(value.end_node.element_id),
            "properties": dict(value),
        }
    if isinstance(value, neo4j.graph.Path):
        return {
            "nodes": [str(n.element_id) for n in value.nodes],
            "relationships": [str(r.element_id) for r in value.relationships],
        }
    return value


def _meta_for(value: object) -> dict:
    """推断 Neo4j 值 meta（node/relationship 标识），Popoto 依赖 meta 区分。"""
    import neo4j

    if isinstance(value, neo4j.graph.Node):
        return {"id": str(value.element_id), "type": "node", "labels": list(value.labels)}
    if isinstance(value, neo4j.graph.Relationship):
        return {"id": str(value.element_id), "type": "relationship", "labels": [value.type]}
    if isinstance(value, neo4j.graph.Path):
        return {"id": str(value.element_id), "type": "path", "labels": []}
    return {"id": None, "type": "value", "labels": []}


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