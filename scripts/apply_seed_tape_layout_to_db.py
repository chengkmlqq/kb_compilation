"""把 fix_seed_tape_layout.py 重排后的节点坐标同步进 kb_tape.nodes（2026-10-21）。

种子 JSON（scripts/seed_tape_data/*.json）是编排数据真源，但线上库里的
Tape.nodes 是播种时写入的历史版本——只更新种子文件不会动线上画布。
本脚本按 tape_name 匹配，把 JSON draft.nodes 的 position 应用到库内
Tape.nodes（仅更新 position，其余节点字段不动；节点 id 不一致则跳过该文件）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from api.db import get_sessionmaker
from api.models.orchestration import Tape

from sqlalchemy import select

DATA_DIR = Path(__file__).resolve().parent / "seed_tape_data"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="同步种子编排布局坐标到库内 kb_tape.nodes")
    parser.add_argument(
        "--data-dir", default=str(DATA_DIR),
        help="seed JSON 目录（默认 scripts/seed_tape_data；部署容器内可指向 /tmp/seed_tape_data）",
    )
    args = parser.parse_args()

    db = get_sessionmaker()()
    applied = skipped = 0
    try:
        for fp in sorted(Path(args.data_dir).glob("*.json")):
            try:
                doc = json.loads(fp.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                print(f"  跳过(读取失败) {fp.name}: {e}")
                continue
            d = doc.get("draft") or doc
            name = str(d.get("tapeName") or "").strip()
            src_pos = {str(n["id"]): n.get("position") for n in (d.get("nodes") or [])}
            if not name or not src_pos:
                continue
            tape = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
            if not tape:
                print(f"  跳过(库无此编排): {name}")
                skipped += 1
                continue
            nodes = copy.deepcopy(list(tape.nodes or []))
            if len(nodes) != len(src_pos):
                # 节点数不一致：id 集合对齐才更新，否则跳过（防错配）
                if not all(str(n.get("id")) in src_pos for n in nodes):
                    print(f"  跳过(节点 id 不匹配): {name}")
                    skipped += 1
                    continue
            changed = 0
            for n in nodes:
                nid = str(n.get("id"))
                if nid in src_pos and n.get("position") != src_pos[nid]:
                    n["position"] = src_pos[nid]
                    changed += 1
            if changed:
                # ⚠️ 必须赋新对象：JSON 列的原地修改（改 dict 再赋回同一 list）
                # 不会被 SQLAlchemy 标记为脏 → commit 静默不落库（假成功）。
                # deepcopy 后再赋值，变更才真正写入。
                tape.nodes = nodes
                db.commit()
                applied += 1
                print(f"  更新 {name}: {changed} 个节点坐标")
            else:
                print(f"  无需更新: {name}")
    finally:
        db.close()
    print(f"\n完成: 更新 {applied} 个编排 / 跳过 {skipped} 个")


if __name__ == "__main__":
    sys.exit(main())