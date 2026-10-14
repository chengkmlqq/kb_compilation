#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图数据质量检查（质量评估编排 v1，2026-10-13）。

维度 graph：Neo4j 语义图质量——节点/边计数、哑节点(无属性)、边类型合法性
(6 种稳定边)、可回溯性(rule_slug/note/page_id 缺失)、wiki 页面存在性对照。
直连 Neo4j 只读不写。

用法：
  编排 script 节点: import quality_graph; r = quality_graph.run(kb_id)
  命令行:          python3 quality_graph.py --kb <kb_id>

输出：{"metrics": {...}, "issues": [...], "attribution": [...]}
"""
import argparse
import os
import sys

sys.path.insert(0, "/srv/kb/worker/builtin_engine/code_rev_src")

_STABLE_REL_TYPES = ("约束", "主体", "客体", "条件", "出自", "依据")


def _driver():
    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI", "bolt://kb-neo4j:7687")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    pwd = os.getenv("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, pwd))


def run(kb_id: str) -> dict:
    label = "ENTITY" + kb_id.replace("-", "_")
    driver = _driver()
    try:
        with driver.session() as sess:
            nodes = sess.run(
                f"MATCH (n:`{label}`) RETURN count(n) AS c"
            ).single()["c"]
            rels = sess.run(
                f"MATCH (a:`{label}`)-[r]-(b:`{label}`) RETURN count(r) AS c"
            ).single()["c"]
            # 哑节点：无 attributes 属性（attributes 空或缺失）
            dumb = sess.run(
                f"MATCH (n:`{label}`) WHERE n.attributes IS NULL OR size(n.attributes)=0 "
                "RETURN count(n) AS c"
            ).single()["c"]
            # 边类型分布
            rel_rows = sess.run(
                f"MATCH (a:`{label}`)-[r]-(b:`{label}`) "
                "RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
            ).data()
            rel_types = {r["t"]: r["c"] for r in rel_rows}
            # 不可回溯关系：边缺 rule_slug 且缺 note（无法回到规则卡/原文）
            untraceable = sess.run(
                f"MATCH (a:`{label}`)-[r]-(b:`{label}`) "
                "WHERE (r.rule_slug IS NULL OR r.rule_slug='') AND (r.note IS NULL OR r.note='') "
                "RETURN count(r) AS c"
            ).single()["c"]
            # 边指向的 wiki 页存在性：节点带 page_slug 的,检查是否在 wiki_pages
            node_slugs = sess.run(
                f"MATCH (n:`{label}`) WHERE n.page_slug IS NOT NULL AND n.page_slug<>'' "
                "RETURN collect(DISTINCT n.page_slug) AS slugs"
            ).single()["slugs"] or []
    finally:
        driver.close()

    issues: list[dict] = []
    # 哑节点
    if dumb > 0:
        issues.append({"dim": "graph", "issue_type": "dumb_nodes",
                       "detail": f"{dumb} 个哑节点(无 attributes),占 {dumb/max(nodes,1):.0%}"})
    # 非法边类型
    bad_types = {t: c for t, c in rel_types.items() if t not in _STABLE_REL_TYPES}
    if bad_types:
        issues.append({"dim": "graph", "issue_type": "illegal_rel_types",
                       "detail": f"非稳定边类型: {bad_types}"})
    # 不可回溯边
    if untraceable > 0:
        issues.append({"dim": "graph", "issue_type": "untraceable_rels",
                       "detail": f"{untraceable} 条边无 rule_slug/note,无法回溯规则卡/原文"})
    # wiki 存在性对照
    from api.db import get_sessionmaker
    from sqlalchemy import text

    db = get_sessionmaker()()
    try:
        if node_slugs:
            existing = db.execute(
                text("SELECT slug FROM wiki_page WHERE kb_id=:kb AND slug IN :slugs AND status='active'"),
                {"kb": kb_id, "slugs": tuple(node_slugs)},
            ).scalars().all() if node_slugs else []
            missing_slugs = [s for s in node_slugs if s not in set(existing)]
            if missing_slugs:
                issues.append({"dim": "graph", "issue_type": "wiki_missing_nodes",
                               "detail": f"{len(missing_slugs)} 个图节点对应 wiki 页不存在,示例: {missing_slugs[:5]}"})
        # 图侧无节点但 wiki 有页(构建未写图)
        wiki_pages = db.execute(
            text("SELECT count(*) FROM wiki_page WHERE kb_id=:kb AND status='active'"),
            {"kb": kb_id},
        ).scalar() or 0
    finally:
        db.close()

    health = 100.0
    if nodes:
        health -= (dumb / nodes) * 80
        health -= (untraceable / max(rels, 1)) * 60
    if bad_types:
        health -= 40
    if issues:
        health -= 10 * len(issues)
    metrics = {
        "nodes": nodes, "rels": rels, "dumb_nodes": dumb,
        "rel_types": rel_types, "untraceable_rels": untraceable,
        "wiki_pages": wiki_pages,
        "dumb_ratio": round(dumb / max(nodes, 1), 4) if nodes else 0,
        "health_score": max(0, int(health)),
    }
    attribution = _attribution(metrics, issues)
    return {"metrics": metrics, "issues": issues, "attribution": attribution}


def _attribution(m: dict, issues: list) -> list:
    out = []
    if m.get("dumb_nodes", 0) > 0:
        out.append({"dim": "graph", "cause": "哑节点 = 写入方未补 attributes(仅 name+kg)",
                    "suggestion": "图谱写入脚本收尾兜底补 attributes(定义/来源)"})
    bad = [i for i in issues if i.get("issue_type") == "illegal_rel_types"]
    if bad:
        out.append({"dim": "graph", "cause": "存在非稳定边类型 → 写入方未收敛到 6 种稳定边",
                    "suggestion": "关系类型映射归一(约束/主体/客体/条件/出自/依据)"})
    if m.get("untraceable_rels", 0) > 0:
        out.append({"dim": "graph", "cause": "边缺 rule_slug/note → 无法回溯规则卡/原文证据",
                    "suggestion": "写入时给每条边带 rule_slug+note(原文句子)"})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True)
    a = ap.parse_args()
    import json
    print(json.dumps(run(a.kb), ensure_ascii=False, indent=1))
