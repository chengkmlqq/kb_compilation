"""MinerU parser 契约测试：请求字段名 + 响应结构解析。

背景（2026-10-04 实测修复）：
  1. 请求发的是 multipart 字段 "files"（复数），而 MinerU 4.x 的 /file_parse
     只接受单个 "file" → 400 {"code":"invalid_request","message":"Invalid
     request: file: Field required"}。
  2. kb 自建 shim 返回 {"status_code":200,"result":"<markdown>"}，而旧的
     _pick_results 只认 results.document / results.files / 顶层 md_content，
     取不到 result → "MinerU 响应中没有 markdown 内容"。
"""
from __future__ import annotations

import httpx
import pytest

from worker.tasks.parsers import mineru_parser as mp


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    @property
    def text(self) -> str:
        import json

        return json.dumps(self._payload)


def test_pick_results_shim_contract():
    """shim 契约：{"status_code":200,"result":"<md>"} → 取出 md。"""
    md, images = mp._pick_results({"status_code": 200, "result": "# Title\n内容"})
    assert md == "# Title\n内容"
    assert images == {}


def test_pick_results_legacy_document():
    md, _ = mp._pick_results({"results": {"document": {"md_content": "legacy"}}})
    assert md == "legacy"


def test_pick_results_empty():
    md, _ = mp._pick_results({"status_code": 500})
    assert md == ""


def test_parse_with_mineru_uses_file_field(monkeypatch):
    """请求必须用 multipart 字段名 file（不是 files）。"""
    captured: dict = {}

    def fake_post(target, data=None, files=None, headers=None):
        captured["target"] = target
        captured["files"] = list((files or {}).keys())
        return _Resp(200, {"status_code": 200, "result": "OK-MD"})

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, target, data=None, files=None, headers=None):
            return fake_post(target, data=data, files=files, headers=headers)

    monkeypatch.setattr(mp, "mineru_endpoint", lambda: "http://mineru:8000")
    monkeypatch.setattr(mp.httpx, "Client", _Client)

    md, _ = mp.parse_with_mineru("a.pdf", "pdf", b"%PDF-1.4 fake")
    assert captured["files"] == ["file"]
    assert captured["target"].endswith("/file_parse")
    assert md == "OK-MD"


def test_parse_with_mineru_missing_endpoint(monkeypatch):
    monkeypatch.setattr(mp, "mineru_endpoint", lambda: "")
    with pytest.raises(RuntimeError, match="endpoint"):
        mp.parse_with_mineru("a.pdf", "pdf", b"x")