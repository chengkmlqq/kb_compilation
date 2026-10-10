#!/usr/bin/env python
"""元数据采集 HTTP 层 e2e（部署 50 后在容器内或本机跑）。

覆盖功能点（逐项 PASS/FAIL 输出）：
  1. metadata/config 读取全量采集开关
  2. metadata/datasources 可采集数据源列表（结构 + 状态字段）
  3. metadata/tables 表列表分页（无数据源时 total=0 也算 PASS）
  4. metadata/collection 提交采集任务（有可采集数据源时）→ 返回 job_id
  5. metadata/collection/{job_id} 任务状态可查
  6. metadata/template 导入模板可下载（xlsx 魔数 PK）
  7. metadata/runs 采集记录列表
  8. 无 token → 401
  9. DELETE 不存在数据源 → 404 或 400（不能 500）
 10. kb_metadata_table/column 表在库中存在

用法（容器内）：
  docker cp scripts/metadata-e2e.py kb-api-server:/tmp/ && \
  docker exec -w /srv/kb kb-api-server python /tmp/metadata-e2e.py
注意：脚本纯 ASCII，避免 Windows MSYS curl body 转 GBK 问题（脚本本身不受影响）。
"""
from __future__ import annotations

import io
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
USER = "admin"
PWD = "admin123"

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{ 'PASS' if ok else 'FAIL' }] {name}" + (f" :: {detail}" if detail else ""))


def main() -> int:
    c = httpx.Client(timeout=30.0)
    # ---- login ----
    r = c.post(
        f"{BASE}/api/v1/auth/login",
        json={"userId": USER, "pwd": PWD},
    )
    if r.status_code != 200:
        print("login failed:", r.status_code, r.text[:200])
        return 2
    body = r.json()
    tok = body.get("identity_cookie") or ""
    if not body.get("success") or not tok:
        print("login not success:", body.get("message"))
        return 2
    H = {"Cookie": f"x-next-identity={tok}"}
    check("login", True, f"token len={len(tok)}")

    # ---- 1. config ----
    r = c.get(f"{BASE}/api/v1/metadata/config", headers=H)
    ok = r.status_code == 200 and r.json().get("success") is True
    check("metadata/config", ok, f"{r.status_code} {r.text[:120]}")

    # ---- 2. datasources ----
    r = c.get(f"{BASE}/api/v1/metadata/datasources", headers=H)
    ok = r.status_code == 200
    dss: list[dict] = []
    if ok:
        j = r.json()
        ok = j.get("success") is True
        dss = (j.get("data") or {}).get("items") or []
    check("metadata/datasources", ok and isinstance(dss, list), f"{r.status_code} n={len(dss)}")

    # 结构字段检查（第一个数据源）
    if dss:
        d0 = dss[0]
        fields = ["id", "name", "dsType", "collectionStatus", "tableCount"]
        missing = [f for f in fields if f not in d0]
        check("datasources fields", not missing, f"missing={missing}")
    else:
        print("  (no collectable datasource — submit/template assertions relaxed)")

    # ---- 3. tables ----
    r = c.get(f"{BASE}/api/v1/metadata/tables?page=1&page_size=10", headers=H)
    ok = r.status_code == 200 and r.json().get("success") is True
    check("metadata/tables", ok, f"{r.status_code} {r.text[:120]}")

    # ---- 4. submit collection ----
    job_id = ""
    if dss:
        ds_id = dss[0]["id"]
        r = c.post(
            f"{BASE}/api/v1/metadata/collection",
            headers=H,
            json={"datasource_id": ds_id, "collection_mode": "full"},
        )
        ok = r.status_code == 200 and r.json().get("success") is True
        if ok:
            job_id = ((r.json().get("data") or {}).get("job_id")) or ""
        check("metadata/collection submit", ok, f"{r.status_code} {r.text[:160]}")

        # ---- 5. job status ----
        if job_id:
            import time

            time.sleep(2)
            r = c.get(f"{BASE}/api/v1/metadata/collection/{job_id}", headers=H)
            ok = r.status_code == 200 and r.json().get("success") is True
            check("metadata/collection/{id}", ok, f"{r.status_code} {r.text[:160]}")
    else:
        print("  (skip submit/status — no collectable datasource)")

    # ---- 6. template download ----
    r = c.get(f"{BASE}/api/v1/metadata/template", headers=H)
    magic = r.content[:2] if r.content else b""
    ok = r.status_code == 200 and magic == b"PK"
    check("metadata/template xlsx", ok, f"{r.status_code} magic={magic!r} len={len(r.content)}")

    # ---- 7. runs ----
    r = c.get(f"{BASE}/api/v1/metadata/runs?page=1&page_size=10", headers=H)
    ok = r.status_code == 200 and r.json().get("success") is True
    check("metadata/runs", ok, f"{r.status_code} {r.text[:120]}")

    # ---- 8. no token -> 401 ----
    r = c.get(f"{BASE}/api/v1/metadata/datasources")
    ok = r.status_code == 401
    check("no-token 401", ok, f"{r.status_code}")

    # ---- 9. delete nonexistent -> not 500 ----
    r = c.delete(f"{BASE}/api/v1/metadata/datasources/does-not-exist", headers=H)
    ok = r.status_code in (400, 404)
    check("delete missing ds not-500", ok, f"{r.status_code} {r.text[:120]}")

    # ---- 10. tables exist in DB ----
    try:
        from api.db import get_db
        from sqlalchemy import text

        db = next(get_db())
        t = db.execute(text("SHOW TABLES LIKE 'kb_metadata%'")).all()
        names = sorted(str(x[0]) for x in t)
        check("kb_metadata tables in DB", len(names) >= 2, f"{names}")
        db.close()
    except Exception as e:  # noqa: BLE001
        check("kb_metadata tables in DB", False, f"err={e}")

    c.close()
    npass = sum(1 for _n, s, _d in results if s == "PASS")
    ntot = len(results)
    print(f"\n===== {npass}/{ntot} PASS =====")
    if npass < ntot:
        print("FAILED items:")
        for n, s, d in results:
            if s == "FAIL":
                print(f"  - {n} :: {d}")
    return 0 if npass == ntot else 1


if __name__ == "__main__":
    sys.exit(main())