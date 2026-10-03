"""Document parser engine dispatcher.

Single entry point `parse_document_by_engine` used by the ingest pipeline:
given a file extension + the KB's engine rules, it picks the engine and
produces markdown. Engines that fail or are unavailable fall back to the
builtin docreader so ingestion never hard-fails on a parser outage.
"""

from __future__ import annotations

import logging

from api.services.parser_registry import (
    ENGINE_DOCREADER,
    ENGINE_MINERU,
    ENGINE_MINERU_CLOUD,
    ENGINE_PADDLEOCR_VL,
    ENGINE_PADDLEOCR_VL_CLOUD,
    engine_available_map,
    normalize_engine,
    resolve_engine,
)

logger = logging.getLogger(__name__)


def parse_document_by_engine(
    file_name: str,
    file_ext: str,
    content: bytes,
    engine_rules: list[dict] | None = None,
    forced_engine: str | None = None,
) -> tuple[str, dict[str, str], str]:
    """Parse bytes into (markdown, images, engine_used).

    - engine_rules: the KB's parser_engine_rules (type -> engine).
    - forced_engine: skip routing and force an engine (e.g. from a manual
      re-parse); still subject to the availability fallback.
    """
    available = engine_available_map()
    engine = normalize_engine(forced_engine) if forced_engine else resolve_engine(
        file_ext, engine_rules, available
    )
    if forced_engine and not available.get(engine, True):
        logger.warning(
            "forced engine %s unavailable, falling back to %s", engine, ENGINE_DOCREADER
        )
        engine = ENGINE_DOCREADER

    def _docreader() -> tuple[str, dict[str, str]]:
        from worker.tasks.parsers.docreader_parser import parse_with_docreader

        return parse_with_docreader(file_name, file_ext, content)

    try:
        if engine == ENGINE_MINERU:
            from worker.tasks.parsers.mineru_parser import parse_with_mineru

            md, images = parse_with_mineru(file_name, file_ext, content)
        elif engine == ENGINE_MINERU_CLOUD:
            from worker.tasks.parsers.mineru_parser import parse_with_mineru

            md, images = parse_with_mineru(file_name, file_ext, content, use_cloud=True)
        elif engine == ENGINE_PADDLEOCR_VL:
            from worker.tasks.parsers.paddle_parser import parse_with_paddle

            md, images = parse_with_paddle(file_name, file_ext, content)
        elif engine == ENGINE_PADDLEOCR_VL_CLOUD:
            from worker.tasks.parsers.paddle_parser import parse_with_paddle

            md, images = parse_with_paddle(file_name, file_ext, content, use_cloud=True)
        else:
            engine = ENGINE_DOCREADER
            md, images = _docreader()
    except Exception as exc:  # noqa: BLE001 — engine outage must not fail ingest
        logger.warning("engine %s failed for %s: %s; falling back to docreader", engine, file_name, exc)
        engine = ENGINE_DOCREADER
        md, images = _docreader()

    return md, images, engine