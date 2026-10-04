#!/usr/bin/env python
"""技能包迁移到 MinIO 对象存储（2026-10-04）。

把 kb_skill.package_zip（历史 BLOB）里的技能包写到对象存储，
回填 storage_path 后置空 package_zip ——瘦身 DB（binlog/备份不再携带 MB 级 BLOB）。

幂等：只有「有 package_zip 且无 storage_path」的行会被处理；可重复执行。
用法（compose 内，KB_DATABASE_URL 已注入）:
  docker cp scripts/migrate_skill_to_minio.py kb-api-server:/tmp/
  docker exec kb-api-server sh -c "cd /srv/kb && PYTHONPATH=/srv/kb python /tmp/migrate_skill_to_minio.py"
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/srv/kb")

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.mcp_skill import KbSkill
from api.services import storage
from api.services.skills import skill_zip_key


def main() -> None:
    if not storage.remote_enabled():
        print("MINIO_ENDPOINT 未配置，跳过 MinIO 迁移（保持本地存储）")
        return
    db = get_sessionmaker()()
    try:
        rows = db.execute(
            select(KbSkill).where(KbSkill.package_zip.isnot(None))
        ).scalars().all()
        done = kept = 0
        for m in rows:
            if m.storage_path:
                kept += 1
                continue
            data = bytes(m.package_zip)
            path = f"minio://{storage.get_settings().MINIO_BUCKET or 'kb-compilation'}/{skill_zip_key(m.id)}"
            storage.put_bytes(path, data, content_type="application/zip")
            m.storage_path = path
            m.package_zip = None  # 迁移后置空：DB 不再持有大 BLOB
            db.commit()
            done += 1
            print(f"  [{m.scope}] {m.name} -> {path} ({len(data)}B)")
        print(f"迁移完成: 新迁移 {done} 个技能，已迁移跳过 {kept} 个")
    finally:
        db.close()


if __name__ == "__main__":
    main()