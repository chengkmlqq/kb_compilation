"""Document parser engine registry.

Mirrors WeKnora's docparser engine_registry: a table of engines, each with a
name, description, the file types it handles, and a live availability probe
(MinerU/Paddle need an endpoint; builtin docreader is always available).

Shared by the API layer (`GET /parsers/engines`) and the worker tasks
(`worker/tasks/parsers/`) so the UI dropdown and the actual routing agree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

ENGINE_DOCREADER = "docreader"
ENGINE_MINERU = "mineru"
ENGINE_MINERU_CLOUD = "mineru_cloud"
ENGINE_PADDLEOCR_VL = "paddleocr_vl"
ENGINE_PADDLEOCR_VL_CLOUD = "paddleocr_vl_cloud"

ENGINE_ALIASES = {
    "builtin": ENGINE_DOCREADER,
    "markitdown": ENGINE_DOCREADER,
    "default": ENGINE_DOCREADER,
    "": ENGINE_DOCREADER,
    "mineru-cloud": ENGINE_MINERU_CLOUD,
    "mineru_cloud_api": ENGINE_MINERU_CLOUD,
    "paddleocr": ENGINE_PADDLEOCR_VL,
    "paddleocr-vl": ENGINE_PADDLEOCR_VL,
    "paddleocr_vl_cloud_api": ENGINE_PADDLEOCR_VL_CLOUD,
    "paddleocr-vl-cloud": ENGINE_PADDLEOCR_VL_CLOUD,
}


def normalize_engine(name: str | None) -> str:
    """Map user/config spellings onto the canonical engine name."""
    if not name:
        return ENGINE_DOCREADER
    key = str(name).strip().lower().replace(" ", "")
    return ENGINE_ALIASES.get(key, key)


def _env(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return ""


def mineru_endpoint() -> str:
    return _env("MINERU_ENDPOINT", "MINERU_URL")


def mineru_cloud_key() -> str:
    return _env("MINERU_CLOUD_API_KEY", "MINERU_API_KEY")


def paddle_endpoint() -> str:
    return _env("PADDLEOCR_ENDPOINT", "PADDLEOCR_URL", "PADDLE_OCR_ENDPOINT")


def paddle_cloud_key() -> str:
    return _env("PADDLEOCR_CLOUD_API_KEY", "PADDLEOCR_API_KEY")


# --------------------------------------------------------------------------- #
# file type routing (which engine suits which extension)
# --------------------------------------------------------------------------- #

#: extensions each engine is a sensible default for
ENGINE_DEFAULT_TYPES: dict[str, list[str]] = {
    ENGINE_DOCREADER: [
        "txt", "md", "markdown", "docx", "pptx", "xlsx", "csv", "html", "htm",
    ],
    ENGINE_MINERU: ["pdf", "doc", "ppt", "pps"],
    ENGINE_MINERU_CLOUD: ["pdf", "doc", "ppt", "pps"],
    ENGINE_PADDLEOCR_VL: ["pdf", "png", "jpg", "jpeg", "bmp", "tiff", "webp"],
    ENGINE_PADDLEOCR_VL_CLOUD: ["pdf", "png", "jpg", "jpeg", "bmp", "tiff", "webp"],
}

#: WeKnora default rules (types -> engine) used when a KB has no explicit rules
DEFAULT_ENGINE_RULES: list[dict[str, Any]] = [
    {"file_types": ["pdf"], "engine": ENGINE_MINERU},
    {"file_types": ["doc", "ppt", "pps"], "engine": ENGINE_MINERU},
    {"file_types": ["png", "jpg", "jpeg", "bmp", "tiff", "webp"], "engine": ENGINE_PADDLEOCR_VL},
    {"file_types": ["txt", "md", "docx", "xlsx", "csv", "html", "htm"], "engine": ENGINE_DOCREADER},
]


@dataclass
class EngineInfo:
    name: str
    display_name: str
    description: str
    file_types: list[str] = field(default_factory=list)
    available: bool = True
    reason: str = ""
    endpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "file_types": list(self.file_types),
            "available": self.available,
            "reason": self.reason,
            "endpoint": self.endpoint,
        }


def _probe_mineru(endpoint: str, api_key: str) -> tuple[bool, str]:
    if not endpoint and not api_key:
        return False, "MinerU endpoint/API key 未配置"
    return True, ""


def _probe_paddle(endpoint: str, api_key: str) -> tuple[bool, str]:
    if not endpoint and not api_key:
        return False, "PaddleOCR-VL endpoint/API key 未配置"
    return True, ""


def _engines() -> list[EngineInfo]:
    m_ep = mineru_endpoint()
    m_key = mineru_cloud_key()
    p_ep = paddle_endpoint()
    p_key = paddle_cloud_key()

    m_ok, m_reason = _probe_mineru(m_ep, "")
    mc_ok, mc_reason = _probe_mineru("", m_key)
    p_ok, p_reason = _probe_paddle(p_ep, "")
    pc_ok, pc_reason = _probe_paddle("", p_key)

    return [
        EngineInfo(
            name=ENGINE_DOCREADER,
            display_name="内置 docreader",
            description="内置解析引擎,支持常用办公文档与纯文本",
            file_types=ENGINE_DEFAULT_TYPES[ENGINE_DOCREADER],
            available=True,
        ),
        EngineInfo(
            name=ENGINE_MINERU,
            display_name="MinerU(自建)",
            description="自建 MinerU 服务,擅长复杂版式 PDF/Office",
            file_types=ENGINE_DEFAULT_TYPES[ENGINE_MINERU],
            available=m_ok,
            reason=m_reason,
            endpoint=m_ep,
        ),
        EngineInfo(
            name=ENGINE_MINERU_CLOUD,
            display_name="MinerU(云端)",
            description="MinerU 官方云 API",
            file_types=ENGINE_DEFAULT_TYPES[ENGINE_MINERU_CLOUD],
            available=mc_ok,
            reason=mc_reason,
        ),
        EngineInfo(
            name=ENGINE_PADDLEOCR_VL,
            display_name="PaddleOCR-VL(自建)",
            description="自建 PaddleOCR-VL,擅长扫描件与图片 OCR",
            file_types=ENGINE_DEFAULT_TYPES[ENGINE_PADDLEOCR_VL],
            available=p_ok,
            reason=p_reason,
            endpoint=p_ep,
        ),
        EngineInfo(
            name=ENGINE_PADDLEOCR_VL_CLOUD,
            display_name="PaddleOCR-VL(云端)",
            description="PaddleOCR-VL 云 API",
            file_types=ENGINE_DEFAULT_TYPES[ENGINE_PADDLEOCR_VL_CLOUD],
            available=pc_ok,
            reason=pc_reason,
        ),
    ]


def list_engines() -> list[dict[str, Any]]:
    return [e.to_dict() for e in _engines()]


def engine_available_map() -> dict[str, bool]:
    return {e.name: e.available for e in _engines()}


def engine_endpoint(engine: str) -> str:
    e = normalize_engine(engine)
    if e in (ENGINE_MINERU,):
        return mineru_endpoint()
    if e in (ENGINE_PADDLEOCR_VL,):
        return paddle_endpoint()
    if e == ENGINE_MINERU_CLOUD:
        return "cloud"
    if e == ENGINE_PADDLEOCR_VL_CLOUD:
        return "cloud"
    return ""


# --------------------------------------------------------------------------- #
# rule resolution
# --------------------------------------------------------------------------- #


def resolve_engine(
    file_ext: str | None,
    rules: list[dict[str, Any]] | None = None,
    available: dict[str, bool] | None = None,
) -> str:
    """Pick an engine for a file extension.

    - If the KB provides explicit rules, match by extension (first hit wins).
    - Otherwise fall back to WeKnora's default type->engine mapping.
    - If the chosen engine is unavailable, fall back to docreader.
    """
    ext = (file_ext or "").strip().lower().lstrip(".")
    if available is None:
        available = engine_available_map()

    chosen: str | None = None
    effective_rules = rules if rules else DEFAULT_ENGINE_RULES
    for rule in effective_rules:
        types = rule.get("file_types") or rule.get("fileTypes") or []
        engine = normalize_engine(rule.get("engine"))
        if ext and ext in {str(t).lower().lstrip(".") for t in types}:
            chosen = engine
            break

    if chosen is None:
        chosen = ENGINE_DOCREADER

    if not available.get(chosen, True):
        # requested engine not configured -> docreader fallback
        return ENGINE_DOCREADER
    return chosen