#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片质量检查（质量评估编排 v1，2026-10-13）。

维度 chunk：检查文档切分质量——切片数/长度分布/过短过长占比/截断痕迹/
近似重复率。DB 直连 doc_chunk 表（快且准），只读不写。

用法：
  编排 script 节点: import quality_chunk; r = quality_chunk.run(kb_id, doc_ids)
  命令行:          python3 quality_chunk.py --kb <kb_id> [--doc <id>]

输出：{"metrics": {...}, "issues": [...], "attribution": [...]}
"""
import argparse
import hashlib
import sys

sys.path.insert(0, "/srv/kb/worker/builtin_engine/code_rev_src")

SHORT = 50      # 过短切片阈值(字符)
LONG = 2000     # 过长切片阈值(字符)
TRUNC_MARKS = ("…", "...", "（节选）", "[截断]")

_STABLE_REL_TYPES = ("约束", "主体", "客体", "条件", "出自", "依据")


def run(kb_id: str, doc_ids: list[str] | None = None) -> dict:
    """执行切片质量检查，返回 metrics/issues/attribution。"""
    from api.db import get_sessionmaker
    from sqlalchemy import text

    db = get_sessionmaker()()
    try:
        if doc_ids:
            cond = "kb_id=:kb AND document_id IN :ids"
            params = {"kb": kb_id, "ids": tuple(doc_ids)}
        else:
            cond = "kb_id=:kb"
            params = {"kb": kb_id}
        rows = db.execute(
            text(
                f"SELECT document_id, seq, content, meta FROM doc_chunk "
                f"WHERE {cond} AND enabled=1 ORDER BY document_id, seq"
            ),
            params,
        ).all()
    finally:
        db.close()

    if not rows:
        return {"metrics": {"docs": 0, "chunks": 0, "avg_len": 0},
                "issues": [{"dim": "chunk", "issue_type": "no_chunks",
                            "detail": "无切片数据(文档未解析或无切片)"}],
                "attribution": [{"dim": "chunk", "cause": "文档未进入切片阶段或解析失败",
                                 "suggestion": "检查 parse_state / 重跑解析"}]}

    # 按文档聚合
    by_doc: dict[str, list] = {}
    for rid, seq, content, meta in rows:
        by_doc.setdefault(str(rid), []).append((int(seq or 0), str(content or ""), meta))

    issues: list[dict] = []
    totals = {"docs": len(by_doc), "chunks": 0, "short": 0, "long": 0,
              "truncated": 0, "dup": 0}
    per_doc: dict[str, dict] = {}
    for doc_id, chunks in by_doc.items():
        lens = [len(c) for _, c, _ in chunks]
        n = len(chunks)
        totals["chunks"] += n
        short_n = sum(1 for L in lens if L < SHORT)
        long_n = sum(1 for L in lens if L > LONG)
        trunc_n = sum(1 for _, c, _ in chunks if any(m in c for m in TRUNC_MARKS))
        seen: set[str] = set()
        dup_n = 0
        for _, c, _ in chunks:
            h = hashlib.md5(c.strip()[:200].encode("utf-8")).hexdigest()
            if h in seen:
                dup_n += 1
            seen.add(h)
        totals["short"] += short_n
        totals["long"] += long_n
        totals["truncated"] += trunc_n
        totals["dup"] += dup_n
        per_doc[doc_id] = {
            "doc_id": doc_id, "chunks": n, "avg_len": round(sum(lens) / n, 1) if n else 0,
            "short": short_n, "long": long_n, "truncated": trunc_n, "dup": dup_n,
        }
        if short_n / max(n, 1) > 0.3:
            issues.append({"dim": "chunk", "doc_id": doc_id, "issue_type": "too_many_short_chunks",
                           "detail": f"{short_n}/{n} 切片过短(<{SHORT}字),占比 {short_n/n:.0%}"})
        if long_n / max(n, 1) > 0.1:
            issues.append({"dim": "chunk", "doc_id": doc_id, "issue_type": "too_many_long_chunks",
                           "detail": f"{long_n}/{n} 切片过长(>{LONG}字),占比 {long_n/n:.0%}"})
        if trunc_n > 0:
            issues.append({"dim": "chunk", "doc_id": doc_id, "issue_type": "truncated_chunks",
                           "detail": f"{trunc_n} 个切片含截断标记"})
        if dup_n / max(n, 1) > 0.2:
            issues.append({"dim": "chunk", "doc_id": doc_id, "issue_type": "duplicate_chunks",
                           "detail": f"{dup_n}/{n} 切片疑似近似重复"})

    short_r = totals["short"] / max(totals["chunks"], 1)
    long_r = totals["long"] / max(totals["chunks"], 1)
    metrics = {
        **totals,
        "short_ratio": round(short_r, 4),
        "long_ratio": round(long_r, 4),
        "health_score": _score(short_r, long_r, totals["truncated"] / max(totals["chunks"], 1)),
        "per_doc": per_doc,
    }

    attribution = _attribution(metrics, issues)
    return {"metrics": metrics, "issues": issues, "attribution": attribution}


def _score(short_r, long_r, trunc_r) -> int:
    s = 100.0
    s -= short_r * 60 + long_r * 60 + trunc_r * 80
    return max(0, int(s))


def _attribution(metrics: dict, issues: list) -> list:
    out = []
    if metrics.get("short_ratio", 0) > 0.3:
        out.append({"dim": "chunk", "cause": "过短切片占比高 → 切分粒度偏细/按词切分",
                    "suggestion": "调大 chunk_size 或按段落合并相邻短块"})
    if metrics.get("long_ratio", 0) > 0.1:
        out.append({"dim": "chunk", "cause": "过长切片占比高 → 切分边界未生效(表/长句整块保留)",
                    "suggestion": "对长表/长句二次切分,或提高 max_chars 后按语义边界再分"})
    if metrics.get("truncated", 0) > 0:
        out.append({"dim": "chunk", "cause": "切片带截断标记 → 切分在硬长度处截断而非语义边界",
                    "suggestion": "开启 overlap/按句号段号切分,减少硬截断"})
    if metrics.get("dup", 0) > 0:
        out.append({"dim": "chunk", "cause": "近似重复切片 → 重叠窗口/段落重复扫描",
                    "suggestion": "关闭 overlap 或调小重叠比例,去重后再入库"})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True)
    ap.add_argument("--doc", default="")
    a = ap.parse_args()
    ids = [d for d in a.doc.split(",") if d] or None
    import json
    print(json.dumps(run(a.kb, ids), ensure_ascii=False, indent=1))
