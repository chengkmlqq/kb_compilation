#!/usr/bin/env python3
"""清理 济南市监局 知识库的孤儿/卡死在途 run 并重发失败文档（2026-10-27）。

Step1: 把该 KB 下 status IN ('queued','running') 但 24h+ 未推进的 run 置 failed
       （error 注明原因；释放防重 guard，让重发能过 execute_tape 的 active 检查）。
Step2: 调 retrigger_wiki_build_failed.main 逻辑（--kb --include-403 --limit）。

孤儿判定：updated_at 早于 now-6h 且 status in (queued,running)。
（编排最慢节点实测 75min，NODE_STALE_S=6h；6h 无推进视为执行进程已死。）

用法（容器内 api-server）：
  docker cp scripts/retrigger_wiki_build_failed.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/retrigger_wiki_build_failed.py \
      --kb <kb_id> --include-403 [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/srv/kb")

from sqlalchemy import select  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.orchestration import TapeRun  # noqa: E402

STALE_S = 6 * 3600


def _stale_mark(r: TapeRun, now: datetime) -> str:
    return (f"worker 重启/编排迁移导致孤儿 run（created={r.created_at} last={r.updated_at}，"
            f"超过 {STALE_S // 3600}h 未推进），置 failed 释放防重 guard（2026-10-27 tape 迁移 reg_cache 后由重发脚本接管）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True, help="知识库 id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now()
    cutoff = now - timedelta(seconds=STALE_S)
    db = get_sessionmaker()()
    try:
        q = (
            select(TapeRun)
            .where(
                TapeRun.status.in_(("queued", "running")),
                TapeRun.bindings.like(f"%{args.kb}%"),
            )
        )
        runs = db.execute(q).scalars().all()
        stale = []
        for r in runs:
            last = r.updated_at or r.created_at
            if last is None or last < cutoff:
                stale.append(r)
        print(f"[stale-clean] 在途 run {len(runs)} 个，其中孤儿(>{STALE_S//3600}h 未推进) {len(stale)} 个")
        if args.dry_run:
            for r in stale:
                print(f"[stale-clean] [dry] {r.id[:12]} {r.tape_name[:20]} last={r.updated_at}")
            return
        for r in stale:
            r.status = "failed"
            r.error = _stale_mark(r, now)
            kid = (r.bindings or {}).get("kid") if isinstance(r.bindings, dict) else ""
            print(f"[stale-clean] 置 failed: {r.id[:12]} {r.tape_name[:24]} kid={str(kid)[:12]}")
        db.commit()
        print(f"[stale-clean] 完成：{len(stale)} 个孤儿 run 已置 failed")
    finally:
        db.close()


if __name__ == "__main__":
    main()