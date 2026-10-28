"""知识库导出/导入服务测试（2026-10-21）——整库 zip：manifest + 文档原文件。

覆盖：导出 zip 结构 / 导入建库+文档入链 / 同名覆盖语义 / 缺 manifest / 坏 zip /
目录重建映射（folder_path → 新目录 id）。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import DocFolder, KbDatasource, KbDocument, WikiPage
from api.services import storage as storage_svc
from api.services.kb_admin import create_document, doc_create_folder
from conftest import create_test_kb as create_kb, seed_llm_model
from api.services.kb_export import export_kb_zip, import_kb_zip


@pytest.fixture()
def kb_db(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    # create_kb 强制绑定摘要模型：手工构造 manifest 的导入测试也要有模型行
    seed_llm_model(db)
    yield db
    db.close()


def _seed_doc(db, kb_id: str, name: str, content: bytes, folder_id: str = "") -> KbDocument:
    """落盘 + 建文档行（storage_path 指向 tmp 下真实文件，导出能读到字节）。"""
    import uuid

    doc_id = uuid.uuid4().hex
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    path = f"/tmp/kb_export_test/{kb_id}/{doc_id}{ext}"
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return create_document(
        db,
        kb_id=kb_id,
        file_name=name,
        file_ext=ext.lstrip("."),
        file_size=len(content),
        storage_path=path,
        folder_id=folder_id,
    )


def _seed_folder(db, kb_id: str, name: str, parent_id: str = "") -> str:
    return doc_create_folder(db, kb_id, {"name": name, "parent_id": parent_id})["id"]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def test_export_empty_kb_manifest(kb_db) -> None:
    kb = create_kb(kb_db, "空库", label="L", description="d")
    data = export_kb_zip(kb_db, kb.id)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format_version"] == 1
    assert manifest["kb"]["name"] == "空库"
    assert manifest["kb"]["label"] == "L"
    assert manifest["documents"] == []
    assert manifest["folders"] == []


def test_export_includes_docs_and_folders(kb_db) -> None:
    kb = create_kb(kb_db, "制度库")
    f1 = _seed_folder(kb_db, kb.id, "制度文件")
    f2 = _seed_folder(kb_db, kb.id, "2024", parent_id=f1)
    _seed_doc(kb_db, kb.id, "办法.md", "# 办法\n第一条".encode("utf-8"), folder_id=f2)
    _seed_doc(kb_db, kb.id, "通知.txt", "通知内容".encode("utf-8"), folder_id=f1)
    _seed_doc(kb_db, kb.id, "根目录.pdf", b"%PDF-root")

    data = export_kb_zip(kb_db, kb.id)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))

    # 目录树与文档清单
    assert len(manifest["folders"]) == 2
    assert len(manifest["documents"]) == 3
    paths = {d["folder_path"] for d in manifest["documents"]}
    assert paths == {"制度文件/2024", "制度文件", ""}
    # 文件字节按目录结构摆放
    assert "files/制度文件/2024/办法.md" in names
    assert "files/制度文件/通知.txt" in names
    assert "files/根目录.pdf" in names
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read("files/制度文件/2024/办法.md") == "# 办法\n第一条".encode("utf-8")
        assert zf.read("files/根目录.pdf") == b"%PDF-root"


def test_export_missing_kb_raises(kb_db) -> None:
    with pytest.raises(ValueError, match="知识库不存在"):
        export_kb_zip(kb_db, "nope")


def test_export_includes_wiki_snapshot(kb_db) -> None:
    kb = create_kb(kb_db, "带 wiki 的库")
    db = kb_db
    db.add(
        WikiPage(
            id="wp1", kb_id=kb.id, slug="实体-监管局", title="监管局",
            page_type="entity", content="# 监管局\n职责", status="active",
        )
    )
    db.commit()
    data = export_kb_zip(kb_db, kb.id)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "wiki/实体-监管局.md" in zf.namelist()
        assert zf.read("wiki/实体-监管局.md") == "# 监管局\n职责".encode("utf-8")


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------


def _import_ctx():
    return dict(caller_user_id="u1", caller_team_name="", is_sys_admin=True)


def test_import_creates_kb_and_enqueues_docs(kb_db, monkeypatch) -> None:
    # 先导出一个库（含目录+文档），然后从环境移除 → 模拟「迁移到空环境」
    src = create_kb(kb_db, "源库")
    f1 = _seed_folder(kb_db, src.id, "目录A")
    _seed_doc(kb_db, src.id, "a.md", "# A".encode("utf-8"), folder_id=f1)
    _seed_doc(kb_db, src.id, "b.txt", b"B")
    zip_bytes = export_kb_zip(kb_db, src.id)
    from api.services.kb_admin import delete_kb

    delete_kb(kb_db, src.id)

    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr("api.routers.kbs._enqueue_document_process", lambda kb, doc, caller_identity="": enqueued.append((kb, doc)))

    res = import_kb_zip(kb_db, zip_bytes, **_import_ctx())
    assert res["kb_name"] == "源库"
    assert res["docs_created"] == 2
    assert res["docs_skipped"] == 0

    # 新库建立，配置快照回放
    new_kb = kb_db.execute(
        select(KbDatasource).where(KbDatasource.name == "源库", KbDatasource.state == "1")
    ).scalars().first()
    assert new_kb is not None and new_kb.id != src.id
    # 文档入链（PENDING + 投递记录）
    docs = kb_db.execute(
        select(KbDocument).where(KbDocument.kb_id == new_kb.id)
    ).scalars().all()
    assert len(docs) == 2
    assert {d.file_name for d in docs} == {"a.md", "b.txt"}
    assert all(d.parse_state == "PENDING" for d in docs)
    assert len(enqueued) == 2
    # 目录重建 + 文档挂回目录
    folders = kb_db.execute(
        select(DocFolder).where(DocFolder.kb_id == new_kb.id)
    ).scalars().all()
    assert len(folders) == 1
    doc_a = next(d for d in docs if d.file_name == "a.md")
    assert doc_a.folder_id == folders[0].id


def test_import_same_name_without_replace_rejects(kb_db, monkeypatch) -> None:
    create_kb(kb_db, "同名库")
    # 手工构造一个最小的合法 zip（manifest 指同名库）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format_version": 1, "kb": {"name": "同名库"}}),
        )
    with pytest.raises(ValueError, match="已存在同名知识库"):
        import_kb_zip(kb_db, buf.getvalue(), **_import_ctx())


def test_import_same_name_replace_overwrites(kb_db, monkeypatch) -> None:
    old = create_kb(kb_db, "覆盖库")
    _seed_doc(kb_db, old.id, "old.md", b"old")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "kb": {"name": "覆盖库", "summary_model_id": "test-llm"},
                    "documents": [{"file_name": "new.md", "folder_path": ""}],
                }
            ),
        )
        zf.writestr("files/new.md", b"# new")
    enqueued = []
    monkeypatch.setattr("api.routers.kbs._enqueue_document_process", lambda kb, doc, caller_identity="": enqueued.append((kb, doc)))
    res = import_kb_zip(kb_db, buf.getvalue(), replace=True, **_import_ctx())
    assert res["kb_name"] == "覆盖库"
    assert res["docs_created"] == 1
    # 旧库软删，新库重建
    rows = kb_db.execute(
        select(KbDatasource).where(KbDatasource.name == "覆盖库")
    ).scalars().all()
    assert len(rows) == 2  # 旧 state=0 + 新 state=1
    assert {r.state for r in rows} == {"0", "1"}
    new_kb = next(r for r in rows if r.state == "1")
    docs = kb_db.execute(
        select(KbDocument).where(KbDocument.kb_id == new_kb.id)
    ).scalars().all()
    assert [d.file_name for d in docs] == ["new.md"]


def test_import_missing_manifest_rejects(kb_db) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("随便.txt", "x")
    with pytest.raises(ValueError, match="缺少 manifest.json"):
        import_kb_zip(kb_db, buf.getvalue(), **_import_ctx())


def test_import_bad_zip_rejects(kb_db) -> None:
    with pytest.raises(ValueError, match="无法解压"):
        import_kb_zip(kb_db, b"not-a-zip", **_import_ctx())


def test_import_wrong_format_version_rejects(kb_db) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 99, "kb": {"name": "x"}}))
    with pytest.raises(ValueError, match="不支持的导出格式版本"):
        import_kb_zip(kb_db, buf.getvalue(), **_import_ctx())


def test_import_skips_missing_file_bytes(kb_db, monkeypatch) -> None:
    """manifest 列出但 zip 无字节的文档 → skipped 不建行。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "kb": {"name": "缺字节库", "summary_model_id": "test-llm"},
                    "documents": [{"file_name": "ghost.pdf", "folder_path": ""}],
                }
            ),
        )
    res = import_kb_zip(kb_db, buf.getvalue(), **_import_ctx())
    assert res["docs_created"] == 0
    assert res["docs_skipped"] == 1


def test_export_import_roundtrip_doc_bytes(kb_db, monkeypatch) -> None:
    """导出→导入往返：文档字节完整（经存储层落盘/读回）。"""
    src = create_kb(kb_db, "往返库")
    f1 = _seed_folder(kb_db, src.id, "子目录")
    _seed_doc(kb_db, src.id, "表格.xlsx", b"\x50\x4b\x03\x04fake-xlsx", folder_id=f1)
    zip_bytes = export_kb_zip(kb_db, src.id)
    from api.services.kb_admin import delete_kb

    delete_kb(kb_db, src.id)

    enqueued = []
    monkeypatch.setattr("api.routers.kbs._enqueue_document_process", lambda kb, doc, caller_identity="": enqueued.append((kb, doc)))
    res = import_kb_zip(kb_db, zip_bytes, **_import_ctx())
    new_kb = kb_db.execute(
        select(KbDatasource).where(KbDatasource.name == "往返库", KbDatasource.state == "1")
    ).scalars().first()
    assert res["docs_created"] == 1
    doc = kb_db.execute(
        select(KbDocument).where(KbDocument.kb_id == new_kb.id)
    ).scalars().first()
    assert doc is not None
    # 存储层读回字节一致
    assert storage_svc.get_bytes(doc.storage_path) == b"\x50\x4b\x03\x04fake-xlsx"
