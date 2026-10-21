"""Document parser engine registry.

Mirrors WeKnora's docparser engine_registry: a table of engines, each with a
name, description, the file types it handles, and a live availability probe
(MinerU needs an endpoint; builtin docreader is always available).

Shared by the API layer (`GET /parsers/engines`) and the worker tasks
(`worker/tasks/parsers/`) so the UI dropdown and the actual routing agree.

2026-10-07: PaddleOCR-VL 已移除（占位服务/需 GPU/无真实模型）——仅保留
docreader（内置）+ mineru（自建/云端）。

2026-10-20: 新增**上传白名单**（SUPPORTED_UPLOAD_TYPES）——上传接口与前端
accept/预校验共用同一份格式清单，杜绝「传了又解析不了」。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
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


# --------------------------------------------------------------------------- #
# 上传白名单（只允许系统真能解析的格式，2026-10-20）
# --------------------------------------------------------------------------- #

#: 允许上传的文档扩展名 = 内置 docreader（BUILTIN_ENGINE）实际注册了解析器的格式。
#: ⚠️ 必须与 docreader/parser/registry.py 的 builtin 注册表保持一致——
#: tests/test_upload_file_types.py 会反查 docreader 注册表，防止两边漂移。
SUPPORTED_UPLOAD_TYPES: tuple[str, ...] = (
    # Office / 文档
    "doc", "docx", "pdf", "ppt", "pptx", "xls", "xlsx",
    # 纯文本类
    "txt", "md", "markdown", "csv", "epub", "mhtml",
    # 图片（docreader ImageParser：解析为图片引用，供文档图片理解/检索）
    "png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp",
    # 代码文件（2026-10 代码仓库知识库：docreader CodeParser 已注册，
    # 与 docreader/parser/registry.py 的 CODE_LANGUAGE_MAP 保持一致）
    "py", "java", "js", "mjs", "cjs", "jsx", "ts", "tsx", "go", "rs",
    "c", "h", "cpp", "cc", "cxx", "hpp", "cs", "kt", "kts", "swift",
    "php", "rb", "sh", "bash", "zsh", "sql", "yaml", "yml", "json",
    "xml", "html", "htm", "css", "scss", "less", "vue", "proto",
    "dockerfile",
)

#: 前端 <input accept> 用的扩展名串（与白名单同源）
SUPPORTED_UPLOAD_ACCEPT: str = ",".join(f".{t}" for t in SUPPORTED_UPLOAD_TYPES)

#: 允许上传的压缩包扩展名（服务端自动解包，见 /kbs/{id}/documents/upload-archive）。
#: zip 走内置 zipfile；rar 走系统 bsdtar（libarchive，部署镜像含 libarchive-tools）。
SUPPORTED_ARCHIVE_TYPES: tuple[str, ...] = ("zip", "rar")

#: 目录上传 / 压缩包解包时跳过的系统垃圾文件（与前端 web/src/lib/upload-dir.ts 的
#: JUNK_FILE_NAMES 同源；隐藏文件另外按「任一路径段以 . 开头」判定）
JUNK_FILE_NAMES: frozenset[str] = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})

_ARCHIVE_SET = frozenset(SUPPORTED_ARCHIVE_TYPES)


def ext_of(name: str | None) -> str:
    """取扩展名（小写、不含点）——上传文件名 / URL 导入的统一取值口径。"""
    return Path(str(name or "").strip()).suffix.lower().lstrip(".")


def is_supported_ext(ext: str | None) -> bool:
    """该扩展名是否有可用解析器（上传白名单）。"""
    return str(ext or "").strip().lower().lstrip(".") in SUPPORTED_UPLOAD_TYPES


def is_supported_file_name(name: str | None) -> bool:
    return is_supported_ext(ext_of(name))


def is_archive_ext(ext: str | None) -> bool:
    """是否为允许上传的压缩包（zip）。"""
    return str(ext or "").strip().lower().lstrip(".") in _ARCHIVE_SET


def unsupported_type_detail(ext: str | None) -> str:
    """统一的不支持格式提示（含可处理的格式清单）。"""
    e = str(ext or "").strip().lower().lstrip(".")
    shown = f"「.{e}」" if e else "（无扩展名）"
    return (
        f"不支持的文件格式{shown}；系统可处理的格式："
        + "、".join(f".{t}" for t in SUPPORTED_UPLOAD_TYPES)
    )


def archive_entry_parts(entry_name: str) -> list[str]:
    """压缩包条目名 → 规范化路径段（统一分隔符、去掉空段与 "."；含 ".." 视为非法返回 []）。"""
    parts = [p for p in str(entry_name or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return []
    return parts


def is_hidden_or_junk(rel_path: str) -> bool:
    """路径中任一段以 "." 开头 / 命中系统垃圾文件 → 跳过（目录与压缩包上传共用判定）。"""
    parts = [p for p in str(rel_path or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return True
    return any(p.startswith(".") or p.lower() in JUNK_FILE_NAMES for p in parts)