#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量指标 wiki 生成（CLI 壳）——业务函数见 metric_wiki_lib.py（无 argparse，可安全 import）。

用法（容器内执行）:
  python3 metric_wiki_demo.py --xlsx /tmp/全量指标20260814.xlsx --kb <kb_id> --limit 5
"""
import argparse
import json
import os
import sys

sys.path.insert(0, "/srv/kb/worker/builtin_engine/code_rev_src")
os.environ.setdefault("WEKNORA_KB_ID", "")
import weknora_rpc as wr
import xlsx_lite
from metric_wiki_lib import (  # noqa: E402
    llm_sql_analysis,
    build_page,
    static_sql_analysis,
)


def main():
    P = argparse.ArgumentParser()
    P.add_argument("--xlsx", required=True)
    P.add_argument("--kb", required=True)
    P.add_argument("--limit", type=int, default=5)
    P.add_argument("--only-code", default="", help="只建该指标编码")
    P.add_argument("--no-llm", action="store_true", help="跳过 SQL LLM 分析")
    A = P.parse_args()

    KB = A.kb
    wr.load_config()
    os.environ["WEKNORA_KB_ID"] = KB
    sh = xlsx_lite.read_sheets(A.xlsx)[0]
    rows = sh["rows"]
    header = rows[0]
    data = [dict(zip(header, r)) for r in rows[1:]]

    if A.only_code:
        data = [r for r in data if r.get("指标编码") == A.only_code]
    else:
        # 采样：跨分类各取一条，保证目录与分类效果可见
        seen, picked = set(), []
        for r in data:
            key = (r.get("指标分类-I"), r.get("指标分类-II"))
            if key in seen:
                continue
            seen.add(key)
            picked.append(r)
            if len(picked) >= A.limit:
                break
        data = picked

    # 建两级目录（幂等：按名称复用已有 folder，不重复创建）
    folders = {}
    existing = wr.tool_call("list_wiki_folders", {"kb_id": KB}) or {}
    ext = existing.get("folders") or (existing.get("data") or {}).get("folders") or []
    by_name = {f.get("name"): f.get("id") for f in ext if isinstance(f, dict) and f.get("name")}

    for meta in data:
        c1 = meta.get("指标分类-I") or "未分类"
        c2 = meta.get("指标分类-II") or "未分类"
        key = (c1, c2)
        if key in folders:
            continue
        print(f"建目录：{c1} / {c2}")
        # 顶级目录（c1）幂等
        f1 = by_name.get(c1)
        if not f1:
            r1 = wr.tool_call("create_wiki_folder", {"kb_id": KB, "name": c1, "parent_id": ""})
            rr = r1.get("data") or r1
            f1 = rr.get("id") if isinstance(rr, dict) else None
            by_name[c1] = f1
        # 二级目录（c2）在 f1 下幂等
        r2 = wr.tool_call("list_wiki_folders", {"kb_id": KB, "parent_id": f1 or ""})
        d2 = r2.get("folders") or (r2.get("data") or {}).get("folders") or (r2.get("data") or [])
        sub_by = {f.get("name"): f.get("id") for f in d2 if isinstance(f, dict) and f.get("name")}
        f2 = sub_by.get(c2)
        if not f2:
            r3 = wr.tool_call("create_wiki_folder", {"kb_id": KB, "name": c2, "parent_id": f1 or ""})
            rr3 = r3.get("data") or r3
            f2 = rr3.get("id") if isinstance(rr3, dict) else None
        folders[key] = (f1, c2, f2)
        print(f"  └ {c2} folder_id={f2}")

    pages = []
    for meta in data:
        c1 = meta.get("指标分类-I") or "未分类"
        c2 = meta.get("指标分类-II") or "未分类"
        f1, _, f2 = folders[(c1, c2)]
        sql = meta.get("SQL脚本") or ""
        print(f"[LLM] 分析 {meta['指标编码']} {meta['指标名称']}")
        an = llm_sql_analysis(wr, meta, sql, no_llm=A.no_llm)
        pages.append(build_page(meta, sql, f2, an))

    print(f"建页 {len(pages)} 个…")
    r = wr.tool_call("create_wiki_pages_batch", {"kb_id": KB, "pages": pages})
    if r.get("error"):
        print("建页失败：", str(r["error"])[:300])
        return 1
    d = r.get("data") or {}
    print("完成：", json.dumps({k: d.get(k) for k in ("created", "updated", "failed", "total")}, ensure_ascii=False))
    for p in pages:
        print("  →", p["slug"])
    return 0


if __name__ == "__main__":
    sys.exit(main())