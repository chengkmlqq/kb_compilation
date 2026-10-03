"""Document parser engines for the ingestion pipeline.

Each engine is a module exposing `parse_with_*` returning (markdown, images).
`registry.parse_document_by_engine` picks the engine for a file and falls back
to the builtin docreader when an engine is unavailable or fails.
"""

from worker.tasks.parsers.registry import parse_document_by_engine

__all__ = ["parse_document_by_engine"]