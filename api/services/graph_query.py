"""Neo4j 知识图谱查询服务。

读取端（api/routers/graph.py 图谱接口使用）。兼容两套写入 schema：
1. 技能直连写入（监督管理制度转wiki 等技能包 weknora_rpc._graph_call）：
   节点  (n:ENTITY<kb_id 去横线> {name, attributes[]})
   关系  (a)-[r:<语义类型如 约束/处罚/制定于>]->(b)
2. 平台 Neo4jGraphStore 旧写入（:Entity {name, kb_id, ...} + :RELATED_TO {type}）

主查技能写入的 ENTITY<kb_id>（当前监管库 1341 节点/2039 边均在此），
并兼容旧 Entity label（若存在 kb_id 匹配数据）。
"""

from __future__ import annotations

import logging
from typing import Any

from api.config import get_settings

logger = logging.getLogger(__name__)


class GraphReader:
    def __init__(self) -> None:
        s = get_settings()
        self._uri = s.NEO4J_URI
        self._auth = (s.NEO4J_USERNAME, s.NEO4J_PASSWORD)
        self._database = s.NEO4J_DATABASE

    @staticmethod
    def _entity_label(kb_id: str) -> str:
        """技能写入侧的节点 label：ENTITY + kb_id 去横线（weknora_rpc._graph_call 同构）。"""
        return "ENTITY" + kb_id.replace("-", "_")

    def _scope(self, kb_id: str, alias: str = "n") -> str:
        """节点范围 WHERE 条件：技能 ENTITY<kb> label（主）∪ 平台 Entity {kb_id}（兼容）。"""
        l1 = self._entity_label(kb_id)
        return f'{alias}:{l1} OR ({alias}:Entity AND {alias}.kb_id = $kb_id)'

    def _run(self, query: str, params: dict | None = None, **kwargs: Any) -> list[dict]:
        from neo4j import GraphDatabase

        merged: dict[str, Any] = dict(params or {})
        merged.update(kwargs)
        driver = GraphDatabase.driver(self._uri, auth=self._auth)
        try:
            with driver.session(database=self._database) as session:
                result = session.run(query, **merged)
                return [dict(record) for record in result]
        finally:
            driver.close()

    # ---- shape builders ----
    @staticmethod
    def _to_nodes(rows: list[dict]) -> list[dict]:
        nodes = []
        for r in rows:
            attrs = r.get("attributes") or []
            nodes.append(
                {
                    "name": r.get("name", ""),
                    # 业务类别：技能写入存 attributes[0]（如 监管对象/处罚规则）
                    "entity_type": r.get("entity_type") or (attrs[0] if attrs else ""),
                    "description": r.get("description")
                    or (" ".join(str(a) for a in attrs[1:4]) if len(attrs) > 1 else ""),
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
                    # 关系语义类型（技能写入：Cypher 类型即语义动词，如 约束/处罚）
                    "type": r.get("rel_type", "RELATED_TO"),
                    "description": r.get("description", ""),
                    "strength": r.get("strength", ""),
                }
            )
        return edges

    # ---- queries ----
    def overview(self, kb_id: str, limit: int = 200) -> dict:
        """全库图：按度数取 TOP N 节点 + 它们之间的边。"""
        scope = self._scope(kb_id)
        nodes = self._run(
            f"""
            MATCH (n) WHERE {scope}
            OPTIONAL MATCH (n)-[r]-()
            WITH n, count(DISTINCT r) AS degree
            RETURN n.name AS name, n.entity_type AS entity_type,
                   n.description AS description, n.attributes AS attributes,
                   n.chunks AS chunks, degree
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
        cscope = self._scope(kb_id, "c")
        mscope = self._scope(kb_id, "m")
        nodes = self._run(
            f"""
            MATCH (c) WHERE {cscope} AND c.name = $center
            MATCH p = (c)-[*1..{depth}]-(m)
            WHERE {mscope}
            WITH m, min(length(p)) AS hops
            RETURN m.name AS name, m.entity_type AS entity_type,
                   m.description AS description, m.attributes AS attributes,
                   m.chunks AS chunks, hops AS degree
            ORDER BY hops, degree
            LIMIT $limit
            """,
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
        scope = self._scope(kb_id)
        nodes = self._run(
            f"""
            MATCH (n) WHERE {scope}
            AND ANY(t IN $terms WHERE toLower(n.name) CONTAINS toLower(t))
            OPTIONAL MATCH (n)-[r]-()
            WITH n, count(DISTINCT r) AS degree
            RETURN n.name AS name, n.entity_type AS entity_type,
                   n.description AS description, n.attributes AS attributes,
                   n.chunks AS chunks, degree
            ORDER BY degree DESC, name
            LIMIT $limit
            """,
            {"kb_id": kb_id, "terms": terms, "limit": limit},
        )
        edges = self._edges_among(kb_id, [n["name"] for n in nodes])
        return {"nodes": self._to_nodes(nodes), "edges": edges}

    def _edges_among(self, kb_id: str, names: list[str]) -> list[dict]:
        """取两端都在 names 集合内的边（诱导子图）。

        技能写入按 source->target 建有向边且 Cypher 类型即语义动词
        （约束/处罚/制定于…），无向匹配会重复；用有向匹配 + 任意类型。
        """
        if len(names) < 2:
            return []
        a_scope = self._scope(kb_id, "a")
        b_scope = self._scope(kb_id, "b")
        # 去掉 a_scope/b_scope 中的别名前缀（保留 OR 表达式本体供 MATCH 复用）
        rows = self._run(
            f"""
            MATCH (a)-[r]->(b)
            WHERE {a_scope} AND {b_scope} AND a.name IN $names AND b.name IN $names
            RETURN a.name AS source, b.name AS target,
                   type(r) AS rel_type, r.description AS description, r.strength AS strength
            """,
            {"kb_id": kb_id, "names": names},
        )
        return self._to_edges(rows)

    def stats(self, kb_id: str) -> dict:
        """图规模统计：节点数/关系数/实体类型分布。"""
        scope = self._scope(kb_id)
        row = self._run(
            f"""
            MATCH (n) WHERE {scope}
            OPTIONAL MATCH (n)-[r]-()
            WITH count(DISTINCT n) AS nodes, count(DISTINCT r) AS edges
            RETURN nodes, edges
            """,
            {"kb_id": kb_id},
        )
        types = self._run(
            f"""
            MATCH (n) WHERE {scope}
            RETURN coalesce(n.entity_type, n.attributes[0], '') AS t, count(*) AS c
            ORDER BY c DESC
            """,
            {"kb_id": kb_id},
        )
        return {
            "nodes": (row[0]["nodes"] if row else 0),
            "edges": (row[0]["edges"] if row else 0),
            "types": [(r["t"], r["c"]) for r in types],
        }


def get_graph_reader() -> GraphReader | None:
    try:
        return GraphReader()
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph reader init failed: %s", exc)
        return None