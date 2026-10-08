"""KServe Open Inference Protocol (OIP) client — POST /v2/models/{model}/infer.

OIP (aka V2 inference protocol) is implemented by TRT-LLM, OpenVINO Model
Server, KServe, etc. Request/response bodies are JSON with typed tensors:

    {
      "id": "...",
      "inputs": [
        {"name": "text", "shape": [1], "datatype": "BYTES", "data": ["<b64>"]},
        ...
      ],
      "outputs": [{"name": "text"}]
    }

BYTES data is base64-encoded UTF-8. Numeric datatypes (FP32/INT64/BOOL/...)
carry raw JSON numbers. This module stays intentionally thin: build inputs,
call infer, return {output_name: data}.
"""

from __future__ import annotations

import base64
import uuid

import httpx


def tk_b64(text: str) -> str:
    """Base64-encode UTF-8 text for a BYTES tensor cell."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_bytes_cell(cell: str) -> str:
    """Decode one BYTES tensor cell; tolerate non-base64 plaintext servers."""
    if not isinstance(cell, str):
        return str(cell)
    try:
        return base64.b64decode(cell.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        return cell


def text_input(name: str, texts: list[str]) -> dict:
    """BYTES text tensor (batch)."""
    return {"name": name, "shape": [len(texts)], "datatype": "BYTES", "data": [tk_b64(t) for t in texts]}


def scalar_input(name: str, datatype: str, value) -> dict:
    return {"name": name, "shape": [1], "datatype": datatype, "data": [value]}


def infer(
    base_url: str,
    model: str,
    inputs: list[dict],
    outputs: list[dict] | None = None,
    api_key: str = "",
    custom_headers: dict | None = None,
    timeout: float = 120.0,
) -> dict[str, list]:
    """POST /v2/models/{model}/infer; returns {output_name: data}."""
    url = f"{base_url.rstrip('/')}/v2/models/{model}/infer"
    payload: dict = {"id": uuid.uuid4().hex, "inputs": inputs}
    if outputs:
        payload["outputs"] = outputs
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if custom_headers:
        # model-scoped custom headers win (custom auth schemes like X-Api-Key)
        headers.update(custom_headers)

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"OIP infer failed {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    out: dict[str, list] = {}
    for o in body.get("outputs") or []:
        name = o.get("name", "")
        if name:
            out[name] = o.get("data") or []
    return out


def embedding_vector_from_output(data: list, dim: int | None) -> list[float]:
    """Interpret an OIP embedding output (flat FP32 array, possibly [n*dim]).

    Some servers return a single embedding per request regardless of batch
    size; callers retry per-item when the count does not line up with dim.
    """
    if dim and dim > 0 and len(data) >= dim:
        return [float(x) for x in data[:dim]]
    return [float(x) for x in data]