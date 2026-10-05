"""存量本地文件 → MinIO 迁移（2026-10-05）。

把 MinIO 引入前落在本地磁盘的文件类数据迁到 MinIO 对象存储，
覆盖三类（幂等，可重复执行，不删除本地源文件）：

1. kb_document.storage_path 仍为本地路径的文档原文
   → minio://<bucket>/kb_documents/<kb_id>/<doc_id><ext>
2. modo_sys_file.storage_type='local' 的文件管理存量
   → minio://<bucket>/sys_files/<相对路径>（字段改 storage_type=s3/ds_name=minio/bucket_name）
   （storage_type='minio' 的 285 行是 ds 遗留元数据、文件在 data-synth 的 MinIO，
    不在本机本地磁盘，明确跳过；'s3' 行已在 MinIO，跳过）
3. 本地任务日志 logs/jobs/<job_id>.log（同事 scheduler 通道）
   → minio://<bucket>/logs/<job_id>.log（与 log_sink 同 key，已有对象则按行去重合并）

用法：python scripts/migrate_local_storage_to_minio.py [--dry-run]
要求：在 api-server 容器内运行（有 api 模块 + 本地卷 + MinIO 可达）：
  docker cp scripts/migrate_local_storage_to_minio.py kb-api-server:/tmp/
  docker exec -w /srv/kb -e PYTHONPATH=/srv/kb kb-api-server python /tmp/migrate_local_storage_to_minio.py
"""

from __future__ import annotations

import mimetypes
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_sessionmaker
from api.models.framework import SysFile
from api.models.knowledge import KbDocument
from api.services import storage as storage_svc

DRY_RUN = "--dry-run" in sys.argv

s = get_settings()
BUCKET = s.MINIO_BUCKET or "kb-compilation"
db_url = s.require_database_url()
dbname = db_url.rsplit("/", 1)[-1].split("?")[0] if "@" in db_url else db_url.split("/")[-1]
print(f"DB: {dbname} | MinIO bucket: {BUCKET} | dry_run={DRY_RUN}")
if not dbname.startswith("kb") and dbname not in ("kb_frame",):
    print(f"WARNING: 目标库名不是 kb 前缀（{dbname}），中止")
    sys.exit(1)
if not storage_svc.remote_enabled():
    print("ERROR: MINIO_ENDPOINT 未配置，无法迁移")
    sys.exit(1)

db: Session = get_sessionmaker()()
stats = {"docs": 0, "sys_files": 0, "logs": 0, "skipped_missing": 0}


# ---------------------------------------------------------------------------
# 1) 文档原文
# ---------------------------------------------------------------------------
print("\n== [1/3] kb_document 本地存量 ==")
docs = db.execute(
    select(KbDocument).where(
        (KbDocument.storage_path.is_not(None))
        & (KbDocument.storage_path != "")
        & ~KbDocument.storage_path.like("minio://%")
    )
).scalars().all()
print(f"待迁移文档 {len(docs)} 篇")
for d in docs:
    src = d.storage_path
    p = Path(src)
    if not p.exists():
        print(f"  SKIP 本地文件缺失: {d.id} <- {src}")
        stats["skipped_missing"] += 1
        continue
    ext = (d.file_ext or p.suffix or "").lstrip(".")
    key = f"kb_documents/{d.kb_id}/{p.name}"
    target = storage_svc.build_remote(BUCKET, key)
    if not storage_svc.exists(target):
        if not DRY_RUN:
            data = p.read_bytes()
            storage_svc.put_bytes(target, data, content_type=mimetypes.guess_type(d.file_name or "")[0])
        print(f"  PUT {target} ({p.stat().st_size} B)")
    else:
        print(f"  EXISTS 跳过写入: {target}")
    if not DRY_RUN:
        d.storage_path = target
    stats["docs"] += 1

# ---------------------------------------------------------------------------
# 2) 文件管理存量（storage_type='local'）
# ---------------------------------------------------------------------------
print("\n== [2/3] modo_sys_file local 存量 ==")
rows = db.execute(select(SysFile).where(SysFile.storage_type == "local")).scalars().all()
print(f"待迁移 sys_file {len(rows)} 行")
for f in rows:
    p = Path(s.kb_storage_dir) / "sys_files" / f.storage_path.lstrip("/")
    if not p.exists():
        # 防御:某些历史行可能直接挂在 kb_storage_dir 下（无 sys_files 段）
        p2 = storage_svc.local_path(f.storage_path)
        p = p2 if p2.exists() else p
        print(f"  SKIP 本地文件缺失: {f.id} <- {f.storage_path}")
        stats["skipped_missing"] += 1
        continue
    key = f"sys_files/{f.storage_path.lstrip('/')}"
    target = storage_svc.build_remote(BUCKET, key)
    if not storage_svc.exists(target):
        if not DRY_RUN:
            data = p.read_bytes()
            storage_svc.put_bytes(target, data, content_type=mimetypes.guess_type(f.file_name or "")[0])
        print(f"  PUT {target} ({p.stat().st_size} B)")
    else:
        print(f"  EXISTS 跳过写入: {target}")
    if not DRY_RUN:
        f.storage_type = "s3"
        f.ds_name = "minio"
        f.bucket_name = BUCKET
    stats["sys_files"] += 1

# ---------------------------------------------------------------------------
# 3) 任务日志本地通道 → MinIO logs/<job_id>.log（与 log_sink 同 key，去重合并）
# ---------------------------------------------------------------------------
print("\n== [3/3] 本地任务日志 ==")
logs_dir = Path(s.kb_storage_dir) / "logs" / "jobs"
if logs_dir.exists():
    log_files = sorted(logs_dir.glob("*.log"))
    print(f"本地日志 {len(log_files)} 个 -> key logs/<job_id>.log")
    for p in log_files:
        job_id = p.stem
        target = storage_svc.build_remote(BUCKET, f"logs/{job_id}.log")
        local_lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        existing = b""
        if storage_svc.exists(target):
            try:
                existing = storage_svc.get_bytes(target)
            except Exception:  # noqa: BLE001
                existing = b""
        existing_lines = [ln for ln in existing.decode("utf-8", errors="replace").splitlines() if ln.strip()]
        new_lines = [ln for ln in local_lines if ln not in existing_lines]
        if new_lines:
            if not DRY_RUN:
                merged = ("\n".join(existing_lines + new_lines) + "\n").encode("utf-8")
                storage_svc.put_bytes(target, merged, content_type="text/plain")
            print(f"  MERGE {target} (+{len(new_lines)} 行, 已有 {len(existing_lines)} 行)")
        else:
            print(f"  SKIP 已合并: {target}")
        stats["logs"] += 1
else:
    print("  无本地日志目录（logs/jobs 不存在）")

# ---------------------------------------------------------------------------
if not DRY_RUN:
    db.commit()
print("\n==== 迁移汇总 ====")
print(f"文档: {stats['docs']} | sys_file: {stats['sys_files']} | 日志: {stats['logs']} | 源文件缺失跳过: {stats['skipped_missing']}")
print("本地源文件保留未删（确认无误后可手动清理）")
db.close()