"""Content-Type 归一化中间件测试（2026-10-11「新建目录 422」回归）。

根因：前端 request() 的默认小写 content-type 与调用方显式 Content-Type
两个大小写不同的键同时存在时，新版 Chromium 合并成单个逗号值
"application/json, application/json"；starlette 不识别 → JSON body 按
字符串解析 → 422 model_attributes_type。中间件把逗号拼接重复值归一化为
单值，兼容存量客户端/旧缓存浏览器。
"""

from __future__ import annotations

import asyncio

from starlette.requests import Request

from api.middleware import normalize_content_type


def _make_request(headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/kbs/x/doc-folders",
        "raw_path": b"/api/v1/kbs/x/doc-folders",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "app": None,
        "asgi": {"version": "3.0", "spec_version": "2.0"},
    }
    return Request(scope)


def _run(scope_headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    captured: dict = {}

    async def call_next(request: Request):
        captured["headers"] = request.scope["headers"]
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True})

    req = _make_request(scope_headers)
    asyncio.run(normalize_content_type(req, call_next))
    return captured["headers"]


def test_comma_joined_duplicate_content_type_normalized():
    """Chromium 合并出 'application/json, application/json' → 归一为单值。"""
    out = _run([(b"content-type", b"application/json, application/json")])
    assert (b"content-type", b"application/json") in out
    # 且只保留一个 content-type
    assert [k for k, _ in out if k.lower() == b"content-type"].count(b"content-type") == 1


def test_charset_carrying_first_segment_kept():
    """'application/json, application/json; charset=utf-8' → 取第一段。"""
    out = _run([(b"content-type", b"application/json, application/json; charset=utf-8")])
    assert (b"content-type", b"application/json") in out


def test_normal_content_type_untouched():
    """单值 content-type 不变；其他头原样保留。"""
    out = _run([(b"content-type", b"application/json"), (b"cookie", b"x=1")])
    assert (b"content-type", b"application/json") in out
    assert (b"cookie", b"x=1") in out


def test_missing_content_type_untouched():
    out = _run([(b"cookie", b"x=1")])
    assert out == [(b"cookie", b"x=1")]