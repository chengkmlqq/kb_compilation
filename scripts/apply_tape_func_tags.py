"""给现有编排打「功能标签」（tape_type：summary 摘要 / kb_build 知识构建），幂等可重放。

背景（2026-10-15）：编排管理页与「知识库构建配置」（构建引擎下拉）需要按功能内容
区分编排——摘要编排（摘要-Stuff/Map-Reduce/Refine）与知识构建编排（wiki构建-* 领域版、
wiki构建-基础原子版、表构建Wiki-基础原子版）是两类主要功能。tape_type 字段自迁移起
存在（默认 general），此处按命名规则为存量编排打标；新建编排由 seed_framework_db.py
种子直接写入正确标签。不命中规则的编排保持原值（如「质量评估-QA」维持 general）。

命名规则（与 seed 打标口径一致，重放幂等）：
  - tape_name LIKE '摘要-%'            → summary（摘要）
  - tape_name LIKE 'wiki构建-%'        → kb_build（知识构建）
  - tape_name IN ('表构建Wiki-基础原子版','表构建Wiki') → kb_build

用法：docker cp scripts/apply_tape_func_tags.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_tape_func_tags.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape


def main() -> None:
    db = get_sessionmaker()()
    try:
        rows = db.execute(select(Tape)).scalars().all()
        updated = skipped = 0
        for t in rows:
            name = t.tape_name or ""
            if name.startswith("摘要-"):
                target = "summary"
            elif name.startswith("wiki构建-") or name in ("表构建Wiki-基础原子版", "表构建Wiki"):
                target = "kb_build"
            else:
                skipped += 1
                continue
            if t.tape_type != target:
                t.tape_type = target
                updated += 1
                print(f"[update] {name} → tape_type={target}")
            else:
                skipped += 1
        db.commit()
        total = len(rows)
        print(f"完成: 共 {total} 个编排，更新 {updated} 个，无需变更 {skipped} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()
