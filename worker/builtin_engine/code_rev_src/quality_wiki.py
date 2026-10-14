#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wiki 质量检查（质量评估编排 v1，2026-10-13）。

维度 wiki：空内容页 / 孤立页 / 断链 / 占位残留 / 证据链缺失。DB 直连
wiki_page + wiki_link（快且准），只读不写。规则归因对应页面可见的原文背书。

用法：
  编排 script 节点: import quality_wiki; r = quality_wiki.run(kb_id)
  命令行:          python3 quality_wiki.py --kb <kb_id>

输出：{"metrics": {...}, "issues": [...], "attribution": [...]}
"""
import argparse
import re
import sys

sys.path.insert(0, "/srv/kb/worker/builtin_engine/code_rev_src")

PLACEHOLDER = "*由 LLM 生成*"


def run(kb_id: str) -> dict:
    from api.db import get_sessionmaker
    from sqlalchemy import text

    db = get_sessionmaker()()
    try:
        pages = db.execute(
            text("SELECT id, slug, title, page_type, content, folder_id, folder_ids "
                 "FROM wiki_page WHERE kb_id=:kb AND status='active'"),
            {"kb": kb_id},
        ).all()
        links = db.execute(
            text("SELECT from_page_id, to_page_id FROM wiki_link WHERE kb_id=:kb"),
            {"kb": kb_id},
        ).all()
    finally:
        db.close()

    if not pages:
        return {"metrics": {"pages": 0, "health_score": 0},
                "issues": [{"dim": "wiki", "issue_type": "no_pages",
                            "detail": "无 wiki 页面(未构建或构建失败)"}],
                "attribution": [{"dim": "wiki", "cause": "wiki 构建未执行或全部失败",
                                 "suggestion": "检查构建编排 run 状态 / 重跑构建"}]}

    by_id = {str(p[0]): p for p in pages}
    slug_by_id = {str(p[0]): str(p[1]) for p in pages}  # p[1]=slug（p[2] 是 title——2026-10-14 曾误用 p[2] 致全量断链误报）
    linked_ids = {str(l[0]) for l in links} | {str(l[1]) for l in links}

    issues: list[dict] = []
    # 1) 空内容页
    empty = [p for p in pages if not (p[4] or "").strip()]
    for p in empty:
        issues.append({"dim": "wiki", "slug": str(p[1]), "page_type": str(p[3]),
                       "issue_type": "empty_content", "detail": "页面内容为空"})
    # 2) 孤立页（无入链无出链）
    orphans = [p for p in pages if str(p[0]) not in linked_ids]
    for p in orphans:
        issues.append({"dim": "wiki", "slug": str(p[1]), "page_type": str(p[3]),
                       "issue_type": "orphan", "detail": "无任何双向链接"})
    # 3) 断链：content 中 [[slug]] 目标不存在
    broken = 0
    broken_slugs: set[str] = set()
    for p in pages:
        targets = re.findall(r"\[\[([^\]|]+)", p[4] or "")
        for t in targets:
            t = t.strip()
            if t and t not in slug_by_id.values():
                broken += 1
                broken_slugs.add(t)
    if broken:
        issues.append({"dim": "wiki", "issue_type": "broken_links",
                       "detail": f"{broken} 处断链,目标示例: {list(broken_slugs)[:5]}"})
    # 4) 占位残留
    placeholder = [p for p in pages if PLACEHOLDER in (p[4] or "")]
    for p in placeholder:
        issues.append({"dim": "wiki", "slug": str(p[1]), "page_type": str(p[3]),
                       "issue_type": "llm_placeholder", "detail": "LLM 解读占位未填充"})
    # 5) 证据链缺失（无出处/来源制度/原文关联标记的页面；实体/规则页用「来源制度」「原文关联」，长句页本身即原文）
    _SOURCE_MARKS = ("出处", "来源制度", "原文关联", "来源")
    no_source = [p for p in pages if not any(m in (p[4] or "") for m in _SOURCE_MARKS)]
    for p in no_source:
        issues.append({"dim": "wiki", "slug": str(p[1]), "page_type": str(p[3]),
                       "issue_type": "missing_source", "detail": "页面无出处/原文引用标记"})
    # 6) 未挂目录（folder_id 空）
    no_folder = [p for p in pages if not (p[5] or p[6] or "")]
    for p in no_folder:
        issues.append({"dim": "wiki", "slug": str(p[1]), "page_type": str(p[3]),
                       "issue_type": "no_folder", "detail": "页面未挂载任何目录"})

    n = len(pages)
    metrics = {
        "pages": n,
        "empty": len(empty), "orphan": len(orphans), "broken_links": broken,
        "placeholder": len(placeholder), "missing_source": len(no_source),
        "no_folder": len(no_folder),
        "empty_ratio": round(len(empty) / n, 4), "orphan_ratio": round(len(orphans) / n, 4),
        "health_score": _score(n, len(empty), len(orphans), broken, len(placeholder), len(no_source)),
    }
    attribution = _attribution(metrics)
    return {"metrics": metrics, "issues": issues, "attribution": attribution}


def _score(n, empty, orphan, broken, placeholder, no_source) -> int:
    s = 100.0
    s -= (empty / n) * 100 + (orphan / n) * 60
    s -= min(broken, n) * 0.5
    s -= (placeholder / n) * 80 + (no_source / n) * 40
    return max(0, int(s))


def _attribution(m: dict) -> list:
    out = []
    if m.get("empty_ratio", 0) > 0.05:
        out.append({"dim": "wiki", "cause": "空内容页占比高 → 建页流程在生成内容前中断/LLM 输出为空",
                    "suggestion": "检查构建日志中该页的生成步骤;对空页重跑补写"})
    if m.get("orphan_ratio", 0) > 0.3:
        out.append({"dim": "wiki", "cause": "孤立页占比高 → 实体抽取过泛/页面间未建立互链",
                    "suggestion": "收敛实体抽取范围,构建尾部执行 rebuild_links"})
    if m.get("broken_links", 0) > 0:
        out.append({"dim": "wiki", "cause": "断链 = 引用了未创建的页面/实体(抽取了未建的实体名)",
                    "suggestion": "构建前先建全部实体页再补引用,或对断链目标补建页面"})
    if m.get("placeholder", 0) > 0:
        out.append({"dim": "wiki", "cause": "LLM 占位未填充 → 解读步骤未跑/供应商失败中断",
                    "suggestion": "重跑 LLM 解读补位(断点续传),或修供应商可达性"})
    if m.get("missing_source", 0) > 0:
        out.append({"dim": "wiki", "cause": "页面缺出处标记 → 渲染模板未带原文出处章节",
                    "suggestion": "渲染器补「出处」行,保证证据链可回原文"})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True)
    a = ap.parse_args()
    import json
    print(json.dumps(run(a.kb), ensure_ascii=False, indent=1))
