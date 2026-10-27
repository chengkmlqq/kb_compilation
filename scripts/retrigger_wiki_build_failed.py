#!/usr/bin/env python3
"""重触发 济南市监局 知识库失败文档的 wiki构建（2026-10-27）。

背景：10-24/25/26 容器重建造成 /tmp/reg_* 中间产物丢失，wiki构建-行业监管/
医疗健康/法务 等 400+ run 批量 failed（FileNotFoundError）。tape 已迁
reg_cache（MinIO）并重灌线上，本脚本把这些失败文档的 wiki构建 重新发起。

对象：kb_tape_run 中 status='failed' 且 error 含 '/tmp/reg_' 的 run，
按 bindings/inputs 里的 kid 去重（一个文档只重发一次），用**迁移后 tape**
（tape_id 不变，step_config 已换新）原样重跑。LLM 403 类的失败（等 key
修复）也可通过 --include-403 纳入。

用法（容器内 api-server）：
  docker cp scripts/retrigger_wiki_build_failed.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/retrigger_wiki_build_failed.py --kb abbf0b4e0c3f41faaab8a3dd8180e1fd
参数：
  --kb <kb_id>   必填，限该知识库
  --limit N      最多重发 N 个文档（默认 0=不限）
  --include-403  同时纳入 LLM 403 类失败（默认只重发 /tmp/reg_ 类）
  --dry-run      只统计不发起
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime

sys.path.insert(0, "/srv/kb")

from sqlalchemy import select  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.orchestration import TapeRun  # noqa: E402
from api.services.orchestration import _create_tape_run  # noqa: E402

TMP_ERR = "/tmp/reg_"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True, help="知识库 id")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-403", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = get_sessionmaker()()
    try:
        # 失败 run：/tmp/reg_ 类（默认）+ 可选 403 类
        q = (
            select(TapeRun)
            .where(
                TapeRun.status == "failed",
                TapeRun.bindings.like(f"%{args.kb}%"),
            )
        )
        runs = db.execute(q).scalars().all()

        by_kid: dict[str, dict] = {}
        for r in runs:
            err = r.error or ""
            if TMP_ERR in err:
                cls = "tmp"
            elif "orphan" in err or "防重 guard" in err:
                cls = "stale"
            elif args.include_403 and "403" in err:
                cls = "403"
            else:
                continue
            b = r.bindings or {}
            kid = (b.get("kid") if isinstance(b, dict) else None) or ""
            if not kid:
                continue
            # 只取该文档最近一次失败 run 的 inputs 作为重发模板
            prev = by_kid.get(kid)
            if prev is None or (r.created_at or datetime.min) > prev.get("_t", datetime.min):
                by_kid[kid] = {"inputs": r.inputs, "cls": cls, "tape_id": r.tape_id,
                               "tape_name": r.tape_name, "run_id": r.id,
                               "_t": r.created_at or datetime.min}

        items = sorted(by_kid.values(), key=lambda x: x["_t"], reverse=True)
        if args.limit > 0:
            items = items[: args.limit]
        print(f"[retrigger] 失败文档去重 {len(by_kid)} 个（含 403: {args.include_403}），本次将重发 {len(items)} 个")

        launched = 0
        for it in items:
            kid = (it["inputs"] or {}).get("kid") if isinstance(it["inputs"], dict) else None
            label = f"{it['cls']} tape={it['tape_name']} kid={(kid or '')[:12]}… run={it['run_id'][:12]}…"
            if args.dry_run:
                print(f"[retrigger] [dry] {label}")
                continue
            try:
                created = _create_tape_run(
                    db, it["tape_id"], inputs=it["inputs"], env="prod"
                )
                rid = created["run_id"]
                run_env = created.get("run_env") or "prod"
                # 建行后必须投递 orch 调度中枢（execute_tape 同款），否则 run 永远 queued
                from worker.celery_app import celery_app

                celery_app.send_task(
                    "worker.tasks.orch_runner.execute_run",
                    args=[rid, run_env],
                    queue="orch",
                )
                print(f"[retrigger] 已发起 {label} → new_run={rid[:12]}…")
                launched += 1
            except ValueError as e:
                # 已存在进行中 run（防重 guard）→ 视为完成
                print(f"[retrigger] 跳过 {label}：{e}")
        print(f"[retrigger] 完成：新发起 {launched} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()