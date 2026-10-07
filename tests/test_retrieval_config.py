"""Global retrieval config (WeKnora tenant-level RetrievalSettings alignment).

Covers /system/retrieval-config (modo_dim RETRIEVAL_CONFIG group) plus the
layering in config_from_kb: global defaults < KB strategy < overrides.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base
from api.models.knowledge import KbDatasource
from api.services import retrieval as retrieval_mod
from api.services.system_config import (
    get_retrieval_config,
    retrieval_config_dict,
    save_retrieval_config,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


def test_get_retrieval_config_defaults(db):
    data = get_retrieval_config(db)
    codes = {i["code"]: i for i in data["items"]}
    assert codes["TOP_K"]["value"] == "10"
    assert codes["THRESHOLD"]["value"] == "0.2"
    assert codes["VECTOR_ENABLED"]["value"] == "true"
    assert codes["WIKI_ENABLED"]["value"] == "false"
    assert codes["TOP_K"]["range"] == ["1", "100", "1"]


def test_save_roundtrip(db):
    save_retrieval_config(
        db,
        [
            {"code": "TOP_K", "value": "25"},
            {"code": "THRESHOLD", "value": "0.35"},
            {"code": "VECTOR_ENABLED", "value": "false"},
        ],
    )
    d = retrieval_config_dict(db)
    assert d["top_k"] == 25
    assert abs(d["threshold"] - 0.35) < 1e-6
    assert d["vector_enabled"] is False


def test_save_validation_int_range(db):
    with pytest.raises(HTTPException) as e:
        save_retrieval_config(db, [{"code": "TOP_K", "value": "9999"}])
    assert e.value.status_code == 400
    assert "取值范围" in e.value.detail


def test_save_validation_bool(db):
    with pytest.raises(HTTPException) as e:
        save_retrieval_config(db, [{"code": "KEYWORD_ENABLED", "value": "maybe"}])
    assert e.value.status_code == 400


def test_save_float_clamped_to_range(db):
    with pytest.raises(HTTPException) as e:
        save_retrieval_config(db, [{"code": "THRESHOLD", "value": "1.5"}])
    assert e.value.status_code == 400


def test_config_from_kb_applies_global(db, monkeypatch):
    save_retrieval_config(
        db,
        [{"code": "TOP_K", "value": "33"}, {"code": "THRESHOLD", "value": "0.5"}],
    )

    def _real(_db):
        return None

    monkeypatch.setattr(retrieval_mod, "_global_retrieval_overrides", lambda: retrieval_config_dict(db))
    kb = KbDatasource(id="kb1", name="kb1", indexing_strategy={})
    cfg = retrieval_mod.config_from_kb(kb)
    assert cfg.top_k == 33
    assert abs(cfg.threshold - 0.5) < 1e-6


def test_overrides_beat_global_and_strategy(monkeypatch):
    monkeypatch.setattr(
        retrieval_mod,
        "_global_retrieval_overrides",
        lambda: {"top_k": 33},
    )
    kb = KbDatasource(
        id="kb1",
        name="kb1",
        indexing_strategy={"vector_enabled": False},
    )
    cfg = retrieval_mod.config_from_kb(kb, {"top_k": 8})
    assert cfg.top_k == 8
    assert cfg.vector_enabled is False