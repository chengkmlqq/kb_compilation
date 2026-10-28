#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热修线上动态 wiki 构建 tape：n02 补 kp_paths 派生（2026-10-28 事故）。

用法（kb-api-server 容器内）：
  docker cp scripts/apply_fix_dynamic_tape_kpaths.py kb-api-server:/tmp/
  docker exec kb-api-server python /tmp/apply_fix_dynamic_tape_kpaths.py
  # 或指定 tape_id：... --tape a1988ab29e6d453bbcfd8a81e3fc3217

幂等：重建同名动态 tape 的 nodes/edges（与生成器当前输出一致），
仅覆盖损坏的 n02，不影响已成功 run 的产物。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/srv/kb")

from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload

BROKEN_TAPE_PREFIX = "wiki构建-动态-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default=None, help="指定 tape_id；缺省则修所有 wiki构建-动态- 前缀的 tape")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from api.db import get_sessionmaker
    from api.models.orchestration import Tape
    from api.services.orchestration import save_tape_design
    from sqlalchemy import select

    db = get_sessionmaker()()
    try:
        if args.tape:
            tapes = [db.execute(select(Tape).where(Tape.id == args.tape)).scalars().first()]
        else:
            tapes = list(db.execute(
                select(Tape).where(Tape.tape_name.like(f"{BROKEN_TAPE_PREFIX}%"))
            ).scalars().all())
        tapes = [t for t in tapes if t]
        print(f"匹配动态 wiki tape 数: {len(tapes)}")
        for t in tapes:
            payload = build_dynamic_tape_payload(tape_name=t.tape_name)
            n02 = next(n for n in payload["nodes"] if n["id"] == "n02")
            assigns = json.loads(n02["data"]["config"]["assignments"])
            has_kp = any(a["variable"] == "kp_paths" for a in assigns)
            print(f"  tape={t.id[:12]} name={t.tape_name!r} n02.kp_paths={'OK' if has_kp else 'MISSING'}")
            if not has_kp:
                print("    !! 生成器仍未派生 kp_paths，请先修复 gen_dynamic_wiki_tape.py")
                continue
            if args.dry_run:
                print("    [dry-run] 跳过写入")
                continue
            # 覆盖 nodes/edges（保持 tape_id 不变，run 不受影响）
            save_tape_design(db, t.id, payload["nodes"], payload["edges"])
            print(f"    [fixed] 已重灌 n02 派生变量")
        if not args.dry_run:
            db.commit()
        print("done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
