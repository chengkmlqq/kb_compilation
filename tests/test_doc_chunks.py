"""doc chunks 读取端点测试：技能脚本经 HTTP 拉文档文本块的通道。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import DocChunk, KbDatasource, KbDocument


@pytest.fixture()
def kb_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def _make_kb(db):
    kb = KbDatasource(
        id="kb-chunks-001",
        name="chunks-test-kb",
        label="测试库",
        description="",
        owner_user_id="u1",
        scope="system",
        state="1",
    )
    db.add(kb)
    db.commit()
    return kb


def _make_doc(db, kb_id: str):
    doc = KbDocument(
        id="doc-chunks-001",
        kb_id=kb_id,
        file_name="test-doc.pdf",
        file_ext="pdf",
        parse_state="READY",
        chunk_count=3,
    )
    db.add(doc)
    db.commit()
    return doc


def _make_chunks(db, kb_id: str, doc_id: str):
    for i in range(3):
        db.add(
            DocChunk(
                id=f"chunk-{i+1}",
                kb_id=kb_id,
                document_id=doc_id,
                seq=i + 1,
                content=f"chunk {i + 1} content 测试文本",
                enabled=True,
            )
        )
    db.commit()


def _make_fixture(kb_db):
    kb = _make_kb(kb_db)
    doc = _make_doc(kb_db, kb.id)
    _make_chunks(kb_db, kb.id, doc.id)
    return kb.id, doc.id


def test_get_document_chunks_ok(kb_db) -> None:
    from api.routers.kbs import get_document_chunks

    kb_id, doc_id = _make_fixture(kb_db)
    resp = get_document_chunks(kb_id, doc_id, caller={"user_id": "u1", "team_name": "", "is_admin": False}, db=kb_db)
    assert resp["success"] is True
    items = resp["data"]["items"]
    assert len(items) == 3
    assert items[0]["seq"] == 1
    assert "chunk 1 content" in items[0]["content"]


def test_get_document_chunks_missing_doc(kb_db) -> None:
    from api.routers.kbs import get_document_chunks

    kb_id, _doc_id = _make_fixture(kb_db)
    resp = get_document_chunks(kb_id, "NOPE", caller={"user_id": "u1", "team_name": "", "is_admin": False}, db=kb_db)
    assert resp["success"] is True
    assert resp["data"]["items"] == []


def test_get_document_chunks_missing_kb(kb_db) -> None:
    import fastapi

    from api.routers.kbs import get_document_chunks

    with pytest.raises(fastapi.HTTPException) as exc:
        get_document_chunks("NOPE_KB", "NOPE", caller={"user_id": "u1", "team_name": "", "is_admin": False}, db=kb_db)
    assert exc.value.status_code == 404
