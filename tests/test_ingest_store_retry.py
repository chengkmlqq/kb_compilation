"""ingest_document store 段修复测试（2026-10-16）：
1) MySQL 1213 死锁自动重试（瞬态错误，重跑即过）
2) store 失败显式回写 parse_state=FAILED（防「任务失败仍显示向量化中」残留）
3) replace_document_chunks 批量 INSERT 正常写入
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import DocChunk, KbDatasource, KbDocument
from api.services.chunking import ChunkConfig

from sqlalchemy.orm import Session


@pytest.fixture()
def kb_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session_()
    yield db
    db.close()


def _make_kb(db: Session) -> KbDatasource:
    kb = KbDatasource(
        id="kb-ingest-retry",
        name="ingest-retry-kb",
        label="重试测试库",
        description="",
        owner_user_id="u1",
        scope="system",
        state="1",
    )
    db.add(kb)
    db.commit()
    return kb


def _make_doc(db: Session, kb_id: str) -> KbDocument:
    doc = KbDocument(
        id="doc-ingest-retry",
        kb_id=kb_id,
        file_name="test.docx",
        file_ext="docx",
        parse_state="PENDING",
        chunk_count=0,
    )
    db.add(doc)
    db.commit()
    return doc


class _FakeStore:
    """Vector store 测试桩：记录 delete/upsert 调用，不连外部系统。"""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.upserted: list[str] = []

    def delete_by_chunks(self, ids: list[str]) -> None:
        self.deleted.extend(ids)

    def upsert(self, kb_id: str, chunk_id: str, vec: list[float]) -> None:
        self.upserted.append(chunk_id)


class _FakeDBAPIError(Exception):
    def __init__(self, code: int, msg: str) -> None:
        self.args = (code, msg)


def _deadlock_error() -> OperationalError:
    return OperationalError(
        "INSERT INTO doc_chunk",
        {},
        _FakeDBAPIError(1213, "Deadlock found when trying to get lock; try restarting transaction"),
    )


class _CountingReplace:
    """可变 replace_document_chunks 桩：前 fail_times 次抛指定异常，之后返回 result。"""

    def __init__(self, fail: Exception | None = None, fail_times: int = 0, result: int = 5) -> None:
        self.fail = fail
        self.fail_times = fail_times
        self.result = result
        self.calls = 0
        self.last_kwargs: dict = {}

    def run(self, db, kb_id: str, document_id: str, chunks, vectors, meta=None, **kw):
        self.calls += 1
        self.last_kwargs = {"chunks": len(chunks), "vectors": len(vectors), "meta": meta}
        if self.fail is not None and self.calls <= self.fail_times:
            raise self.fail
        return self.result


def _setup(monkeypatch, kb_db: Session, patch_replace: bool = True):
    """替换 ingest_document 外部依赖为测试桩；返回 (doc, fake_store, replace 桩)。"""
    from api.services import ingest as ingest_mod

    kb = _make_kb(kb_db)
    doc = _make_doc(kb_db, kb.id)

    monkeypatch.setattr(
        ingest_mod,
        "parse_document_by_engine",
        lambda *a, **k: ("# 测试标题\n\n正文内容段落。", [], "test_engine"),
    )
    monkeypatch.setattr(
        ingest_mod, "embed_chunks", lambda client, texts: [[0.1, 0.2, 0.3] for _ in texts]
    )
    fake_store = _FakeStore()
    # replace_document_chunks 内部局部导入 get_vector_store（api.services.vector_store）
    monkeypatch.setattr("api.services.vector_store.get_vector_store", lambda *a, **k: fake_store)
    # 死锁重试间的退避在测试里不真睡
    monkeypatch.setattr(ingest_mod.time, "sleep", lambda s: None)

    replace = None
    if patch_replace:
        replace = _CountingReplace()
        monkeypatch.setattr(ingest_mod, "replace_document_chunks", replace.run)
    return kb, doc, fake_store, replace


def _cfg() -> ChunkConfig:
    return ChunkConfig(chunk_size=100, chunk_overlap=10, strategy="auto", enable_parent_child=False)


# --- 场景 1：批量 INSERT 正常写入 ---

def test_store_bulk_insert_writes_all_chunks(kb_db: Session, monkeypatch) -> None:
    from api.services.ingest import ingest_document

    _kb, doc, fake_store, _replace = _setup(monkeypatch, kb_db, patch_replace=False)
    res = ingest_document(kb_db, doc, b"data", None, chunk_cfg=_cfg())

    assert res["parse_state"] == "READY"  # 成功契约：无 success 键，看 parse_state
    assert res["chunks"] >= 1
    # 批量插入落库：行数与返回一致，字段完整
    rows = kb_db.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id).order_by(DocChunk.seq)
    ).scalars().all()
    assert len(rows) == res["chunks"]
    assert [r.seq for r in rows] == list(range(res["chunks"]))  # split seq 0-based
    assert "测试" in rows[0].content
    assert rows[0].meta["source_file"] == "test.docx"
    assert rows[0].enabled is True
    # 向量 store 逐条 upsert（顺序与行一致）
    assert len(fake_store.upserted) == res["chunks"]
    # 文档状态 READY + chunk_count
    doc2 = kb_db.get(KbDocument, doc.id)
    assert doc2.parse_state == "READY"
    assert doc2.chunk_count == res["chunks"]


# --- 场景 2：死锁（1213）自动重试后成功 ---

def test_store_deadlock_retry_then_success(kb_db: Session, monkeypatch) -> None:
    from api.services.ingest import ingest_document

    _kb, doc, _fake_store, rep = _setup(monkeypatch, kb_db, patch_replace=True)
    rep.fail = _deadlock_error()
    rep.fail_times = 1  # 第一次死锁，第二次成功
    rep.result = 5

    res = ingest_document(kb_db, doc, b"data", None, chunk_cfg=_cfg())

    assert rep.calls == 2, f"应重试 1 次共调用 2 次，实际 {rep.calls}"
    assert res["parse_state"] == "READY"
    assert res["chunks"] == 5
    doc2 = kb_db.get(KbDocument, doc.id)
    assert doc2.parse_state == "READY"
    assert doc2.chunk_count == 5


def test_store_deadlock_exhausted_marks_failed(kb_db: Session, monkeypatch) -> None:
    from api.services.ingest import ingest_document

    _kb, doc, _fake_store, rep = _setup(monkeypatch, kb_db, patch_replace=True)
    rep.fail = _deadlock_error()
    rep.fail_times = 99  # 永远死锁 → 3 次后放弃

    res = ingest_document(kb_db, doc, b"data", None, chunk_cfg=_cfg())

    assert rep.calls == 3, f"应尝试 3 次，实际 {rep.calls}"
    assert res["success"] is False
    assert res["parse_state"] == "FAILED"
    assert "Deadlock" in (res["error"] or "")
    doc2 = kb_db.get(KbDocument, doc.id)
    assert doc2.parse_state == "FAILED"
    assert "Deadlock" in (doc2.parse_error or "")


# --- 场景 3：非死锁异常立即 FAILED（不回写残留 EMBEDDING） ---

def test_store_other_error_marks_failed_immediately(kb_db: Session, monkeypatch) -> None:
    from api.services.ingest import ingest_document

    _kb, doc, _fake_store, rep = _setup(monkeypatch, kb_db, patch_replace=True)
    rep.fail = RuntimeError("disk full")
    rep.fail_times = 99

    res = ingest_document(kb_db, doc, b"data", None, chunk_cfg=_cfg())

    assert rep.calls == 1, f"非死锁不应重试，实际 {rep.calls}"
    assert res["success"] is False
    assert res["parse_state"] == "FAILED"
    assert "disk full" in (res["error"] or "")
    doc2 = kb_db.get(KbDocument, doc.id)
    assert doc2.parse_state == "FAILED"
    assert "disk full" in (doc2.parse_error or "")