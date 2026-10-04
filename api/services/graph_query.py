"""Neo4j 知识图谱查询服务。

写入侧见同文件外的 `api/services/graph.py::Neo4jGraphStore`（worker
KbGraphBuildTask 调用）。本模块负责**读取**，供 api/routers/graph.py 的图谱
接口使用，对齐 WeKnora 的 neo4j repository（SearchNode / 全库图 / ego 邻域）。

图谱 schema（由 Neo4jGraphStore 写入，勿随意改动字段名）：
  节点  (:Entity {name, kb_id, description, entity_type, chunks[]})
  关系  (:Entity)-[:RELATED_TO {kb_id, type, attributes}]->(:Entity)
其中关系语义类型存在**关系属性** `type`（如「属于/负责/位于」），
Cypher 关系类型统一为 RELATED_TO —— 与 WeKnora 的 `rel.type` 约定一致。
"""

from __future__ import annotations

import logging
from typing import Any

from api.config import get_settings

logger = logging.getLogger(__name__)


class Neo4jGraphReader:
    """读取 Neo4j 知识图谱（overview / ego 邻域 / 节点搜索 / 统计）。"""

    def __init__(self, uri: str, user: str, password: str, database: str | None = None):
        self._uri = uri
        self._auth = (user, password)
        self._database = database

    # ---- low-level ----
    def _run(self, query: str, params: dict[str, Any]) -> list[dict]:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(self._uri, auth=self._auth)
        try:
            with driver.session(database=self._database) as session:
                result = session.run(query, **params)
                return [dict(record) for record in result]
        finally:
            driver.close()

    # ---- shape builders ----
    @staticmethod
    def _to_nodes(rows: list[dict]) -> list[dict]:
        nodes = []
        for r in rows:
            nodes.append(
                {
                    "name": r.get("name", ""),
                    "entity_type": r.get("entity_type", ""),
                    "description": r.get("description", ""),
                    "degree": r.get("degree", 0),
                    "chunks": r.get("chunks") or [],
                }
            )
        return nodes

    @staticmethod
    def _to_edges(rows: list[dict]) -> list[dict]:
        edges = []
        for r in rows:
            edges.append(
                {
                    "source": r.get("source", ""),
                    "target": r.get("target", ""),
                    # 关系语义类型（RELATED_TO 是 Cypher 类型，真实语义在 type 属性）
                    "type": r.get("rel_type", "RELATED_TO"),
                    "description": r.get("description", ""),
                    "strength": r.get("strength", ""),
                }
            )
        return edges

    # ---- queries ----
    def overview(self, kb_id: str, limit: int = 200) -> dict:
        """全库图：按度数取 TOP N 节点 + 它们之间的边。"""
        nodes = self._run(
            """
            MATCH (n:Entity {kb_id: $kb_id})
            OPTIONAL MATCH (n)-[r]-(m:Entity {kb_id: $kb_id})
            WITH n, count(DISTINCT m) AS degree
            RETURN n.name AS name, n.entity_type AS entity_type,
                   n.description AS description, n.chunks AS chunks, degree
            ORDER BY degree DESC, name
            LIMIT $limit
            """,
            {"kb_id": kb_id, "limit": limit},
        )
        edges = self._edges_among(kb_id, [n["name"] for n in nodes])
        return {"nodes": self._to_nodes(nodes), "edges": edges}

    def ego(self, kb_id: str, center: str, depth: int = 1, limit: int = 200) -> dict:
        """以 center 为中心取 depth 跳邻域（变长路径，无向）。"""
        depth = max(1, min(int(depth or 1), 3))
        nodes = self._run(
            """
            MATCH (c:Entity {kb_id: $kb_id, name: $center})
            MATCH p = (c)-[*1..%d]-(m:Entity {kb_id: $kb_id})
            WITH m, min(length(p)) AS hops
            RETURN m.name AS name, m.entity_type AS entity_type,
                   m.description AS description, m.chunks AS chunks, hops AS degree
            ORDER BY hops, degree
            LIMIT $limit
            """
            % depth,
            {"kb_id": kb_id, "center": center, "limit": limit},
        )
        names = [center] + [n["name"] for n in nodes]
        edges = self._edges_among(kb_id, names)
        return {
            "nodes": [{"name": center, "entity_type": "", "description": "",
                       "chunks": [], "degree": 0}] + self._to_nodes(nodes),
            "edges": edges,
        }

    def search(self, kb_id: str, query: str, limit: int = 100) -> dict:
        """按名称片段搜索节点，返回命中节点的诱导子图（节点+其间边）。"""
        terms = [t for t in query.replace("，", ",").replace(" ", ",").split(",") if t]
        if not terms:
            return {"nodes": [], "edges": []}
        nodes = self._run(
            """
            MATCH (n:Entity {kb_id: $kb_id})
            WHERE ANY(t IN $terms WHERE toLower(n.name) CONTAINS toLower(t))
            OPTIONAL MATCH (n)-[r]-(m:Entity {kb_id: $kb_id})
            WITH n, count(DISTINCT m) AS degree
            RETURN n.name AS name, n.entity_type AS entity_type,
                   n.description AS description, n.chunks AS chunks, degree
            ORDER BY degree DESC, name
            LIMIT $limit
            """,
            {"kb_id": kb_id, "terms": terms, "limit": limit},
        )
        edges = self._edges_among(kb_id, [n["name"] for n in nodes])
        return {"nodes": self._to_nodes(nodes), "edges": edges}

    def _edges_among(self, kb_id: str, names: list[str]) -> list[dict]:
        """取两端都在 names 集合内的边（诱导子图）。

        用有向匹配 `(a)-[r]->(b)`：写入侧始终按 source->target 建有向边，
        无向匹配会把同一条边正反各返回一次（导致图上出现重复边）。
        """
        if len(names) < 2:
            return []
        rows = self._run(
            """
            MATCH (a:Entity {kb_id: $kb_id})-[r:RELATED_TO]->(b:Entity {kb_id: $kb_id})
            WHERE a.name IN $names AND b.name IN $names
            RETURN a.name AS source, b.name AS target,
                   r.type AS rel_type, r.description AS description, r.strength AS strength
            """,
            {"kb_id": kb_id, "names": names},
        )
        return self._to_edges(rows)

    def stats(self, kb_id: str) -> dict:
        """图规模统计：节点数/关系数/实体类型分布。"""
        row = self._run(
            """
            MATCH (n:Entity {kb_id: $kb_id})
            OPTIONAL MATCH ()-[r:RELATED_TO {kb_id: $kb_id}]->()
            WITH count(DISTINCT n) AS nodes, count(DISTINCT r) AS edges
            RETURN nodes, edges
            """,
            {"kb_id": kb_id},
        )
        types = self._run(
            """
            MATCH (n:Entity {kb_id: $kb_id})
            RETURN coalesce(n.entity_type, '') AS t, count(*) AS c
            ORDER BY c DESC
            """,
            {"kb_id": kb_id},
        )
        base = row[0] if row else {}
        return {
            "nodes": base.get("nodes", 0),
            "edges": base.get("edges", 0),
            "entity_types": [{"type": t["t"], "count": t["c"]} for t in types],
        }

    def health(self) -> dict:
        """连通性探测（供 /api/v1/graph/health 与前端状态展示）。"""
        try:
            rows = self._run("RETURN 1 AS ok", {})
            return {"available": True, "ok": bool(rows and rows[0].get("ok") == 1)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("neo4j health check failed: %s", exc)
            return {"available": False, "error": str(exc)[:200]}


def get_graph_reader() -> Neo4jGraphReader | None:
    """按 env 构造 reader；未配置 Neo4j 返回 None。"""
    s = get_settings()
    if not s.neo4j_enabled:
        return None
    return Neo4jGraphReader(
        uri=s.NEO4J_URI,
        user=s.NEO4J_USERNAME,
        password=s.NEO4J_PASSWORD,
        database=s.NEO4J_DATABASE or None,
    )