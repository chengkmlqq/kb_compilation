"""PaddleOCR-VL parser engine client (self-hosted + cloud).

Ported from WeKnora internal/infrastructure/docparser/paddleocr_vl_converter.go.
Self-hosted: POST {endpoint}/layout-parsing (JSON, base64 file). The service
returns one layoutParsingResults entry per page; we merge their markdown and
images into one document.
"""

from __future__ import annotations

import base64
import logging

import httpx

from api.services.parser_registry import paddle_cloud_key, paddle_endpoint

logger = logging.getLogger(__name__)

PADDLE_TIMEOUT = 300.0


def _recognition_params(use_seal: bool, use_chart: bool) -> dict:
    """Parameters matching the WeKnora self-hosted/cloud parity payload."""
    return {
        "markdownIgnoreLabels": [
            "header", "header_image", "footer", "footer_image",
            "number", "footnote", "aside_text",
        ],
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useLayoutDetection": True,
        "useChartRecognition": use_chart,
        "useSealRecognition": use_seal,
        "useOcrForImageBlock": False,
        "mergeTables": True,
        "relevelTitles": True,
        "restructurePages": True,
        "layoutShapeMode": "auto",
        "promptLabel": "ocr",
        "layoutNms": True,
        "repetitionPenalty": 1,
        "temperature": 0,
        "topP": 1,
        "minPixels": 147384,
        "maxPixels": 2822400,
    }


def _file_type_code(file_ext: str) -> int:
    """0 = PDF, 1 = image (WeKnora's fileTypeCode)."""
    return 0 if (file_ext or "").lower().lstrip(".") == "pdf" else 1


def parse_with_paddle(
    file_name: str,
    file_ext: str,
    content: bytes,
    use_cloud: bool = False,
    use_seal: bool = True,
    use_chart: bool = True,
) -> tuple[str, dict[str, str]]:
    """Parse bytes via PaddleOCR-VL. Raises on failure."""
    endpoint = "" if use_cloud else paddle_endpoint()
    if use_cloud and not paddle_cloud_key():
        raise RuntimeError("PaddleOCR-VL 云端 API key 未配置")
    if not use_cloud and not endpoint:
        raise RuntimeError("PaddleOCR-VL endpoint 未配置")

    target = endpoint.rstrip("/") + "/layout-parsing"

    body: dict = {
        "file": base64.b64encode(content).decode("ascii"),
        "fileType": _file_type_code(file_ext),
    }
    headers = {}
    if use_cloud:
        headers["Authorization"] = f"Bearer {paddle_cloud_key()}"
    else:
        body.update(_recognition_params(use_seal, use_chart))

    with httpx.Client(timeout=PADDLE_TIMEOUT, follow_redirects=True) as client:
        resp = client.post(target, json=body, headers=headers)

    if resp.status_code != 200:
        raise RuntimeError(f"PaddleOCR-VL 状态 {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    if payload.get("errorCode"):
        raise RuntimeError(
            f"PaddleOCR-VL error {payload.get('errorCode')}: {payload.get('errorMsg')}"
        )

    result = payload.get("result") or {}
    pages = result.get("layoutParsingResults") or []
    if not pages:
        raise RuntimeError("PaddleOCR-VL 响应中没有解析结果")

    md_parts: list[str] = []
    images: dict[str, str] = {}
    for page in pages:
        md = (page.get("markdown") or {})
        text = md.get("text") or ""
        if text:
            md_parts.append(text)
        for path, data in (md.get("images") or {}).items():
            if path not in images:
                images[path] = data

    merged = "\n\n".join(md_parts)
    if not merged:
        raise RuntimeError("PaddleOCR-VL 解析结果为空")
    logger.info("PaddleOCR-VL parsed %s: %d chars, %d images", file_name, len(merged), len(images))
    return merged, images