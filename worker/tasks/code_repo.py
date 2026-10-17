"""Code repository sync Celery task — 代码仓库知识库拉取（2026-10）。

KbCodeRepoSyncTask：创建代码仓库知识库后 / 手动触发 / 定时触发时执行——

1. git 浅克隆（--depth 1，凭证注入：token / ssh_key / user_pass）
2. 遍历工作树文件，分拣（跳过 .git/隐藏/垃圾/二进制/超大/非白名单格式）
3. 仓库目录结构 → 知识库文档目录树（doc_folder，同级同名复用）
4. 文件字节 → MinIO（code_repos/{kb_id}/{commit}/{relpath}）→ kb_document
   (source_type=repo, folder_id 按目录树, repo_commit_sha)
5. 每个文件入队 KbDocumentProcessTask（解析 → 摘要编排 → 构建引擎编排，
   与上传文档完全同一条链路，编排无需改造）
6. 增量：DB 已有 relpath → 更新 storage_path(新 commit) + 重置 PENDING 重解析；
   工作树已删除 → delete_document 级联清理
7. 回写 kb_datasource.repo_commit_sha / repo_pull_status / repo_last_pull_at

Task wiring: TASK_CLASS_REGISTRY["KbCodeRepoSyncTask"] = handler(job_id, task_params)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from api.lib.crypto import aes_decrypt
from api.models.framework import Job

logger = logging.getLogger(__name__)

TASK_CLASS_CODE_REPO = "KbCodeRepoSyncTask"

# 默认跳过目录/文件（与 docreader/parser/code_parser.py CODE_SKIP_DIRS/FILES 同源，
# 避免把依赖目录/构建产物灌进知识库）
CODE_SKIP_DIRS = {
    ".git", "node_modules", "target", "dist", "build", "out",
    ".idea", ".vscode", "__pycache__", ".venv", "venv", "env",
    "vendor", ".next", ".gradle", "coverage", ".pytest_cache",
    ".mvn", ".cargo",
}
CODE_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"}

_MAX_FILE_BYTES = int(os.getenv("MAX_REPO_FILE_BYTES", "10485760"))  # 10MB 兜底
_GIT_TIMEOUT = int(os.getenv("GIT_CLONE_TIMEOUT", "600"))


# --------------------------------------------------------------------------- #
# git 执行
# --------------------------------------------------------------------------- #
def _build_clone_url(repo_url: str, auth: dict) -> str:
    """按凭证类型构造 git 克隆 URL（token 内嵌 / 其余原样）。

    token 方式：https://<token>@host/...（仅进程内拼接，不落日志）。
    ssh_key / user_pass 走 GIT_SSH_COMMAND / GIT_ASKPASS，URL 不变。
    """
    url = repo_url.strip()
    if not url:
        raise ValueError("repo_url 为空")
    auth_type = auth.get("type", "none")
    if auth_type == "token":
        token = str(auth.get("token") or "").strip()
        if not token:
            raise ValueError("token 为空")
        # 仅在 http/https 上内嵌 token
        if url.startswith("http://") or url.startswith("https://"):
            scheme, _, rest = url.partition("://")
            host_part, _, path = rest.partition("/")
            # 去掉 URL 里已存在的用户名（user:pass@ 或 user@）
            if "@" in host_part:
                host_part = host_part.rsplit("@", 1)[1]
            return f"{scheme}://{token}@{host_part}/{path}"
    return url


def _git_clone(repo_url: str, branch: str, dest: str, auth: dict) -> None:
    """浅克隆仓库到 dest。凭证经 env 注入，失败抛异常（含 stderr 摘要）。"""
    url = _build_clone_url(repo_url, auth)
    cmd = ["git", "clone", "--depth", "1", "--single-branch"]
    if branch and branch.strip():
        cmd += ["--branch", branch.strip()]
    cmd += [url, dest]

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # 凭证缺失时立即失败而非交互挂起
    tmp_ssh_key: str | None = None
    tmp_askpass: str | None = None
    auth_type = auth.get("type", "none")
    try:
        if auth_type == "ssh_key":
            key_body = str(auth.get("key") or "").strip()
            if not key_body:
                raise ValueError("ssh_key 为空")
            fd, tmp_ssh_key = tempfile.mkstemp(prefix="kb-git-key-")
            with os.fdopen(fd, "w") as f:
                f.write(key_body + "\n")
            os.chmod(tmp_ssh_key, 0o600)
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {tmp_ssh_key} -o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes"
            )
        elif auth_type == "user_pass":
            user = str(auth.get("user") or "").strip()
            pwd = str(auth.get("password") or "").strip()
            if not user or not pwd:
                raise ValueError("user_pass 用户名/密码不完整")
            fd, tmp_askpass = tempfile.mkstemp(prefix="kb-git-askpass-")
            with os.fdopen(fd, "w") as f:
                # GIT_ASKPASS 脚本：第一次调用输出用户名，第二次输出密码
                f.write(
                    "#!/bin/sh\n"
                    'case "$1" in\n'
                    f'  Username*) echo "{user}" ;;\n'
                    f'  Password*) echo "{pwd}" ;;\n'
                    "  *) exit 0 ;;\n"
                    "esac\n"
                )
            os.chmod(tmp_askpass, 0o700)
            env["GIT_ASKPASS"] = tmp_askpass

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=env,
        )
        if proc.returncode != 0:
            # 截断 stderr 前 500 字符，凭证不得落日志
            err = (proc.stderr or "").strip()[-500:]
            raise RuntimeError(f"git clone 失败: {err}")
    finally:
        if tmp_ssh_key and os.path.exists(tmp_ssh_key):
            os.unlink(tmp_ssh_key)
        if tmp_askpass and os.path.exists(tmp_askpass):
            os.unlink(tmp_askpass)


def _git_head_commit(dest: str) -> str:
    proc = subprocess.run(
        ["git", "-C", dest, "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("git rev-parse HEAD 失败")
    return (proc.stdout or "").strip()


# --------------------------------------------------------------------------- #
# 工作树分拣
# --------------------------------------------------------------------------- #
def _walk_repo(dest: str) -> list[tuple[str, Path]]:
    """遍历工作树，返回 [(relpath, abs_path)]，已过滤隐藏/垃圾/跳过目录。"""
    found: list[tuple[str, Path]] = []
    root = Path(dest)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in CODE_SKIP_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if fn in CODE_SKIP_FILES or fn.startswith("."):
                continue
            abs_path = Path(dirpath) / fn
            rel = str(abs_path.relative_to(root)).replace(os.sep, "/")
            found.append((rel, abs_path))
    found.sort(key=lambda t: t[0])
    return found


def _is_binary(path: Path) -> bool:
    """快速二进制判定：读前 8000 字节含 NUL。"""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(8000)
    except OSError:
        return True


def repo_rel_dir(rel: str) -> str:
    """文件相对路径 → 仓库内目录（根目录文件返回 ""，避免建 "." 目录）。"""
    parent = str(Path(rel).parent).replace("\\", "/")
    return "" if parent in ("", ".") else parent


# --------------------------------------------------------------------------- #
# 库内落库
# --------------------------------------------------------------------------- #
def _decrypt_auth(kb) -> dict:
    """解密 repo_auth_secret（AES）→ auth dict；空返回 {"type": "none"}。"""
    secret = getattr(kb, "repo_auth_secret", None) or ""
    if not secret:
        return {"type": "none"}
    try:
        plain = aes_decrypt(secret)
        parsed = json.loads(plain)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb %s: repo_auth_secret 解密失败: %s", kb.id, exc)
    return {"type": "none"}


def _ensure_folder(db, kb_id: str, rel_dir: str) -> str:
    """按仓库相对目录逐级创建 doc_folder（同级同名复用），返回末级 folder_id。

    rel_dir=""（仓库根）返回 ""。与目录上传 ensureFolder 语义一致。
    """
    from api.models.knowledge import DocFolder

    rel_dir = (rel_dir or "").replace("\\", "/").strip("/")
    if not rel_dir or rel_dir == ".":
        return ""
    parts = [p for p in rel_dir.split("/") if p]
    parent_id = ""
    current = ""
    for name in parts:
        row = db.execute(
            select(DocFolder).where(
                DocFolder.kb_id == kb_id,
                DocFolder.parent_id == parent_id,
                DocFolder.name == name,
            )
        ).scalars().first()
        if row is None:
            row = DocFolder(
                id=uuid.uuid4().hex,
                kb_id=kb_id,
                name=name,
                parent_id=parent_id,
                created_by="repo-sync",
            )
            db.add(row)
            db.flush()
        current = row.id
        parent_id = current
    db.commit()
    return current


def _enqueue_doc_process(kb_id: str, document_id: str, cookie: str) -> None:
    """建 modo_job 行（DOC_{id}）+ 投 Celery 解析（复用上传链同款模式）。"""
    from worker.celery_app import celery_app

    job_id = f"DOC_{document_id}"
    task_params = json.dumps(
        {"kbId": kb_id, "documentId": document_id, "cookie": cookie},
        ensure_ascii=False,
    )
    from api.db import get_sessionmaker

    framework_db = get_sessionmaker()()
    try:
        existing = framework_db.get(Job, job_id)
        if existing is None:
            framework_db.add(
                Job(
                    id=job_id,
                    task_id=document_id,
                    task_class="KbDocumentProcessTask",
                    queue_name="default",
                    task_params=task_params,
                    trigger_type="API",
                    state="PENDING",
                )
            )
        else:
            existing.task_params = task_params
            existing.state = "PENDING"
            existing.start_time = None
            existing.end_time = None
            existing.duration_ms = None
            existing.error_message = None
        framework_db.commit()
    finally:
        framework_db.close()
    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job",
        args=[job_id],
        task_id=job_id,
        queue="default",
    )


def _sync_repo(job_id: str, task_params: dict) -> dict:
    """代码仓库同步主流程。"""
    from api.db import get_sessionmaker
    from api.models.knowledge import KbDatasource, KbDocument
    from api.services import storage as storage_svc
    from api.services.kb_admin import create_document, delete_document
    from api.services.parser_registry import is_supported_ext

    kb_id = str(task_params.get("kbId") or task_params.get("kb_id") or "").strip()
    cookie = str(task_params.get("cookie") or "")
    if not kb_id:
        return {"success": False, "error": "task_params must include kbId"}

    db = get_sessionmaker()()
    tmpdir: str | None = None
    try:
        kb = db.execute(
            select(KbDatasource).where(KbDatasource.id == kb_id)
        ).scalars().first()
        if not kb:
            return {"success": False, "error": f"知识库不存在: {kb_id}"}
        if (getattr(kb, "repo_type", "") or "") != "git":
            return {"success": False, "error": f"知识库不是代码仓库类型: {kb_id}"}

        repo_url = getattr(kb, "repo_url", None) or ""
        if not repo_url:
            return {"success": False, "error": "知识库未配置代码仓库地址"}

        kb.repo_pull_status = "pulling"
        kb.repo_pull_error = None
        db.commit()

        # 1. 浅克隆到临时目录
        tmpdir = tempfile.mkdtemp(prefix=f"kb-repo-{kb_id}-")
        auth = _decrypt_auth(kb)
        branch = getattr(kb, "repo_branch", None) or ""
        _git_clone(repo_url, branch, tmpdir, auth)
        commit = _git_head_commit(tmpdir)

        # 2. 分拣工作树
        files = _walk_repo(tmpdir)
        bucket = get_settings_bucket()

        # 3. 目录树 + 文件落库
        existing_docs: dict[str, KbDocument] = {}
        rows = db.execute(
            select(KbDocument).where(
                KbDocument.kb_id == kb_id,
                KbDocument.source_type == "repo",
            )
        ).scalars().all()
        for d in rows:
            existing_docs[d.file_name] = d

        seen: set[str] = set()
        created = 0
        refreshed = 0
        skipped = 0
        for rel, abs_path in files:
            ext = Path(rel).suffix.lower().lstrip(".")
            if not is_supported_ext(ext):
                skipped += 1
                continue
            if _is_binary(abs_path):
                skipped += 1
                continue
            try:
                size = abs_path.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size <= 0 or size > _MAX_FILE_BYTES:
                skipped += 1
                continue
            # 通过分拣才算入同步集合（repo_file_count = 落库文档数）
            seen.add(rel)

            folder_id = _ensure_folder(db, kb_id, repo_rel_dir(rel))
            rel_key = f"code_repos/{kb_id}/{commit}/{rel}"
            storage_path = storage_svc.build_remote(bucket, rel_key)
            storage_svc.put_bytes(storage_path, abs_path.read_bytes())

            existing = existing_docs.get(rel)
            if existing is None:
                create_document(
                    db, kb_id,
                    file_name=rel,
                    file_ext=ext,
                    file_size=size,
                    storage_path=storage_path,
                    folder_id=folder_id,
                    source_type="repo",
                    repo_commit_sha=commit,
                    created_by=kb.created_by,
                )
                created += 1
            else:
                # 变更：更新 storage_path（新 commit 前缀）+ 重置重解析
                existing.storage_path = storage_path
                existing.file_size = size
                existing.folder_id = folder_id
                existing.repo_commit_sha = commit
                existing.parse_state = "PENDING"
                existing.parse_error = None
                db.commit()
                refreshed += 1

        # 4. 增量删除：DB 有但工作树已无 → 级联清理
        removed = 0
        for rel, d in existing_docs.items():
            if rel in seen:
                continue
            try:
                delete_document(db, kb_id, d.id)
                removed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("repo sync delete doc=%s failed: %s", d.id, exc)

        # 5. 入队解析（新增 + 变更全部重新解析；已有文档覆盖旧行状态）
        doc_rows = db.execute(
            select(KbDocument).where(
                KbDocument.kb_id == kb_id,
                KbDocument.source_type == "repo",
            )
        ).scalars().all()
        queued = 0
        for d in doc_rows:
            if d.file_name not in seen:
                continue
            _enqueue_doc_process(kb_id, d.id, cookie)
            queued += 1

        # 6. 回写状态
        kb.repo_commit_sha = commit
        kb.repo_file_count = len(seen)
        kb.repo_pull_status = "success"
        kb.repo_pull_error = None
        from datetime import datetime

        kb.repo_last_pull_at = datetime.utcnow()
        db.commit()

        return {
            "success": True,
            "data": {
                "kb_id": kb_id,
                "commit": commit,
                "created": created,
                "refreshed": refreshed,
                "removed": removed,
                "skipped": skipped,
                "queued": queued,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("code repo sync failed kb=%s", kb_id)
        try:
            kb = db.execute(
                select(KbDatasource).where(KbDatasource.id == kb_id)
            ).scalars().first()
            if kb is not None:
                kb.repo_pull_status = "failed"
                kb.repo_pull_error = str(exc)[-500:]
                db.commit()
        except Exception:  # noqa: BLE001
            pass
        return {"success": False, "error": f"代码仓库同步失败: {exc}"}
    finally:
        db.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def get_settings_bucket() -> str:
    """MinIO bucket（默认 kb-compilation，与 storage.resolve_new_path 同源）。"""
    from api.config import get_settings

    return get_settings().MINIO_BUCKET or "kb-compilation"


# --------------------------------------------------------------------------- #
# Scheduler registry handler
# --------------------------------------------------------------------------- #
def _handle_code_repo_sync(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    return _sync_repo(job_id, params)


def register_task_handlers() -> None:
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY

    TASK_CLASS_REGISTRY[TASK_CLASS_CODE_REPO] = _handle_code_repo_sync


register_task_handlers()
