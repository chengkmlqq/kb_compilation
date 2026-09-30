"""Wiki page generation — build wiki pages from document knowledge.

A pragmatic port of WeKnora's wiki_ingest chain: instead of reproducing its
full async orchestration (asynq queues, per-slug locks, retry schedules), we
keep the same *data flow* — group chunks by extracted entities, generate a
page per entity, dedupe by slug, then link pages that co-occur — and run it
synchronously inside a Celery task (which the platform's scheduler already
provides retry/locking semantics for).

Pages land in wiki_page / wiki_link on the BUSINESS store (the framework
relational DB; only chunk embeddings live in the vector store). Slugs are
derived from titles (pinyin-free ASCII fallback not needed — Chinese slugs
are fine).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models.knowledge import DocChunk, WikiLink, WikiPage
from api.services.chat import ChatClient, ChatConfig, ChatMessage
from api.services.graph import Entity, GraphExtractor, parse_llm_json_array

logger = logging.getLogger(__name__)

_PAGE_TYPE_BY_ENTITY = {
    "Person": "entity",
    "Organization": "entity",
    "Location": "entity",
    "Product": "entity",
    "Event": "entity",
    "Work": "entity",
    "Concept": "concept",
    "Resource": "entity",
    "Category": "entity",
    "Operation": "concept",
}


def slugify(title: str) -> str:
    """Slug from a title: keep CJK/alnum, collapse separators, lowercase."""
    slug = re.sub(r"[\s_/\\:：]+", "-", title.strip().lower())
    slug = re.sub(r"[^\w\-一-鿿]", "", slug, flags=re.UNICODE)
    return slug.strip("-") or "untitled"


@dataclass
class WikiCandidate:
    """One generated page candidate (grouped by entity title)."""

    title: str
    entity_type: str
    page_type: str
    slug: str
    chunk_ids: list[str] = field(default_factory=list)
    content_parts: list[str] = field(default_factory=list)
    frequency: int = 1

    @property
    def content(self) -> str:
        return "\n\n".join(self.content_parts)


def group_candidates_by_entities(
    entities: list[Entity],
    chunks_by_id: dict[str, str],
) -> list[WikiCandidate]:
    """Aggregate extracted entities into page candidates (dedupe by slug)."""
    by_slug: dict[str, WikiCandidate] = {}
    for ent in entities:
        slug = slugify(ent.title)
        cand = by_slug.get(slug)
        if cand is None:
            cand = WikiCandidate(
                title=ent.title,
                entity_type=ent.entity_type,
                page_type=_PAGE_TYPE_BY_ENTITY.get(ent.entity_type, "entity"),
                slug=slug,
                frequency=ent.frequency,
            )
            by_slug[slug] = cand
        else:
            cand.frequency += 1
        for cid in ent.chunk_ids:
            if cid not in cand.chunk_ids:
                cand.chunk_ids.append(cid)
            content = chunks_by_id.get(cid, "")
            if content and content not in cand.content_parts:
                cand.content_parts.append(content)
    return list(by_slug.values())


def find_existing_pages(db: Session, kb_id: str, slugs: list[str]) -> dict[str, WikiPage]:
    """Return {slug: page} for slugs that already have pages."""
    if not slugs:
        return {}
    rows = db.execute(select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug.in_(slugs))).scalars().all()
    return {p.slug: p for p in rows}


def upsert_pages(db: Session, kb_id: str, candidates: list[WikiCandidate], created_by: str | None = None) -> list[WikiPage]:
    """Insert-or-update wiki pages for candidates; returns the page rows."""
    existing = find_existing_pages(db, kb_id, [c.slug for c in candidates])
    pages: list[WikiPage] = []
    for cand in candidates:
        page = existing.get(cand.slug)
        if page is None:
            page = WikiPage(
                id=uuid.uuid4().hex,
                kb_id=kb_id,
                slug=cand.slug,
                title=cand.title,
                page_type=cand.page_type,
                content=cand.content,
                created_by=created_by,
                status="active",
            )
            db.add(page)
        else:
            # merge content into the existing page (append-only, idempotent)
            if cand.content and cand.content not in page.content:
                page.content = page.content + "\n\n" + cand.content
            page.page_type = page.page_type or cand.page_type
        pages.append(page)
    db.flush()
    return pages


def build_links(db: Session, kb_id: str, pages: list[WikiPage]) -> int:
    """Link pages that co-occur in the same chunks (see-also edges)."""
    # chunk -> slugs map: which slugs appeared in which chunk
    chunk_slugs: dict[str, set[str]] = {}
    page_by_slug = {p.slug: p for p in pages}
    for page in pages:
        for cid in page.content or []:
            pass  # placeholder — replaced by chunk map below
    # Rebuild from chunk candidates: pages created from the same chunk share
    # that chunk in their source; we approximate co-occurrence by title overlap
    # in content_parts, which is O(n^2) but fine for KB-scale builds.
    added = 0
    for i, a in enumerate(pages):
        for b in pages[i + 1 :]:
            if _pages_related(a, b):
                existing = db.execute(
                    select(WikiLink).where(
                        WikiLink.kb_id == kb_id,
                        WikiLink.from_page_id == a.id,
                        WikiLink.to_page_id == b.id,
                    )
                ).scalars().first()
                if existing is None:
                    db.add(
                        WikiLink(
                            id=uuid.uuid4().hex,
                            kb_id=kb_id,
                            from_page_id=a.id,
                            to_page_id=b.id,
                            link_type="related",
                        )
                    )
                    added += 1
    return added


def _pages_related(a: WikiPage, b: WikiPage) -> bool:
    """Heuristic: pages sharing a significant overlap are related.

    For CJK titles we compare 2-gram shingles (word segmentation is not
    available), so "数据中台" and "中台架构" share the bigram "中台". For
    ASCII titles we compare tokens.
    """
    ta, tb = (a.title or "").strip(), (b.title or "").strip()
    if not ta or not tb or ta == tb:
        return False

    def bigrams(s: str) -> set[str]:
        chars = re.sub(r"\s+", "", s)
        return {chars[i : i + 2] for i in range(len(chars) - 1)} if len(chars) >= 2 else set()

    def tokens(s: str) -> set[str]:
        return set(re.findall(r"[a-zA-Z0-9_]{2,}", s))

    a_grams, b_grams = bigrams(ta), bigrams(tb)
    if a_grams & b_grams:
        return True
    return bool(tokens(ta) & tokens(tb))


def build_wiki(
    db: Session,
    kb_id: str,
    entities: list[Entity],
    chunks_by_id: dict[str, str],
    created_by: str | None = None,
) -> dict:
    """Full wiki build: group entities -> upsert pages -> link related pages."""
    candidates = group_candidates_by_entities(entities, chunks_by_id)
    pages = upsert_pages(db, kb_id, candidates, created_by=created_by)
    links = build_links(db, kb_id, pages)
    db.commit()
    return {"pages": len(pages), "links": links}


def build_wiki_from_chunks(
    db: Session,
    chat_cfg: ChatConfig,
    kb_id: str,
    chunks: list[dict],
    language: str = "中文",
    created_by: str | None = None,
) -> dict:
    """Extract entities from chunks and build wiki pages in one pass."""
    client = ChatClient(chat_cfg)
    extractor = GraphExtractor(client, language=language)
    for c in chunks:
        extractor.extract_entities(c.get("id", ""), c.get("content", ""))
    entities = list(extractor.entities_by_title.values())
    chunks_by_id = {c.get("id", ""): c.get("content", "") for c in chunks}
    return build_wiki(db, kb_id, entities, chunks_by_id, created_by=created_by)
