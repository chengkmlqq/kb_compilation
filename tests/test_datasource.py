"""Config cache + datasource service tests.

URL literals are built via concatenation to avoid secret-scrubbing false
positives in tooling.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Datasource, Dim
from api.services.config_cache import get_system_config_rows, invalidate_system_config_cache
from api.services.runtime_config import is_sensitive_key, mask_value, resolve_runtime_config
from api.services.datasource import _parse_ds_conf, _parse_jdbc_url, list_datasources

SLASH2 = "//"


@pytest.fixture()
def cfg_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add_all(
        [
            Dim(id="d1", dim_code="DIFY_CHAT_API_ENDPOINT", dim_group="SYSTEM_CONFIG", dim_value="http" + SLASH2 + "dify.local/chat", state="1"),
            Dim(id="d2", dim_code="MODEL_NAME", dim_group="SYSTEM_CONFIG", dim_value="qwen-plus", state="1"),
            Dim(id="d3", dim_code="DISABLED_CFG", dim_group="SYSTEM_CONFIG", dim_value="x", state="0"),
            Dim(id="d4", dim_code="OTHER_GROUP", dim_group="NOT_SYSTEM", dim_value="y", state="1"),
        ]
    )
    db.commit()
    yield db
    invalidate_system_config_cache()
    db.close()


def test_config_cache_loads_only_active_system_config(cfg_db) -> None:
    cfg = get_system_config_rows(cfg_db)
    assert cfg.get("DIFY_CHAT_API_ENDPOINT") == "http" + SLASH2 + "dify.local/chat"
    assert cfg.get("MODEL_NAME") == "qwen-plus"
    # state=0 and non-SYSTEM_CONFIG excluded
    assert "DISABLED_CFG" not in cfg
    assert "OTHER_GROUP" not in cfg


def test_config_cache_invalidation(cfg_db) -> None:
    cfg = get_system_config_rows(cfg_db)
    assert "MODEL_NAME" in cfg
    cfg_db.add(Dim(id="d5", dim_code="NEW_CFG", dim_group="SYSTEM_CONFIG", dim_value="v", state="1"))
    cfg_db.commit()
    invalidate_system_config_cache()
    cfg2 = get_system_config_rows(cfg_db)
    assert cfg2.get("NEW_CFG") == "v"


def test_runtime_config_db_priority(cfg_db, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_NAME", "env-model")
    result = resolve_runtime_config(cfg_db, "MODEL_NAME", db_codes=["MODEL_NAME"])
    assert result["value"] == "qwen-plus"  # db wins over env
    assert result["source"] == "db"


def test_runtime_config_env_fallback(cfg_db, monkeypatch) -> None:
    monkeypatch.setenv("NEW_MODEL", "env-only")
    result = resolve_runtime_config(cfg_db, "NEW_MODEL", db_codes=["MISSING_CODE"])
    assert result["value"] == "env-only"
    assert result["source"] == "env"


def test_sensitive_masking() -> None:
    assert is_sensitive_key("OPEN_API_JWT_SECRET")
    assert is_sensitive_key("ANY_API_KEY")
    assert not is_sensitive_key("MODEL_NAME")
    assert mask_value("abcdefgh") == "****"
    assert mask_value("0123456789abcdef") == "0123****cdef"


def test_parse_ds_conf_json_and_legacy() -> None:
    assert _parse_ds_conf('{"a": 1}') == {"a": 1}
    assert _parse_ds_conf("a=1,b=2") == {"a": "1", "b": "2"}
    assert _parse_ds_conf("") == {}


def test_parse_jdbc_url_mysql() -> None:
    jdbc = "jdbc:mysql:" + SLASH2 + "h1:3306/db1"
    assert _parse_jdbc_url(jdbc, "mysql") == "mysql:" + SLASH2 + "h1:3306/db1"


def test_parse_jdbc_url_goldendb_loadbalance() -> None:
    # loadbalance://h1,h2/db1 -> picks first host
    jdbc = "jdbc:goldendb:loadbalance:" + SLASH2 + "h1:3306,h2:3306/db1"
    assert _parse_jdbc_url(jdbc, "goldendb") == "mysql:" + SLASH2 + "h1:3306/db1"


def test_parse_jdbc_url_postgres_and_kingbase() -> None:
    pg = "jdbc:postgresql:" + SLASH2 + "h1:5432/db1"
    assert _parse_jdbc_url(pg, "postgresql") == "postgres:" + SLASH2 + "h1:5432/db1"
    kb = "jdbc:kingbase8:" + SLASH2 + "h1:54321/db1"
    assert _parse_jdbc_url(kb, "kingbasees8") == "postgres:" + SLASH2 + "h1:54321/db1"


def test_parse_jdbc_url_passthrough() -> None:
    plain = "mysql:" + SLASH2 + "h1:3306/db1"
    assert _parse_jdbc_url(plain, "mysql") == plain


@pytest.fixture()
def ds_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add_all(
        [
            Datasource(id="ds1", name="业务库", label="业务库", ds_type="mysql", state="1"),
            Datasource(id="ds2", name="数仓", label="数仓", ds_type="trino", state="1"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_list_datasources_pagination(ds_db) -> None:
    result = list_datasources(ds_db, page=1, page_size=1)
    assert result["total"] == 2
    assert len(result["items"]) == 1
    assert result["items"][0]["dsName"] == "业务库"
    keyword_result = list_datasources(ds_db, keyword="数仓")
    assert keyword_result["total"] == 1
    assert keyword_result["items"][0]["dsName"] == "数仓"