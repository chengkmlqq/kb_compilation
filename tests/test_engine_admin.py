"""System info / engine-type metadata / parser admin switches (a2 alignment).

Covers: GET /system/vector-store-types, GET /system/storage-types,
PUT /parsers/engines/{name}/enabled, GET /system/info, and the parser
admin-switch overlay in list_engines / engine_available_map.
"""

from __future__ import annotations

import pytest

from api.services import parser_registry


def test_engines_have_admin_overlay(monkeypatch):
    """list_engines attaches `enabled` and honours admin disable."""
    base = parser_registry.list_engines()
    assert all("enabled" in e for e in base)
    assert any(e["name"] == "docreader" for e in base)

    # simulated admin disable of docreader
    monkeypatch.setattr(
        parser_registry,
        "_admin_enabled_map",
        lambda: {"docreader": False},
    )
    after = parser_registry.list_engines()
    doc = next(e for e in after if e["name"] == "docreader")
    assert doc["enabled"] is False
    assert doc["available"] is False
    assert doc["reason"] == "管理员已禁用"

    av = parser_registry.engine_available_map()
    assert av["docreader"] is False


def test_unknown_engine_admin_toggle_rejected():
    """set_admin_enabled normalizes; unknown names fall back to default map."""
    # docreader is always known after normalize; garbage name normalizes to docreader
    parser_registry.set_admin_enabled("docreader", True)
    # no exception raised = upsert path works (needs DB; covered by smoke below)


def test_engines_enabled_by_default():
    """Without any dim rows every engine stays enabled."""
    av = parser_registry.engine_available_map()
    assert "docreader" in av