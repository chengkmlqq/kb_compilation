#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次末收尾：清 wiki 空目录 + 重建 wiki 链接 + 收尾僵尸 run（2026-10-18）

背景：
1. 同名 docx/md/pdf 三格式共用同一个文档根目录，并发清空重跑时后跑的 n05 会
   重新建出空的分类目录，而先跑完的 n16 已经清过 —— 单文档内清不干净，
   需在整批构建结束后统一迭代清理。
2. 清空重跑（n05 clear_file_pages 物理删页）会连带删除 wiki_link，
   若不重建则 wiki 关系图（/wiki/graph，nodes=wiki_page / edges=wiki_link）全空。

用法（容器内）：
  docker cp scripts/cleanup_empty_wiki_folders.py kb-api-server:/tmp/
  docker exec -w /srv/kb kb-api-server python /tmp/cleanup_empty_wiki_folders.py \
      --kb 808ac2b90bb44dafaa014b9e71091730 [--dry-run] [--no-rebuild-links]

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
    ap.add_argument("--no-rebuild-links", action="store_true", help="跳过 wiki 链接重建")
    args = ap.parse_args()

    db = next(get_db())

    # 1) 迭代删空目录
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

    # 2) 重建 wiki 链接（[[slug]] → wiki_link），否则关系图全空
    if args.no_rebuild_links:
        print("链接重建: 已跳过（--no-rebuild-links）")
    elif args.dry_run:
        print("链接重建: dry-run 跳过")
    else:
        from api.services.kb_admin import wiki_rebuild_links

        if args.kb:
            kbs = [args.kb]
        else:
            kbs = [r[0] for r in db.execute(text("select id from kb_datasource")).all()]
        for kb in kbs:
            try:
                res = wiki_rebuild_links(db, kb) or {}
                db.commit()
                print(f"链接重建 [{kb[:8]}]: +{int(res.get('added_links') or 0)} 条")
            except Exception as e:  # noqa: BLE001
                db.rollback()
                print(f"链接重建 [{kb[:8]}] 失败: {e}")

    # 3) 僵尸 running run
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
