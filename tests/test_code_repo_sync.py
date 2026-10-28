"""代码仓库同步任务测试（KbCodeRepoSyncTask，2026-10）。

覆盖 _sync_repo 主流程（mock git 命令与对象存储）：
- 非 repo 知识库 / 无地址 → 失败且不落库
- 首次同步：目录树镜像（doc_folder 层级）+ 文件落库（storage_path 前缀）
  + source_type=repo + repo_commit_sha
- 增量：新 commit 下文件变更 → storage_path 更新 + PENDING 重解析
- 删除：工作树无 → delete_document 级联
- 失败：clone 抛错 → repo_pull_status=failed + error 写入
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base
from api.models.knowledge import DocFolder, KbDatasource, KbDocument
from api.services import storage as storage_svc
from api.services.kb_admin import doc_create_folder
from conftest import create_test_kb as create_kb
from worker.tasks import code_repo as cr


@pytest.fixture()
def repo_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def _build_worktree(dest: Path, version: int) -> None:
    """构造两版"仓库工作树"（模拟 clone 产物）。"""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "src").mkdir(exist_ok=True)
    if version == 1:
        (dest / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        (dest / "README.md").write_text("# demo\n", encoding="utf-8")
        (dest / "src" / "skip.bin").write_bytes(b"\x00\x01\x02")
        (dest / "src" / "notes.xyz").write_text("x", encoding="utf-8")
        (dest / "src" / ".hidden.py").write_text("y", encoding="utf-8")
        (dest / ".DS_Store").write_text("z", encoding="utf-8")
    else:
        (dest / "src" / "main.py").write_text("def main():\n    return 2\n", encoding="utf-8")
        (dest / "util.go").write_text("package main\n", encoding="utf-8")
        # README.md 已删除


@pytest.fixture()
def repo_env(repo_db, monkeypatch, tmp_path):
    """patch git 命令 + 对象存储 + 入队，全部可观测。"""
    worktree = tmp_path / "wt1"
    _build_worktree(worktree, 1)

    def fake_clone(url, branch, dest, auth):
        shutil.copytree(worktree, dest, dirs_exist_ok=True) if not Path(dest).exists() \
            else shutil.copytree(worktree, dest, dirs_exist_ok=True)

    monkeypatch.setattr(cr, "_git_clone", fake_clone)
    monkeypatch.setattr(cr, "_git_head_commit", lambda dest: "a1b2c3d4e5f67890")
    written: list[str] = []
    monkeypatch.setattr(
        storage_svc, "put_bytes",
        lambda sp, data, content_type=None: written.append(sp) or sp,
    )
    monkeypatch.setattr(cr, "get_settings_bucket", lambda: "kb-compilation")
    monkeypatch.setattr(cr, "_enqueue_doc_process", lambda kb_id, doc_id, cookie: None)
    queued: list[str] = []

    def _fake_enqueue(kb_id, doc_id, cookie):
        queued.append(doc_id)

    monkeypatch.setattr(cr, "_enqueue_doc_process", _fake_enqueue)
    import api.db as _db_mod

    monkeypatch.setattr(
        _db_mod, "get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: repo_db})(),
    )
    yield types_ns(written=written, queued=queued, db=repo_db, worktree=worktree,
                   monkeypatch=monkeypatch, tmp_path=tmp_path)


def types_ns(**kw):
    return type("NS", (), kw)()


def _mk_repo_kb(db, **overrides) -> KbDatasource:
    fields = dict(
        name="代码库测试",
        repo_type="git",
        repo_url="https://git.example.com/team/demo.git",
        repo_auth_type="none",
    )
    fields.update(overrides)
    return create_kb(
        db,
        name=fields.pop("name"),
        repo_type=fields.pop("repo_type", ""),
        repo_url=fields.pop("repo_url", ""),
        repo_branch=fields.pop("repo_branch", ""),
        repo_auth_type=fields.pop("repo_auth_type", "none"),
        repo_auth_secret=fields.pop("repo_auth_secret", ""),
    )


def test_sync_rejects_non_git_kb(repo_env):
    kb = create_kb(repo_env.db, "普通库")
    result = cr._sync_repo("J1", {"kbId": kb.id})
    assert result["success"] is False
    assert "不是代码仓库类型" in result["error"]


def test_sync_rejects_missing_url(repo_env):
    # 绕过 create_kb 的 url 校验：直接构造无地址的 git KB 行
    kb = KbDatasource(
        id="kb-nourl", name="无地址", repo_type="git", repo_url="",
        repo_auth_type="none", indexing_strategy={}, state="1",
    )
    repo_env.db.add(kb)
    repo_env.db.commit()
    result = cr._sync_repo("J1", {"kbId": kb.id})
    assert result["success"] is False
    assert "未配置代码仓库地址" in result["error"]


def test_first_sync_creates_folders_and_docs(repo_env):
    kb = _mk_repo_kb(repo_env.db)
    result = cr._sync_repo("J1", {"kbId": kb.id})
    assert result["success"], result

    folders = repo_env.db.execute(select(DocFolder)).scalars().all()
    assert [f.name for f in folders] == ["src"]

    docs = repo_env.db.execute(
        select(KbDocument).where(KbDocument.source_type == "repo")
    ).scalars().all()
    names = sorted(d.file_name for d in docs)
    assert names == ["README.md", "src/main.py"]

    main = next(d for d in docs if d.file_name == "src/main.py")
    assert main.repo_commit_sha == "a1b2c3d4e5f67890"
    assert main.parse_state == "PENDING"
    assert main.storage_path.startswith("minio://kb-compilation/")
    assert "code_repos/" in main.storage_path
    assert "/a1b2c3d4e5f67890/src/main.py" in main.storage_path
    src_folder = next(f for f in folders if f.name == "src")
    assert main.folder_id == src_folder.id
    readme = next(d for d in docs if d.file_name == "README.md")
    assert readme.folder_id == ""

    kb_after = repo_env.db.get(KbDatasource, kb.id)
    assert kb_after.repo_commit_sha == "a1b2c3d4e5f67890"
    assert kb_after.repo_pull_status == "success"
    assert kb_after.repo_file_count == 2
    assert len(repo_env.written) == 2
    assert len(repo_env.queued) == 2


def test_sync_incremental_update_and_delete(repo_env):
    kb = _mk_repo_kb(repo_env.db)
    cr._sync_repo("J1", {"kbId": kb.id})
    assert len(repo_env.queued) == 2  # 首轮入队 2

    # 第二轮：新 commit + v2 工作树（main.py 变更、util.go 新增、README 删除）
    new_commit = "f00d123456789abc"
    worktree2 = repo_env.tmp_path / "wt2"
    _build_worktree(worktree2, 2)
    repo_env.monkeypatch.setattr(
        cr, "_git_clone",
        lambda url, branch, dest, auth: shutil.copytree(
            worktree2, dest, dirs_exist_ok=True
        ),
    )
    repo_env.monkeypatch.setattr(cr, "_git_head_commit", lambda dest: new_commit)

    result = cr._sync_repo("J2", {"kbId": kb.id})
    assert result["success"], result

    docs = repo_env.db.execute(
        select(KbDocument).where(KbDocument.source_type == "repo")
    ).scalars().all()
    names = sorted(d.file_name for d in docs)
    assert "README.md" not in names  # 已删除
    assert "util.go" in names  # 新增
    assert "src/main.py" in names  # 保留并重解析
    main = next(d for d in docs if d.file_name == "src/main.py")
    assert main.repo_commit_sha == new_commit
    assert "/f00d123456789abc/src/main.py" in main.storage_path
    # 共 2 个文档（main.py + util.go），README 被删 → 入队 2
    assert len(repo_env.queued) == 4


def test_sync_failure_marks_failed(repo_env, monkeypatch):
    kb = _mk_repo_kb(repo_env.db)
    monkeypatch.setattr(
        cr, "_git_clone",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("clone boom")),
    )
    result = cr._sync_repo("J3", {"kbId": kb.id})
    assert result["success"] is False
    kb_after = repo_env.db.get(KbDatasource, kb.id)
    assert kb_after.repo_pull_status == "failed"
    assert "clone boom" in (kb_after.repo_pull_error or "")
    docs = repo_env.db.execute(select(KbDocument)).scalars().all()
    assert len(docs) == 0


def test_ensure_folder_reuses_same_level(repo_db):
    doc_create_folder(repo_db, "kb1", {"name": "lib"}, user_id="u")
    another = cr._ensure_folder(repo_db, "kb1", "lib/sub")
    folders = repo_db.execute(select(DocFolder)).scalars().all()
    # lib 复用 + sub 新建 = 2 个目录
    assert len(folders) == 2
    assert another != ""


def test_ensure_folder_root_returns_empty(repo_db):
    assert cr._ensure_folder(repo_db, "kb1", "") == ""
    assert cr._ensure_folder(repo_db, "kb1", ".") == ""