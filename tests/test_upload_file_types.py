"""上传格式门禁 + zip 压缩包自动解包（2026-10-20）。

覆盖：
- 白名单与 docreader 内置解析器注册表一致（防止「传了又处理不了」的漂移）；
- 不支持的格式上传被 400 拒绝且不落库（此前任意扩展名都会落库，
  worker 解析时才抛 `Unsupported file type` → 文档 FAILED）；
- 支持的格式正常入库；
- GET /kbs/file-types/supported 下发白名单（前端 accept / 预校验同源）；
- zip 自动解包：保留包内目录结构、跳过隐藏/空/不支持条目、非 zip 拒绝。
"""

from __future__ import annotations

import io
import shutil
import types
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.knowledge import KbDocument
from api.services.identity import Identity, encode_identity_cookie
from api.services.kb_admin import create_kb, doc_create_folder, doc_folders
from api.services.parser_registry import SUPPORTED_UPLOAD_TYPES
import api.middleware as mw
import api.routers.kbs as kbs_router
from tests._rar_builder import build_rar


@pytest.fixture()
def ft_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 共享底层连接：TestClient 线程与主线程同一库
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


@pytest.fixture()
def ft_client(ft_db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: ft_db
    monkeypatch.setattr(
        kbs_router,
        "decode_identity_cookie",
        lambda cookie: types.SimpleNamespace(user_id="admin", user_name="admin", team_name="ROOT"),
    )
    monkeypatch.setattr(mw, "get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: ft_db})())
    monkeypatch.setattr(mw, "get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"))
    monkeypatch.setattr(mw, "decode_identity_cookie", lambda cookie: Identity(user_id="admin"))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    yield client
    app.dependency_overrides.clear()


def _mk_kb(db, name: str = "格式门禁库"):
    return create_kb(db, name, scope="personal", owner_user_id="admin", created_by="admin")


def _upload(client, kb_id: str, file_name: str, payload: bytes = b"hello world"):
    return client.post(
        f"/api/v1/kbs/{kb_id}/documents/upload",
        files={"file": (file_name, io.BytesIO(payload), "application/octet-stream")},
    )


def _upload_zip(client, kb_id: str, entries: dict[str, bytes], *, folder_id: str = "", keep_structure: bool = True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    url = f"/api/v1/kbs/{kb_id}/documents/upload-archive?keep_structure={'true' if keep_structure else 'false'}"
    if folder_id:
        url += f"&folder_id={folder_id}"
    return client.post(url, files={"file": ("docs.zip", io.BytesIO(buf.getvalue()), "application/zip")})


def _docs(db, kb_id: str) -> list[KbDocument]:
    return list(
        db.execute(select(KbDocument).where(KbDocument.kb_id == kb_id)).scalars().all()
    )


# --------------------------------------------------------------------------- #
# 白名单自身
# --------------------------------------------------------------------------- #


def test_supported_types_match_docreader_builtin_registry() -> None:
    """护栏：上传白名单必须等于 docreader 内置引擎注册的解析格式。

    两边漂移时本用例失败——要么补白名单，要么去掉解析器不支持的格式。
    """
    from docreader.parser.registry import BUILTIN_ENGINE, registry

    builtin = set(registry._engines[BUILTIN_ENGINE].keys())  # noqa: SLF001 - 注册表无公开读法
    assert set(SUPPORTED_UPLOAD_TYPES) == builtin


def test_supported_file_types_endpoint(ft_client) -> None:
    r = ft_client.get("/api/v1/kbs/file-types/supported")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["types"] == list(SUPPORTED_UPLOAD_TYPES)
    assert ".pdf" in data["accept"] and ".docx" in data["accept"]
    assert data["archive_types"] == ["zip", "rar"]


# --------------------------------------------------------------------------- #
# 单文件格式门禁
# --------------------------------------------------------------------------- #


def test_upload_rejects_unsupported_extension(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload(ft_client, kb.id, "design.psd")
    assert r.status_code == 400, r.text
    assert "不支持的文件格式" in r.text
    assert ".pdf" in r.text  # 提示里带可处理格式清单
    assert _docs(ft_db, kb.id) == []  # 不落库、不排队


def test_upload_accepts_supported_extension(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload(ft_client, kb.id, "规范.md", b"# hello")
    assert r.status_code == 200, r.text
    docs = _docs(ft_db, kb.id)
    assert len(docs) == 1
    assert docs[0].file_ext == "md"
    assert docs[0].parse_state == "PENDING"


def test_upload_accepts_uppercase_extension(ft_client, ft_db) -> None:
    """大小写不敏感：.PDF 也应放行（扩展名按小写比对）。"""
    kb = _mk_kb(ft_db)
    r = _upload(ft_client, kb.id, "报告.PDF", b"%PDF-1.4 test")
    assert r.status_code == 200, r.text
    assert len(_docs(ft_db, kb.id)) == 1


def test_upload_strips_directory_prefix_from_filename(ft_client, ft_db) -> None:
    """webkitdirectory 上传的 multipart filename 带目录前缀（Chrome 用 webkitRelativePath
    作 filename，如「制度文件/2024/规范.md」）——后端必须剥成裸文件名入库，目录层级
    由前端建真实文档目录承载，不容许路径混进文档名。"""
    kb = _mk_kb(ft_db)
    r = ft_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload",
        files={"file": ("制度文件/2024/规范.md", io.BytesIO("# 规范".encode()), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    docs = _docs(ft_db, kb.id)
    assert len(docs) == 1
    assert docs[0].file_name == "规范.md"
    assert docs[0].file_ext == "md"


def test_upload_strips_windows_style_path_from_filename(ft_client, ft_db) -> None:
    """防御反斜杠路径（Windows 风格「dir\\file.md」同样剥掉）。"""
    kb = _mk_kb(ft_db)
    r = ft_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload",
        files={"file": ("dir\\sub\\规范.md", io.BytesIO("# 规范".encode()), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    docs = _docs(ft_db, kb.id)
    assert len(docs) == 1
    assert docs[0].file_name == "规范.md"
    assert docs[0].file_ext == "md"


# --------------------------------------------------------------------------- #
# zip 自动解包
# --------------------------------------------------------------------------- #


def test_zip_upload_extracts_and_keeps_structure(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload_zip(
        ft_client,
        kb.id,
        {
            "制度/管理办法.md": b"# \xe7\xae\xa1\xe7\x90\x86\xe5\x8a\x9e\xe6\xb3\x95",  # # 管理办法
            "制度/2024/细则.docx": b"docx-bytes",
            "说明.txt": b"txt",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 3
    assert data["folders_created"] == 2  # 制度 + 制度/2024

    docs = {(d.file_name): d for d in _docs(ft_db, kb.id)}
    assert set(docs) == {"管理办法.md", "细则.docx", "说明.txt"}

    folders = {f["name"]: f for f in doc_folders(ft_db, kb.id)}
    assert "制度" in folders and "2024" in folders
    # 「制度/管理办法.md」落到 制度；「制度/2024/细则.docx」落到 制度/2024；根级文件落到库根
    assert docs["管理办法.md"].folder_id == folders["制度"]["id"]
    assert docs["细则.docx"].folder_id == folders["2024"]["id"]
    assert folders["2024"]["parent_id"] == folders["制度"]["id"]
    assert docs["说明.txt"].folder_id in ("", None)


def test_zip_upload_skips_hidden_empty_and_unsupported(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload_zip(
        ft_client,
        kb.id,
        {
            "ok.md": b"# ok",
            "__MACOSX/._ok.md": b"junk",  # 隐藏目录
            "Thumbs.db": b"junk",  # 系统垃圾文件
            "empty.txt": b"",  # 空文件
            "photo.psd": b"unsupported",  # 非白名单格式
            "../escape.md": b"zip-slip",  # 非法路径
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 1
    assert data["skipped"]["hidden"] == 2          # __MACOSX/._ok.md + Thumbs.db
    assert data["skipped"]["empty"] == 1
    assert data["skipped"]["unsupported"] == 1
    assert "psd" in data["unsupported_types"]
    assert [d.file_name for d in _docs(ft_db, kb.id)] == ["ok.md"]


def test_zip_upload_keep_structure_false_flattens(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    target = doc_create_folder(ft_db, kb.id, {"name": "汇总"})
    r = _upload_zip(
        ft_client,
        kb.id,
        {"制度/管理办法.md": b"# a", "制度/2024/细则.docx": b"b"},
        folder_id=target["id"],
        keep_structure=False,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["folders_created"] == 0
    assert {d.folder_id for d in _docs(ft_db, kb.id)} == {target["id"]}
    assert [f["name"] for f in doc_folders(ft_db, kb.id)] == ["汇总"]  # 不新建包内层级


def test_zip_upload_rejects_non_zip(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = ft_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload-archive",
        files={"file": ("notes.md", io.BytesIO(b"# not a zip"), "text/markdown")},
    )
    assert r.status_code == 400
    assert "仅支持 zip" in r.text
    assert _docs(ft_db, kb.id) == []


def test_zip_upload_rejects_broken_zip(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload_zip(ft_client, kb.id, {})  # 空包（无条目）也算合法 zip，这里构造坏包
    assert r.status_code == 200, r.text  # 空包：合法但无文件
    assert r.json()["data"]["created_count"] == 0

    bad = ft_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload-archive",
        files=({"file": ("broken.zip", io.BytesIO(b"PK\x03\x04 truncated"), "application/zip")}),
    )
    assert bad.status_code == 400
    assert "无法解压" in bad.text


def test_zip_upload_restores_mojibake_filename(ft_client, ft_db) -> None:
    """Windows GBK 文件名（无 UTF-8 标志）的 zip：入库文件名应还原为中文。

    模拟 Python zipfile 的标准行为：无 flag bit 11 的条目名按 CP437 解码，
    服务器侧 _restore_mojibake_filename 应把「╓╨╣·」还原为「中国」。
    """
    from api.routers.kbs import _restore_mojibake_filename

    kb = _mk_kb(ft_db)
    # 构造 Windows GBK 编码文件名 + 无 UTF-8 标志位
    gbk_name = "中国移动大数据产品技术规范-20201208-评审.doc"
    raw_bytes = gbk_name.encode("gbk")
    info = zipfile.ZipInfo("x.doc")
    info.flag_bits = 0  # 不设 UTF-8 标志（Windows 老工具行为）
    info.filename = raw_bytes.decode("cp437")  # zipfile 无标志时按 CP437 解码
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(info, b"content")
    r = ft_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload-archive?keep_structure=true",
        files={"file": ("docs.zip", io.BytesIO(buf.getvalue()), "application/zip")},
    )
    assert r.status_code == 200, r.text
    docs = _docs(ft_db, kb.id)
    assert len(docs) == 1
    assert docs[0].file_name == gbk_name, f"文件名未还原: {docs[0].file_name!r}"
    # 还原函数单测：正常中文名/ASCII 名不应被误改
    assert _restore_mojibake_filename("制品采购实施管理办法.pdf") == "制品采购实施管理办法.pdf"
    assert _restore_mojibake_filename("report.pdf") == "report.pdf"
    assert _restore_mojibake_filename("") == ""


def test_zip_upload_rejects_unknown_folder(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload_zip(ft_client, kb.id, {"a.md": b"# a"}, folder_id="not-exist-folder")
    assert r.status_code == 400
    assert "目录不存在" in r.text
    assert _docs(ft_db, kb.id) == []


def _upload_rar(client, kb_id: str, entries: dict[str, bytes], *, folder_id: str = "", keep_structure: bool = True):
    buf = build_rar(entries)
    url = f"/api/v1/kbs/{kb_id}/documents/upload-archive?keep_structure={'true' if keep_structure else 'false'}"
    if folder_id:
        url += f"&folder_id={folder_id}"
    return client.post(url, files={"file": ("docs.rar", io.BytesIO(buf), "application/vnd.rar")})


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="需系统 bsdtar（libarchive）解 rar")
def test_rar_upload_extracts_and_keeps_structure(ft_client, ft_db) -> None:
    """rar 压缩包自动解包（与 zip 同链路：建目录 + 入库 + 跳过统计）。"""
    kb = _mk_kb(ft_db)
    r = _upload_rar(
        ft_client,
        kb.id,
        {
            "制度/管理办法.md": "# 管理办法".encode(),
            "制度/2024/细则.docx": b"docx-bytes",
            "说明.txt": b"txt",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 3
    assert data["folders_created"] == 2

    docs = {d.file_name: d for d in _docs(ft_db, kb.id)}
    assert set(docs) == {"管理办法.md", "细则.docx", "说明.txt"}
    folders = {f["name"]: f for f in doc_folders(ft_db, kb.id)}
    assert docs["管理办法.md"].folder_id == folders["制度"]["id"]
    assert docs["细则.docx"].folder_id == folders["2024"]["id"]


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="需系统 bsdtar（libarchive）解 rar")
def test_rar_upload_skips_hidden_and_unsupported(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    r = _upload_rar(
        ft_client,
        kb.id,
        {
            "ok.md": b"# ok",
            "Thumbs.db": b"junk",  # 系统垃圾文件
            "photo.psd": b"unsupported",  # 非白名单格式
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 1
    assert data["skipped"]["hidden"] == 1
    assert data["skipped"]["unsupported"] == 1
    assert "psd" in data["unsupported_types"]
    assert [d.file_name for d in _docs(ft_db, kb.id)] == ["ok.md"]


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="需系统 bsdtar（libarchive）解 rar")
def test_rar_upload_keep_structure_false_flattens(ft_client, ft_db) -> None:
    kb = _mk_kb(ft_db)
    target = doc_create_folder(ft_db, kb.id, {"name": "汇总"})
    r = _upload_rar(
        ft_client,
        kb.id,
        {"制度/管理办法.md": b"# a", "制度/2024/细则.docx": b"b"},
        folder_id=target["id"],
        keep_structure=False,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["folders_created"] == 0
    assert {d.folder_id for d in _docs(ft_db, kb.id)} == {target["id"]}


def test_rar_upload_rejects_broken_rar(ft_client, ft_db) -> None:
    """伪 rar（非合法包）被拒绝且不落库（需 bsdtar 时走 bsdtar 校验，否则报缺组件）。"""
    kb = _mk_kb(ft_db)
    r = ft_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload-archive",
        files={"file": ("broken.rar", io.BytesIO(b"Rar!\x1a\x07\x00 truncated"), "application/vnd.rar")},
    )
    assert r.status_code == 400
    assert "无法解压" in r.text or "缺少 rar 解包组件" in r.text
    assert _docs(ft_db, kb.id) == []


# --------------------------------------------------------------------------- #
# 嵌套压缩包：包内再嵌 zip/rar 递归解包（2026-10-22）
# --------------------------------------------------------------------------- #


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_upload_extracts_nested_archive(ft_client, ft_db) -> None:
    """zip 内嵌 zip：递归解包，子包名（去扩展名）作为一层目录保留完整层级。"""
    kb = _mk_kb(ft_db)
    inner = _zip_bytes({"细则.md": b"# xize", "2024/办法.docx": b"docx-bytes"})
    r = _upload_zip(ft_client, kb.id, {"制度/内层.zip": inner, "说明.txt": b"txt"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 3
    assert data["folders_created"] == 3  # 制度 + 制度/内层 + 制度/内层/2024

    docs = {d.file_name: d for d in _docs(ft_db, kb.id)}
    assert set(docs) == {"说明.txt", "细则.md", "办法.docx"}
    folders = {f["name"]: f for f in doc_folders(ft_db, kb.id)}
    # 说明.txt 落库根；细则.md → 制度/内层；办法.docx → 制度/内层/2024
    assert docs["说明.txt"].folder_id in ("", None)
    assert docs["细则.md"].folder_id == folders["内层"]["id"]
    assert folders["内层"]["parent_id"] == folders["制度"]["id"]
    assert docs["办法.docx"].folder_id == folders["2024"]["id"]
    assert folders["2024"]["parent_id"] == folders["内层"]["id"]


def test_zip_upload_nested_broken_archive_skips(ft_client, ft_db) -> None:
    """嵌套坏子包跳过（计入 failed），外层正常文件不受影响。"""
    kb = _mk_kb(ft_db)
    r = _upload_zip(ft_client, kb.id, {"good.md": b"# ok", "坏包.zip": b"PK\x03\x04 truncated"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 1
    assert data["skipped"]["failed"] == 1
    assert any("坏包.zip" in e for e in data["errors"])
    assert [d.file_name for d in _docs(ft_db, kb.id)] == ["good.md"]


def test_zip_upload_nested_depth_limit(ft_client, ft_db) -> None:
    """嵌套超过深度上限（MAX_ARCHIVE_DEPTH=5）：该子包跳过并计入 failed。"""
    kb = _mk_kb(ft_db)
    inner5 = {"b.md": b"# b", "deep.zip": _zip_bytes({"a.md": b"# a"})}
    inner4 = {"deep.zip": _zip_bytes(inner5)}
    inner3 = {"deep.zip": _zip_bytes(inner4)}
    inner2 = {"deep.zip": _zip_bytes(inner3)}
    r = _upload_zip(ft_client, kb.id, {"deep.zip": _zip_bytes(inner2)})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 1  # 第 5 层内的 b.md 可入库
    assert data["skipped"]["failed"] == 1  # 第 6 层 deep.zip 超深跳过
    assert any("嵌套压缩包层级过深" in e for e in data["errors"])


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="需系统 bsdtar（libarchive）解 rar")
def test_zip_upload_extracts_nested_rar(ft_client, ft_db) -> None:
    """zip 内嵌 rar：递归解包（子包走 bsdtar 分支，与 zip 同链路）。

    夹具说明：_rar_builder 构造的 rar 仅支持纯文本条目（含 zip 二进制内容时
    bsdtar 会误读内部字节），故嵌套 rar 用 zip 作外层验证 rar 分支递归。
    """
    kb = _mk_kb(ft_db)
    inner = build_rar({"细则.md": b"# xize"})
    r = _upload_zip(ft_client, kb.id, {"制度/内层.rar": inner, "说明.txt": b"txt"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["created_count"] == 2
    assert data["folders_created"] == 2  # 制度 + 制度/内层

    docs = {d.file_name: d for d in _docs(ft_db, kb.id)}
    assert set(docs) == {"说明.txt", "细则.md"}
    folders = {f["name"]: f for f in doc_folders(ft_db, kb.id)}
    assert docs["细则.md"].folder_id == folders["内层"]["id"]
    assert docs["说明.txt"].folder_id in ("", None)
