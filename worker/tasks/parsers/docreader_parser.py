"""Builtin docreader parser engine (the default; no external service).

Thin wrapper so the engine registry has a uniform call shape: every engine
returns (markdown, images) and lives behind one entry point.
"""

from __future__ import annotations

import logging

from docreader.parser import Parser

logger = logging.getLogger(__name__)


def parse_with_docreader(
    file_name: str,
    file_ext: str,
    content: bytes,
    parser_engine: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Parse bytes with the builtin docreader. Returns (markdown, {})."""
    parser = Parser()
    doc = parser.parse_file(
        file_name=file_name,
        file_type=file_ext,
        content=content,
        parser_engine=parser_engine,
    )
    return doc.content or "", {}