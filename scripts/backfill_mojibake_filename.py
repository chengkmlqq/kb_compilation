#!/usr/bin/env python3
"""存量回填：还原压缩包上传导致的文件名乱码（2026-10-23，全表覆盖版）。

背景：Windows 打的 zip（GBK 文件名、无 UTF-8 标志）经 upload-archive 自动
解包后，文件名被 zipfile 按 CP437 解码成「╓╨╣·」类 box-drawing 乱码。
本脚本用与 api.routers.kbs._restore_mojibake_filename 相同的还原逻辑
（cp437→gbk 可逆探测，U+2500–U+257F 特征）扫描并回填全部受影响数据：

- kb_document.file_name：乱码 → 还原
- doc_chunk.meta（source_file 字段）：乱码 → 还原
- wiki_page.title / slug：乱码 → 还原
- wiki_page.source_refs（JSON，doc_id|file_name 列表）：file_name 段乱码 → 还原
- kb_quality_doc_eval.file_name：乱码 → 还原

storage_path 按 doc_id 命名不受影响，无需迁移物理文件。
幂等：只更新命中特征且可还原的行；还原后与现名相同则跳过。

用法（api-server 容器内）：
  docker cp scripts/backfill_mojibake_filename.py kb-api-server:/tmp/
  docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/backfill_mojibake_filename.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/srv/kb")

from sqlalchemy import text  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402

# U+2500-U+257F box-drawing 区 REGEXP（utf8mb4 按字符区间匹配）
_BOX_RE = "[╀-╿]"
_BATCH = 500


def _restore(name: str) -> str:
    """与 api/routers/kbs.py 同逻辑的 mojibake 还原（CP437→GBK 可逆探测）。"""
    if not name:
        return name
    if any("\u2500" <= ch <= "\u257f" for ch in name):
        try:
            raw = name.encode("cp437")
            restored = raw.decode("gbk")
            if restored and "\ufffd" not in restored:
                return restored
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _is_mojibake(name: str) -> bool:
    return any("\u2500" <= ch <= "\u257f" for ch in name)


def _restore_doc_name_field(db, table: str, column: str, where_sql: str) -> int:
    """通用列回填：SELECT 乱码行 → 单值还原 → UPDATE。"""
    fixed = 0
    rows = db.execute(
        text(f"SELECT id, {column} FROM {table} WHERE {column} REGEXP :pat"),
        {"pat": _BOX_RE},
    ).all()
    for rid, val in rows:
        if not val or not _is_mojibake(val):
            continue
        restored = _restore(val)
        if restored == val or not restored:
            continue
        db.execute(
            text(f"UPDATE {table} SET {column}=:v WHERE id=:id"),
            {"v": restored, "id": rid},
        )
        fixed += 1
    db.commit()
    return fixed


def _backfill_source_refs(db) -> int:
    """wiki_page.source_refs（JSON ["doc_id|file_name", ...]）：还原 file_name 段。"""
    fixed = 0
    while True:
        rows = db.execute(
            text("SELECT id, source_refs FROM wiki_page WHERE source_refs REGEXP :pat LIMIT :lim"),
            {"pat": _BOX_RE, "lim": _BATCH},
        ).all()
        if not rows:
            break
        for pid, refs in rows:
            try:
                arr = json.loads(refs) if isinstance(refs, str) else (refs or [])
            except (TypeError, ValueError):
                continue
            changed = False
            out = []
            for item in arr or []:
                s = str(item)
                if "|" in s and _is_mojibake(s.split("|", 1)[1]):
                    doc_id, fname = s.split("|", 1)
                    rname = _restore(fname)
                    if rname and rname != fname:
                        out.append(f"{doc_id}|{rname}")
                        changed = True
                        continue
                out.append(s)
            if changed:
                db.execute(
                    text("UPDATE wiki_page SET source_refs=:v WHERE id=:id"),
                    {"v": json.dumps(out, ensure_ascii=False), "id": pid},
                )
                fixed += 1
        db.commit()
        print(f"[backfill] source_refs 游标轮次，累计 {fixed} 条")
    return fixed


def _backfill_chunk_meta(db) -> int:
    """doc_chunk.meta（JSON）：还原 source_file 字段（游标模式，无 OFFSET）。"""
    fixed = 0
    while True:
        rows = db.execute(
            text("SELECT id, meta FROM doc_chunk WHERE meta REGEXP :pat LIMIT :lim"),
            {"pat": _BOX_RE, "lim": _BATCH},
        ).all()
        if not rows:
            break
        for cid, meta in rows:
            try:
                m = json.loads(meta) if isinstance(meta, str) else (meta or {})
            except (TypeError, ValueError):
                continue
            sf = m.get("source_file")
            if not sf or not _is_mojibake(sf):
                continue
            restored = _restore(sf)
            if restored == sf or not restored:
                continue
            m["source_file"] = restored
            db.execute(
                text("UPDATE doc_chunk SET meta=:meta WHERE id=:id"),
                {"meta": json.dumps(m, ensure_ascii=False), "id": cid},
            )
            fixed += 1
        db.commit()
        print(f"[backfill] doc_chunk 游标轮次，累计 {fixed} 条")
    return fixed


def main() -> None:
    db = get_sessionmaker()()
    try:
        doc_fixed = _restore_doc_name_field(db, "kb_document", "file_name", "")
        print(f"[backfill] kb_document.file_name 回填 {doc_fixed} 条")

        chunk_fixed = _backfill_chunk_meta(db)
        print(f"[backfill] doc_chunk.meta.source_file 回填 {chunk_fixed} 条")

        wiki_title_fixed = _restore_doc_name_field(db, "wiki_page", "title", "")
        wiki_slug_fixed = _restore_doc_name_field(db, "wiki_page", "slug", "")
        print(f"[backfill] wiki_page.title 回填 {wiki_title_fixed} 条 / slug {wiki_slug_fixed} 条")

        refs_fixed = _backfill_source_refs(db)
        print(f"[backfill] wiki_page.source_refs 回填 {refs_fixed} 条")

        quality_fixed = _restore_doc_name_field(db, "kb_quality_doc_eval", "file_name", "")
        print(f"[backfill] kb_quality_doc_eval.file_name 回填 {quality_fixed} 条")

        print(f"[backfill] 完成：文档 {doc_fixed} + 切片 {chunk_fixed} + wiki标题 {wiki_title_fixed}"
              f" + wiki slug {wiki_slug_fixed} + refs {refs_fixed} + 评估明细 {quality_fixed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()