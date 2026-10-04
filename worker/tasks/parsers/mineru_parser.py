"""MinerU parser engine client (self-hosted + cloud).

Ported from WeKnora internal/infrastructure/docparser/mineru_converter.go.
Self-hosted: POST {endpoint}/file_parse (multipart). Cloud: the same shape
via the hosted API. Returns markdown (with image refs preserved) plus an
image map, exactly like the WeKnora reader.
"""

from __future__ import annotations

import base64
import logging

import httpx

from api.services.parser_registry import mineru_cloud_key, mineru_endpoint

logger = logging.getLogger(__name__)

MINERU_TIMEOUT = 300.0


def _pick_results(payload: dict) -> tuple[str, dict[str, str]]:
    """Extract (md_content, images) from a MinerU response.

    Mirrors WeKnora: prefer results.document, fall back to results.files,
    then to a single top-level markdown field.

    Also accepts the kb self-hosted shim contract {status_code, result}:
    ``result`` is the markdown string (images inlined base64), which is what
    /file_parse on mineru 4.x returns.
    """
    results = payload.get("results") or {}
    for key in ("document", "files"):
        node = results.get(key)
        if isinstance(node, dict):
            md = node.get("md_content") or ""
            images = node.get("images") or {}
            if md:
                return str(md), dict(images or {})

    # kb 自建服务 /file_parse（mineru 4.x shim）：{"status_code":200,"result":"<md>"}
    shim_md = payload.get("result")
    if isinstance(shim_md, str) and shim_md.strip():
        images = payload.get("images") or {}
        return shim_md, dict(images or {})

    # some builds return the fields at the top level
    md = payload.get("md_content") or payload.get("markdown") or ""
    images = payload.get("images") or {}
    return str(md), dict(images or {})


def parse_with_mineru(
    file_name: str,
    file_ext: str,
    content: bytes,
    use_cloud: bool = False,
    table_enable: bool = True,
    formula_enable: bool = True,
    ocr_enable: bool = True,
    language: str = "",
    backend: str = "",
    vlm_server_url: str = "",
) -> tuple[str, dict[str, str]]:
    """Parse bytes via MinerU. Raises on failure."""
    endpoint = "" if use_cloud else mineru_endpoint()
    if use_cloud and not mineru_cloud_key():
        raise RuntimeError("MinerU 云端 API key 未配置")
    if not use_cloud and not endpoint:
        raise RuntimeError("MinerU endpoint 未配置")

    target = endpoint.rstrip("/") + "/file_parse"

    data = {
        "return_md": "true",
        "return_images": "true",
        "table_enable": "true" if table_enable else "false",
        "formula_enable": "true" if formula_enable else "false",
        "parse_method": "ocr" if ocr_enable else "txt",
        "start_page_id": "0",
        "end_page_id": "99999",
        "backend": backend or "pipeline",
        "response_format_zip": "false",
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_content_list": "true",
    }
    if language:
        data["lang_list"] = language
    if vlm_server_url:
        data["server_url"] = vlm_server_url

    # MinerU 4.x 的 /file_parse 只接受单个 **file** 字段（server_shim: file_parse(file=...)）。
    # 历史实现发的是 "files"（复数），服务端会返回 400 "file: Field required"。
    # 云端与自建保持一致：统一用 file；额外 form 字段对 4.x 端点是可选参数（缺省即 pipeline）。
    files = {"file": (file_name or "document", content)}
    headers = {}
    if use_cloud:
        headers["Authorization"] = f"Bearer {mineru_cloud_key()}"

    with httpx.Client(timeout=MINERU_TIMEOUT, follow_redirects=True) as client:
        resp = client.post(target, data=data, files=files, headers=headers)

    if resp.status_code != 200:
        raise RuntimeError(f"MinerU API 状态 {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    md, images = _pick_results(payload)
    if not md:
        raise RuntimeError("MinerU 响应中没有 markdown 内容")
    logger.info("MinerU parsed %s: %d chars, %d images", file_name, len(md), len(images))
    return md, images


def data_url_to_ref(value: str) -> str:
    """Normalize a MinerU image value into a data URL (or '' if unusable)."""
    if not value:
        return ""
    if value.startswith("data:"):
        return value
    # raw base64 -> assume png
    return "data:image/png;base64," + value


def decode_image(value: str) -> bytes:
    """Best-effort decode of a data URL / raw base64 image to bytes."""
    if not value:
        return b""
    raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        return base64.b64decode(raw)
    except Exception:  # noqa: BLE001
        return b""