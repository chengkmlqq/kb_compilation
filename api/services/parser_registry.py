"""Document parser engine registry.

Mirrors WeKnora's docparser engine_registry: a table of engines, each with a
name, description, the file types it handles, and a live availability probe
(MinerU needs an endpoint; builtin docreader is always available).

Shared by the API layer (`GET /parsers/engines`) and the worker tasks
(`worker/tasks/parsers/`) so the UI dropdown and the actual routing agree.

2026-10-07: PaddleOCR-VL 已移除（占位服务/需 GPU/无真实模型）——仅保留
docreader（内置）+ mineru（自建/云端）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# admin enable/disable (modo_dim PARSER_ENGINES / ENGINE_ADMIN:<name>) —
# WeKnora-style parser availability switches. True unless disabled; the
# worker resolve_engine honours them so disabling here actually reroutes.
# --------------------------------------------------------------------------- #

_ENGINE_ADMIN_PREFIX = "ENGINE_ADMIN:"


def _admin_enabled_map() -> dict[str, bool]:
    """Read admin on/off switches for each engine (True unless disabled)."""
    try:
        from sqlalchemy import select

        from api.db import get_sessionmaker
        from api.models.framework import Dim

        db = get_sessionmaker()()
        try:
            rows = db.execute(
                select(Dim).where(Dim.dim_group == "PARSER_ENGINES", Dim.state == "1")
            ).scalars().all()
            return {
                r.dim_code[len(_ENGINE_ADMIN_PREFIX):]: (r.dim_value.strip().lower() in ("true", "1"))
                for r in rows
                if r.dim_code.startswith(_ENGINE_ADMIN_PREFIX)
            }
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - worker without DB falls back to enabled
        return {}


def set_admin_enabled(name: str, enabled: bool) -> None:
    """Upsert the admin switch for one engine."""
    import uuid

    from sqlalchemy import select

    from api.db import get_sessionmaker
    from api.models.framework import Dim

    engine = normalize_engine(name)
    code = f"{_ENGINE_ADMIN_PREFIX}{engine}"
    db = get_sessionmaker()()
    try:
        row = db.execute(
            select(Dim).where(Dim.dim_group == "PARSER_ENGINES", Dim.dim_code == code)
        ).scalars().first()
        if row:
            row.dim_value = "true" if enabled else "false"
            row.state = "1"
        else:
            db.add(Dim(
                id=uuid.uuid4().hex,
                dim_code=code,
                dim_group="PARSER_ENGINES",
                dim_value="true" if enabled else "false",
                dim_desc=f"解析引擎 {engine} 管理员开关",
                seq=0,
                state="1",
            ))
        db.commit()
    finally:
        db.close()


def _effective_available(info: dict) -> dict:
    """Attach admin overlay: probed available AND admin enabled."""
    admin = _admin_enabled_map()
    out = dict(info)
    enabled = admin.get(info["name"], True)
    out["enabled"] = enabled
    if not enabled:
        out["available"] = False
        out["reason"] = "管理员已禁用"
    return out

ENGINE_DOCREADER = "docreader"
ENGINE_MINERU = "mineru"
ENGINE_MINERU_CLOUD = "mineru_cloud"

ENGINE_ALIASES = {
    "builtin": ENGINE_DOCREADER,
    "markitdown": ENGINE_DOCREADER,
    "default": ENGINE_DOCREADER,
    "": ENGINE_DOCREADER,
    "mineru-cloud": ENGINE_MINERU_CLOUD,
    "mineru_cloud_api": ENGINE_MINERU_CLOUD,
    # 2026-10-07: PaddleOCR-VL 已移除——旧规则/别名（paddleocr*）保留别名降级：
    # normalize 走不到引擎的 key 会被 normalize 透传；registry 的 else 分支兜底 docreader
    "paddleocr": ENGINE_DOCREADER,
    "paddleocr-vl": ENGINE_DOCREADER,
    "paddleocr_vl_cloud_api": ENGINE_DOCREADER,
    "paddleocr-vl-cloud": ENGINE_DOCREADER,
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
}

#: WeKnora default rules (types -> engine) used when a KB has no explicit rules
DEFAULT_ENGINE_RULES: list[dict[str, Any]] = [
    {"file_types": ["pdf"], "engine": ENGINE_MINERU},
    {"file_types": ["doc", "ppt", "pps"], "engine": ENGINE_MINERU},
    # 2026-10-07: PaddleOCR-VL 移除——图片类型默认路由 mineru（OCR 擅长图内文字）
    {"file_types": ["png", "jpg", "jpeg", "bmp", "tiff", "webp"], "engine": ENGINE_MINERU},
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


def _engines() -> list[EngineInfo]:
    m_ep = mineru_endpoint()
    m_key = mineru_cloud_key()

    m_ok, m_reason = _probe_mineru(m_ep, "")
    mc_ok, mc_reason = _probe_mineru("", m_key)

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
    ]


def list_engines() -> list[dict[str, Any]]:
    return [_effective_available(e.to_dict()) for e in _engines()]


def engine_available_map() -> dict[str, bool]:
    return {e["name"]: e["available"] for e in list_engines()}


def engine_endpoint(engine: str) -> str:
    e = normalize_engine(engine)
    if e in (ENGINE_MINERU,):
        return mineru_endpoint()
    if e == ENGINE_MINERU_CLOUD:
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