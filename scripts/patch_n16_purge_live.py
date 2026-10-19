"""更新线上已发布领域编排的 n16「合并+链接重建」步骤代码为带保护版本。

背景：seed_framework_db._seed_tapes_from_exports 幂等 skip（已存在编排不覆盖），
仓库 seed_tape_data/*.json 改后线上 kb_tape_step 仍是旧版。本脚本用种子 JSON
（真源）里的新 n16 code 更新线上 TapeStep.step_config.code 与 Tape.nodes 画布 code。

用法（kb-api-server 容器内）：/srv/kb/.venv/bin/python /tmp/patch_n16_purge.py
"""
import json
import sys
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

SEED_DIR = Path("/srv/kb/scripts/seed_tape_data")
STEP_LABEL = "合并+链接重建"


def main() -> int:
    db = get_sessionmaker()()
    try:
        tapes = db.execute(
            select(Tape).where(Tape.tape_name.like("wiki构建-%"))
        ).scalars().all()
        updated_steps = updated_nodes = skipped = 0
        for t in tapes:
            seed_fp = SEED_DIR / f"{t.tape_name}.json"
            if not seed_fp.exists():
                print(f"[skip] {t.tape_name}: 无种子文件 {seed_fp.name}")
                skipped += 1
                continue
            doc = json.loads(seed_fp.read_text(encoding="utf-8"))
            d = doc.get("draft") or doc
            # 新 n16 code：取种子 steps 里 label 匹配的执行层 code
            new_code = ""
            for s in d.get("steps") or []:
                if (s.get("step_label") or "") == STEP_LABEL:
                    new_code = (s.get("step_config") or {}).get("code") or ""
                    break
            if not new_code:
                print(f"[skip] {t.tape_name}: 种子 steps 无 {STEP_LABEL}")
                skipped += 1
                continue
            # 1) 更新执行层 TapeStep
            steps = db.execute(
                select(TapeStep).where(TapeStep.tape_id == t.id)
            ).scalars().all()
            hit = False
            for st in steps:
                if (st.step_label or "") == STEP_LABEL:
                    cfg = dict(st.step_config or {})
                    if cfg.get("code") == new_code:
                        print(f"[same] {t.tape_name}: n16 code 已一致")
                        hit = True
                        break
                    cfg["code"] = new_code
                    st.step_config = cfg
                    updated_steps += 1
                    hit = True
                    print(f"[step] {t.tape_name}: n16 step_config.code 已更新")
                    break
            if not hit:
                print(f"[warn] {t.tape_name}: 执行层找不到 {STEP_LABEL} 步骤")
                skipped += 1
            # 2) 更新画布 Tape.nodes 里同 label 节点的 code
            nodes = t.nodes or []
            changed = False
            for n in nodes:
                if ((n.get("data") or {}).get("label") or "") == STEP_LABEL:
                    cfg = dict(n.get("data", {}).get("config") or {})
                    if cfg.get("code") == new_code:
                        continue
                    cfg["code"] = new_code
                    n.setdefault("data", {})["config"] = cfg
                    changed = True
            if changed:
                t.nodes = nodes
                updated_nodes += 1
                print(f"[node] {t.tape_name}: 画布 nodes n16 code 已更新")
        db.commit()
        print(f"done: steps={updated_steps} nodes={updated_nodes} skipped={skipped}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
