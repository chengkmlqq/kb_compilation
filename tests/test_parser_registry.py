"""Tests for the parser engine registry + routing."""

from __future__ import annotations

from api.services.parser_registry import (
    ENGINE_DOCREADER,
    ENGINE_MINERU,
    ENGINE_PADDLEOCR_VL,
    engine_available_map,
    list_engines,
    normalize_engine,
    resolve_engine,
)


def test_normalize_engine_aliases():
    assert normalize_engine("builtin") == ENGINE_DOCREADER
    assert normalize_engine("markitdown") == ENGINE_DOCREADER
    assert normalize_engine(None) == ENGINE_DOCREADER
    assert normalize_engine("") == ENGINE_DOCREADER
    assert normalize_engine("mineru_cloud") == "mineru_cloud"


def test_list_engines_shape():
    engines = list_engines()
    names = {e["name"] for e in engines}
    assert ENGINE_DOCREADER in names
    assert ENGINE_MINERU in names
    assert ENGINE_PADDLEOCR_VL in names
    for e in engines:
        assert {"name", "display_name", "available", "reason", "file_types"} <= set(e)
        assert isinstance(e["available"], bool)


def test_docreader_always_available():
    available = engine_available_map()
    assert available[ENGINE_DOCREADER] is True


def test_unconfigured_cloud_engines_unavailable(monkeypatch):
    for var in ("MINERU_CLOUD_API_KEY", "MINERU_API_KEY",
                "PADDLEOCR_CLOUD_API_KEY", "PADDLEOCR_API_KEY",
                "MINERU_ENDPOINT", "MINERU_URL",
                "PADDLEOCR_ENDPOINT", "PADDLEOCR_URL", "PADDLE_OCR_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    avail = engine_available_map()
    # with nothing configured, only docreader is available
    assert avail[ENGINE_DOCREADER] is True
    assert avail[ENGINE_MINERU] is False
    assert avail[ENGINE_PADDLEOCR_VL] is False


def test_configured_mineru_endpoint_becomes_available(monkeypatch):
    monkeypatch.setenv("MINERU_ENDPOINT", "http://mineru:8000")
    avail = engine_available_map()
    assert avail[ENGINE_MINERU] is True


def test_resolve_engine_default_pdf_goes_mineru():
    # default rules: pdf -> mineru (if available) else docreader
    avail = {ENGINE_DOCREADER: True, ENGINE_MINERU: True}
    assert resolve_engine("pdf", None, avail) == ENGINE_MINERU


def test_resolve_engine_unavailable_mineru_falls_back_to_docreader():
    # pdf wants mineru but it's not configured -> fall back to docreader
    avail = {ENGINE_DOCREADER: True, ENGINE_MINERU: False}
    assert resolve_engine("pdf", None, avail) == ENGINE_DOCREADER


def test_resolve_engine_txt_goes_docreader():
    avail = {ENGINE_DOCREADER: True, ENGINE_MINERU: True}
    assert resolve_engine("txt", None, avail) == ENGINE_DOCREADER


def test_resolve_engine_custom_rules_take_priority():
    rules = [{"file_types": ["pdf"], "engine": "docreader"}]
    avail = {ENGINE_DOCREADER: True, ENGINE_MINERU: True}
    assert resolve_engine("pdf", rules, avail) == ENGINE_DOCREADER


def test_resolve_engine_unknown_ext_defaults_docreader():
    avail = {ENGINE_DOCREADER: True}
    assert resolve_engine("xyz", None, avail) == ENGINE_DOCREADER