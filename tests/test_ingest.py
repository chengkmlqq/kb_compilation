"""Ingestion pipeline tests: parse -> chunk -> embed -> store.

Uses a fake embedding client (deterministic vectors) so the whole pipeline
runs without a live embedding endpoint. The knowledge store here is a
pgvector-backed PG; these tests exercise the pure logic (chunking, chunk
assembly, idempotent replace) which is the part we own.
"""

from __future__ import annotations

import pytest

from api.services.ingest import (
    chunk_text,
    parse_document,
    DEFAULT_CHUNK_SIZE,
)
from api.services.embedding import EmbeddingClient, EmbeddingConfig


class FakeEmbeddingClient(EmbeddingClient):
    """Deterministic embeddings — one hot-ish vector per text hash."""

    def __init__(self, dim: int = 8):
        super().__init__(EmbeddingConfig(base_url="", api_key="", model="fake", dim=dim))
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [0.0] * self.cfg.dim
            v[hash(t) % self.cfg.dim] = 1.0
            out.append(v)
        return out


def test_parse_markdown_to_text() -> None:
    md = parse_document("t.md", "md", b"# Title\n\nSome body text here.")
    assert "# Title" in md
    assert "body text" in md


def test_chunk_text_splits_long_content() -> None:
    long_text = "Sentence number. " * 300
    chunks = chunk_text(long_text, chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    # seq numbering is 0..n-1 in order
    assert [seq for seq, _ in chunks] == list(range(len(chunks)))
    # every chunk non-empty
    assert all(text.strip() for _, text in chunks)


def test_chunk_text_empty_returns_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_text_short_content_single_chunk() -> None:
    chunks = chunk_text("短内容。", chunk_size=800, chunk_overlap=80)
    assert len(chunks) == 1
