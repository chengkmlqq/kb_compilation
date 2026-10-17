#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次末收尾：迭代清理 wiki 空目录 + 收尾僵尸 running run（2026-10-17）

背景：同名 docx/md/pdf 三格式共用同一个文档根目录，并发清空重跑时
后跑的 n05 会重新建出空的分类目录，而先跑完的 n16 已经清过 —— 单文档
内的 n16 清不干净，需要在整批构建结束后统一收尾。

用法（容器内）：
  docker cp scripts/cleanup_empty_wiki_folders.py kb-api-server:/tmp/
  docker exec -w /srv/kb kb-api-server python /tmp/cleanup_empty_wiki_folders.py \
      --kb 808ac2b90bb44dafaa014b9e71091730 [--dry-run]

不带 --kb 则处理全部知识库。
"""
from __future__ import annotations

import argparse

from sqlalchemy import bindparam, text

from api.db import get_db

EMPTY_FOLDER_SQL = """
select f.id from wiki_folder f
where (:kb = '' or f.kb_id = :kb)
  and not exists(select 1 from wiki_page p where p.folder_id = f.id)
  and not exists(select 1 from wiki_page_folder pf where pf.folder_id = f.id)
  and not exists(select 1 from wiki_folder c where c.parent_id = f.id)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default="", help="知识库 ID，留空=全部")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--stale-minutes", type=int, default=40)
    args = ap.parse_args()

    db = next(get_db())

    rounds = deleted = 0
    for _ in range(args.max_rounds):
        ids = [r[0] for r in db.execute(text(EMPTY_FOLDER_SQL), {"kb": args.kb}).all()]
        if not ids:
            break
        if args.dry_run:
            print(f"[dry-run] 第 {rounds + 1} 轮可删 {len(ids)} 个空目录")
            break
        db.execute(
            text("delete from wiki_folder where id in :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
        db.commit()
        rounds += 1
        deleted += len(ids)
    print(f"空目录清理: {rounds} 轮, 删除 {deleted} 个")

    stale = db.execute(
        text("""select id from kb_tape_run
                where status='running'
                  and created_at < date_sub(now(), interval :m minute)"""),
        {"m": args.stale_minutes},
    ).all()
    if not args.dry_run:
        for (rid,) in stale:
            db.execute(
                text("""update kb_tape_run
                        set status='failed',
                            error=coalesce(error,'僵尸占位：超时未推进，已人工收尾')
                        where id=:i"""),
                {"i": rid},
            )
        db.commit()
    print(f"僵尸 running run: {len(stale)} 个" + ("（dry-run 未改）" if args.dry_run else " 已收尾"))

    print("剩余空目录:",
          db.execute(text(EMPTY_FOLDER_SQL.replace("select f.id", "select count(*)")), {"kb": args.kb}).scalar())


if __name__ == "__main__":
    main()
