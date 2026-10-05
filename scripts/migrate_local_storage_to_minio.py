"""存量本地文件 → MinIO 迁移（2026-10-05）。

把 MinIO 引入前落在本地磁盘的文件类数据迁到 MinIO 对象存储，
覆盖三类（幂等，可重复执行）：

1. kb_document.storage_path 仍为本地路径的文档原文
   → minio://<bucket>/kb_documents/<kb_id>/<磁盘文件名>
2. modo_sys_file.storage_type='local' 的文件管理存量
   → minio://<bucket>/sys_files/<相对路径>（字段改 storage_type=s3/ds_name=minio/bucket_name）
   （storage_type='minio' 的 285 行是 ds 遗留元数据、文件在 data-synth 的 MinIO，
    不在本机本地磁盘，明确跳过；'s3' 行已在 MinIO，跳过）
3. 本地任务日志 logs/jobs/<job_id>.log（同事 scheduler 通道）
   → minio://<bucket>/logs/<job_id>.log（与 log_sink 同 key，已有对象则按行去重合并）

用法：
  python scripts/migrate_local_storage_to_minio.py [--dry-run] [--prune]
  --prune: 迁移后清理已迁移的本地源文件（逐个校验 MinIO 对象存在且 sha256 一致才删；
           日志先合并最新本地行再删；对应 job 处于非终态(PENDING/RUNNING 等)只合并不删）
要求：在 api-server 容器内运行（有 api 模块 + 本地卷 + MinIO 可达）：
  docker cp scripts/migrate_local_storage_to_minio.py kb-api-server:/tmp/
  docker exec -w /srv/kb -e PYTHONPATH=/srv/kb kb-api-server python /tmp/migrate_local_storage_to_minio.py --prune
"""

from __future__ import annotations

import hashlib
import mimetypes
import sys
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_sessionmaker
from api.models.framework import SysFile
from api.models.knowledge import KbDocument
from api.services import storage as storage_svc

DRY_RUN = "--dry-run" in sys.argv
PRUNE = "--prune" in sys.argv
NON_TERMINAL_STATES = {"PENDING", "RUNNING", "STARTED", "RETRY", "QUEUED", "SUSPENDED"}

s = get_settings()
BUCKET = s.MINIO_BUCKET or "kb-compilation"
db_url = s.require_database_url()
dbname = db_url.rsplit("/", 1)[-1].split("?")[0] if "@" in db_url else db_url.split("/")[-1]
print(f"DB: {dbname} | MinIO bucket: {BUCKET} | dry_run={DRY_RUN} | prune={PRUNE}")
if not dbname.startswith("kb") and dbname not in ("kb_frame",):
    print(f"WARNING: 目标库名不是 kb 前缀（{dbname}），中止")
    sys.exit(1)
if not storage_svc.remote_enabled():
    print("ERROR: MINIO_ENDPOINT 未配置，无法迁移")
    sys.exit(1)

db: Session = get_sessionmaker()()
stats = {"docs": 0, "sys_files": 0, "logs": 0, "skipped_missing": 0}
prune_stats = {"docs": 0, "sys_files": 0, "logs": 0, "kept": 0}


def sha256_b64(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def remote_matches(target: str, local_bytes: bytes) -> bool:
    """MinIO 对象存在且与本地字节 sha256 一致才返回 True。"""
    try:
        if not storage_svc.exists(target):
            return False
        remote = storage_svc.get_bytes(target)
        return sha256_b64(remote) == sha256_b64(local_bytes)
    except Exception:  # noqa: BLE001
        return False


def remote_covers(target: str, local_lines: list[str]) -> bool:
    """日志专用:MinIO 行集完整覆盖本地行集（合并是行去重,字节天然不等,按内容判完整）。"""
    try:
        if not storage_svc.exists(target):
            return False
        remote = storage_svc.get_bytes(target)
        remote_lines = {ln for ln in remote.decode("utf-8", errors="replace").splitlines() if ln.strip()}
        return all(ln in remote_lines for ln in local_lines)
    except Exception:  # noqa: BLE001
        return False


def prune_file(p: Path, target: str, kind: str) -> None:
    """校验后删除本地源文件。"""
    try:
        local_bytes = p.read_bytes()
    except OSError as e:
        print(f"  KEEP {p} (读取失败 {e})")
        prune_stats["kept"] += 1
        return
    if remote_matches(target, local_bytes):
        if not DRY_RUN:
            p.unlink()
        print(f"  REMOVE {p} ({len(local_bytes)} B, sha256 一致)")
        prune_stats[kind] += 1
    else:
        print(f"  KEEP {p} (MinIO 对象缺失或字节不一致)")
        prune_stats["kept"] += 1


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
log_files = sorted(logs_dir.glob("*.log")) if logs_dir.exists() else []
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

# ---------------------------------------------------------------------------
if not DRY_RUN:
    db.commit()
print("\n==== 迁移汇总 ====")
print(f"文档: {stats['docs']} | sys_file: {stats['sys_files']} | 日志: {stats['logs']} | 源文件缺失跳过: {stats['skipped_missing']}")

# ---------------------------------------------------------------------------
# 4) --prune: 清理本地源文件（逐个校验 MinIO 存在 + sha256 一致才删）
# ---------------------------------------------------------------------------
if PRUNE:
    print("\n== [4/4] 清理本地源文件 ==")

    # 文档:扫描 KB_STORAGE_DIR 根下 32-hex 目录（kb_id 形态）内的文件
    root = Path(s.kb_storage_dir)
    if root.exists():
        import re

        kb_re = re.compile(r"^[0-9a-f]{32}$")
        for kb_dir in sorted(root.iterdir()):
            if not kb_dir.is_dir() or not kb_re.match(kb_dir.name):
                continue
            for fp in sorted(kb_dir.iterdir()):
                if not fp.is_file():
                    continue
                target = storage_svc.build_remote(BUCKET, f"kb_documents/{kb_dir.name}/{fp.name}")
                prune_file(fp, target, "docs")

    # sys_files:扫描 {KB_STORAGE_DIR}/sys_files/** 下所有文件
    sf_root = root / "sys_files"
    if sf_root.exists():
        for fp in sorted(sf_root.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(sf_root).as_posix()
            target = storage_svc.build_remote(BUCKET, f"sys_files/{rel}")
            prune_file(fp, target, "sys_files")

    # 日志:先合并最新本地行到 MinIO（防迁移后追加的行丢失），非终态 job 只合并不删
    for p in log_files:
        job_id = p.stem
        target = storage_svc.build_remote(BUCKET, f"logs/{job_id}.log")
        state_row = db.execute(
            text("SELECT state FROM modo_job WHERE id=:i"), {"i": job_id}
        ).first()
        state = (state_row[0] if state_row else "SUCCESS") or "SUCCESS"
        local_lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        existing_lines = []
        try:
            existing = storage_svc.get_bytes(target)
            existing_lines = [ln for ln in existing.decode("utf-8", errors="replace").splitlines() if ln.strip()]
        except Exception:  # noqa: BLE001
            existing_lines = []
        new_lines = [ln for ln in local_lines if ln not in existing_lines]
        if new_lines:
            if not DRY_RUN:
                merged = ("\n".join(existing_lines + new_lines) + "\n").encode("utf-8")
                storage_svc.put_bytes(target, merged, content_type="text/plain")
            print(f"  MERGE(prune) {target} (+{len(new_lines)} 行)")
        if state in NON_TERMINAL_STATES:
            print(f"  KEEP {p} (job state={state} 非终态, 只合并不删)")
            prune_stats["kept"] += 1
            continue
        local_bytes = p.read_bytes()
        # 日志用行覆盖校验(合并去重),非字节一致——内容完整即可删
        if remote_covers(target, local_lines):
            if not DRY_RUN:
                p.unlink()
            print(f"  REMOVE {p} ({len(local_bytes)} B, 行内容完整覆盖)")
            prune_stats["logs"] += 1
        else:
            print(f"  KEEP {p} (MinIO 对象缺失或行集未完整覆盖)")
            prune_stats["kept"] += 1

    print("\n==== 清理汇总 ====")
    print(f"删除: 文档 {prune_stats['docs']} | sys_file {prune_stats['sys_files']} | 日志 {prune_stats['logs']} | 保留: {prune_stats['kept']}")
else:
    print("\n本地源文件保留未删（需要清理时加 --prune 重跑，逐文件校验 MinIO 字节一致后才删）")

db.close()