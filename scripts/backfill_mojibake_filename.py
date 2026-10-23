#!/usr/bin/env python3
"""存量回填：还原压缩包上传导致的文件名乱码（kb_document.file_name + doc_chunk.meta.source_file）。

背景（2026-10-23）：Windows 打的 zip（GBK 文件名、无 UTF-8 标志）经
upload-archive 自动解包后，文件名被 zipfile 按 CP437 解码成「╓╨╣·」乱码。
本脚本用与 api.routers.kbs._restore_mojibake_filename 相同的还原逻辑
（cp437→gbk 可逆探测）扫描存量数据并回填：

- kb_document.file_name：乱码 → 还原（56 条，2026-10-21 批量导入）
- doc_chunk.meta.source_file：乱码 → 还原（9524 条，解析产物元数据）

幂等：只更新命中特征且可还原的行；还原后与现名相同则跳过。
storage_path 按 doc_id 命名不受影响，无需迁移物理文件。
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


def main() -> None:
    db = get_sessionmaker()()
    try:
        # 1) kb_document.file_name 回填
        doc_fixed = 0
        rows = db.execute(
            text("SELECT id, file_name FROM kb_document WHERE file_name LIKE '%╓%' OR file_name LIKE '%╥%' OR file_name LIKE '%╞%'")
        ).all()
        for doc_id, fn in rows:
            if not _is_mojibake(fn):
                continue
            restored = _restore(fn)
            if restored == fn or not restored:
                continue
            db.execute(
                text("UPDATE kb_document SET file_name=:fn, updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
                {"fn": restored, "id": doc_id},
            )
            doc_fixed += 1
        db.commit()
        print(f"[backfill] kb_document.file_name 回填 {doc_fixed} 条")

        # 2) doc_chunk.meta.source_file 回填（JSON 字段，text() 下是字符串需手动解析）
        #    用无 OFFSET 的游标模式：已更新的行不再匹配 LIKE，每轮 LIMIT 从头取，
        #    避免 OFFSET 分页在 UPDATE 后匹配行减少导致尾部行永远取不到。
        chunk_fixed = 0
        batch = 500
        while True:
            chunk_rows = db.execute(
                text("SELECT id, meta FROM doc_chunk WHERE meta LIKE '%╓%' LIMIT :lim"),
                {"lim": batch},
            ).all()
            if not chunk_rows:
                break
            for cid, meta in chunk_rows:
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
                chunk_fixed += 1
            db.commit()
            print(f"[backfill] doc_chunk 游标轮次，累计回填 {chunk_fixed} 条")
        db.commit()
        print(f"[backfill] 完成：文档 {doc_fixed} 条 + 切片元数据 {chunk_fixed} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()