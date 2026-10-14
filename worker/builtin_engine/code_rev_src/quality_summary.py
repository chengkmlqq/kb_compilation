#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量评估汇总+落库（质量评估编排 v1，2026-10-13）。

编排收尾节点：接收三个维度检查结果(chunk/wiki/graph)，生成汇总归因清单，
落库 kb_quality_run + kb_quality_report（页面查询/汇总用）。

用法（编排 script 节点）：
  import quality_summary
  quality_summary.run(kb_id, run_id, scope, doc_ids, chunk_r, wiki_r, graph_r)
"""
import json
import sys
import uuid

sys.path.insert(0, "/srv/kb/worker/builtin_engine/code_rev_src")


def run(kb_id: str, run_id: str, scope: str, doc_ids: list | None,
        chunk_r: dict | None, wiki_r: dict | None, graph_r: dict | None,
        created_by: str = "") -> dict:
    from api.db import get_sessionmaker
    from sqlalchemy import text

    issues: list = []
    attribution: list = []
    metrics_map: dict[str, dict] = {}
    for dim, r in (("chunk", chunk_r), ("wiki", wiki_r), ("graph", graph_r)):
        if not r:
            continue
        metrics_map[dim] = r.get("metrics") or {}
        for it in (r.get("issues") or []):
            issues.append({**it, "dim": dim})
        for at in (r.get("attribution") or []):
            attribution.append(at)

    scores = {d: (m.get("health_score") or 0) for d, m in metrics_map.items()}
    overall = int(sum(scores.values()) / len(scores)) if scores else 0
    summary = (
        f"评估完成: 切片 {scores.get('chunk','-')} / wiki {scores.get('wiki','-')} / "
        f"图数据 {scores.get('graph','-')} / 综合 {overall}; "
        f"问题 {len(issues)} 条, 归因建议 {len(attribution)} 条"
    )

    db = get_sessionmaker()()
    try:
        if not run_id:
            # 独立执行（无编排 run 上下文）：自建 kb_quality_run 行
            run_id = uuid.uuid4().hex[:32]
            db.execute(
                text("INSERT INTO kb_quality_run (id, kb_id, scope, doc_ids, status, "
                     "created_by) VALUES (:id, :kb, :scope, :docs, 'success', :by)"),
                {"id": run_id, "kb": kb_id, "scope": scope,
                 "docs": json.dumps(doc_ids or [], ensure_ascii=False), "by": created_by or None},
            )
        db.execute(
            text("UPDATE kb_quality_run SET status='success', chunk_metrics=:cm, "
                 "wiki_metrics=:wm, graph_metrics=:gm, issues=:iss, attribution=:att, "
                 "summary=:sum, updated_at=CURRENT_TIMESTAMP WHERE id=:rid"),
            {"rid": run_id, "cm": json.dumps(metrics_map.get("chunk") or {}, ensure_ascii=False),
             "wm": json.dumps(metrics_map.get("wiki") or {}, ensure_ascii=False),
             "gm": json.dumps(metrics_map.get("graph") or {}, ensure_ascii=False),
             "iss": json.dumps(issues, ensure_ascii=False),
             "att": json.dumps(attribution, ensure_ascii=False),
             "sum": summary},
        )
        # 每维度一条 report(页面按 run 聚合)
        for dim, r in (("chunk", chunk_r), ("wiki", wiki_r), ("graph", graph_r)):
            if not r:
                continue
            db.execute(
                text("INSERT INTO kb_quality_report (id, run_id, kb_id, doc_id, dim, metrics, issues) "
                     "VALUES (:id, :rid, :kb, :doc, :dim, :m, :i)"),
                {"id": uuid.uuid4().hex[:32], "rid": run_id, "kb": kb_id,
                 "doc": doc_ids[0] if doc_ids and scope == "single" else None,
                 "dim": dim,
                 "m": json.dumps(r.get("metrics") or {}, ensure_ascii=False),
                 "i": json.dumps(r.get("issues") or [], ensure_ascii=False)},
            )
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        db.execute(
            text("UPDATE kb_quality_run SET status='failed', error=:e, updated_at=CURRENT_TIMESTAMP "
                 "WHERE id=:rid"),
            {"e": str(e)[:500], "rid": run_id},
        )
        db.commit()
        return {"success": False, "error": str(e)[:300]}
    finally:
        db.close()
    print(f"[quality] {summary}", flush=True)
    return {"success": True, "run_id": run_id, "overall": overall, "issues": len(issues)}
