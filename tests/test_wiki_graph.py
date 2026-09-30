"""Wiki + graph service tests (pure logic, no LLM/Neo4j needed)."""

from __future__ import annotations

import pytest

from api.services.graph import Entity, parse_llm_json_array
from api.services.wiki import (
    WikiCandidate,
    build_links,
    group_candidates_by_entities,
    slugify,
    upsert_pages,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import KnowledgeBase
from api.models.knowledge import WikiPage


# ---------------------------------------------------------------------------
# graph helpers
# ---------------------------------------------------------------------------


def test_parse_llm_json_array_plain() -> None:
    result = parse_llm_json_array('[{"title": "A"}, {"title": "B"}]')
    assert len(result) == 2
    assert result[0]["title"] == "A"


def test_parse_llm_json_array_with_fences() -> None:
    text = '好的，以下是结果：\n```json\n[{"title": "A"}]\n```\n希望有帮助'
    result = parse_llm_json_array(text)
    assert result == [{"title": "A"}]


def test_parse_llm_json_array_invalid_returns_empty() -> None:
    assert parse_llm_json_array("抱歉，我不确定") == []
    assert parse_llm_json_array("") == []


def test_parse_llm_json_array_trailing_text() -> None:
    text = '[{"title": "A"}]\n\n以上是提取的实体列表。'
    result = parse_llm_json_array(text)
    assert result == [{"title": "A"}]


# ---------------------------------------------------------------------------
# wiki generation
# ---------------------------------------------------------------------------


def test_slugify_chinese_and_ascii() -> None:
    assert slugify("数据中台") == "数据中台"
    assert slugify("Data Platform") == "data-platform"
    assert slugify("  多  空格  ") == "多-空格"
    assert slugify("") == "untitled"


def test_group_candidates_dedupes_by_slug() -> None:
    chunks = {"c1": "内容一", "c2": "内容二"}
    entities = [
        Entity(title="数据中台", entity_type="Concept", chunk_ids=["c1"]),
        Entity(title="数据中台", entity_type="Concept", chunk_ids=["c2"]),
        Entity(title="绩效考核", entity_type="Operation", chunk_ids=["c1"]),
    ]
    candidates = group_candidates_by_entities(entities, chunks)
    by_title = {c.title: c for c in candidates}
    assert set(by_title) == {"数据中台", "绩效考核"}
    # merged candidate has both chunk contents, frequency summed
    assert by_title["数据中台"].frequency == 2
    assert set(by_title["数据中台"].chunk_ids) == {"c1", "c2"}
    # page_type mapping
    assert by_title["数据中台"].page_type == "concept"
    assert by_title["绩效考核"].page_type == "concept"


def test_upsert_pages_and_links() -> None:
    engine = create_engine("sqlite:///:memory:")
    # Only wiki tables are created (knowledge base metadata is PG-only
    # in production; for this test we model pages on a dedicated base).
    KnowledgeBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    candidates = [
        # Titles share the "中台" token -> treated as related (see-also).
        WikiCandidate(title="数据中台", entity_type="Concept", page_type="concept",
                      slug="数据中台", chunk_ids=["c1"], content_parts=["..."]),
        WikiCandidate(title="中台架构", entity_type="Concept", page_type="concept",
                      slug="中台架构", chunk_ids=["c1"], content_parts=["..."]),
    ]
    pages = upsert_pages(db, "kb1", candidates)
    assert len(pages) == 2
    # re-run is idempotent (no duplicate pages)
    pages2 = upsert_pages(db, "kb1", candidates)
    assert {p.slug for p in pages2} == {"数据中台", "中台架构"}
    assert len(db.query(WikiPage).all()) == 2

    # related titles produce a link
    links = build_links(db, "kb1", pages)
    assert links == 1
    # re-running links is idempotent
    assert build_links(db, "kb1", pages) == 0
    db.close()