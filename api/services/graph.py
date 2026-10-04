"""Knowledge graph services — LLM entity/relationship extraction + Neo4j write.

Ported from WeKnora graph.go + neo4j repository:
- Entity extraction per chunk (LLM, JSON array, merged by title with frequency
  counting and chunk-id accumulation)
- Relationship extraction in batches (min-entity guard, strength 5-10)
- Neo4j write via MERGE (apoc.merge.node / apoc.merge.relationship), nodes
  keyed by (name, kb_id), page_id/page_slug attached when wiki pages exist

Prompts live in api/assets/prompts/graph_extraction.yaml (Apache-2.0, WeKnora).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from api.services.chat import ChatClient, ChatConfig, ChatMessage

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompts"
MIN_ENTITIES_FOR_RELATION = 2
MAX_CONCURRENT_EXTRACTIONS = 4
RELATION_BATCH_SIZE = 8

ENTITY_TYPES = [
    "Person", "Organization", "Location", "Product", "Event", "Date",
    "Work", "Concept", "Resource", "Category", "Operation",
]


def _load_prompt(prompt_id: str) -> str:
    """Load a prompt template by id from the YAML assets."""
    with open(_PROMPT_DIR / "graph_extraction.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for tpl in data.get("templates", []):
        if tpl.get("id") == prompt_id or (prompt_id == "default" and tpl.get("default")):
            return tpl.get("content", "")
    raise ValueError(f"prompt template not found: {prompt_id}")


def parse_llm_json_array(text: str) -> list[dict]:
    """Extract a JSON array from an LLM response (tolerant of code fences)."""
    if not text or not text.strip():
        return []
    cleaned = text.strip()
    # strip ```json ... ``` fences
    m = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1).strip()
    # find the first [ ... ] span
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("failed to parse LLM JSON array; response head: %s", text[:200])
        return []
    return [item for item in parsed if isinstance(item, dict)]


@dataclass
class Entity:
    title: str
    entity_type: str = ""
    description: str = ""
    frequency: int = 1
    chunk_ids: list[str] = field(default_factory=list)


@dataclass
class Relationship:
    source: str
    target: str
    relation_type: str = ""
    description: str = ""
    strength: int = 5


class GraphExtractor:
    """LLM-driven entity/relationship extraction (per chunk + batch merge)."""

    def __init__(self, chat_client: ChatClient, language: str = "中文"):
        self.chat_client = chat_client
        self.language = language
        self.entities_by_title: dict[str, Entity] = {}
        self.relationships: list[Relationship] = []

    def _system(self, prompt_content: str) -> str:
        return prompt_content.replace("{{language}}", self.language)

    def extract_entities(self, chunk_id: str, content: str) -> list[Entity]:
        """Extract entities from one chunk; merge into the collector by title."""
        if not content or not content.strip():
            return []
        prompt = _load_prompt("default_extract_entities")
        messages = [
            ChatMessage(role="system", content=self._system(prompt)),
            ChatMessage(role="user", content=content),
        ]
        try:
            resp = self.chat_client.chat(messages)
        except Exception:
            logger.exception("LLM entity extraction failed for chunk %s", chunk_id)
            return []
        items = parse_llm_json_array(resp)
        out: list[Entity] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            existing = self.entities_by_title.get(title)
            if existing is None:
                ent = Entity(
                    title=title,
                    entity_type=str(item.get("type") or ""),
                    description=str(item.get("description") or ""),
                    frequency=1,
                    chunk_ids=[chunk_id],
                )
                self.entities_by_title[title] = ent
                out.append(ent)
            else:
                if chunk_id not in existing.chunk_ids:
                    existing.chunk_ids.append(chunk_id)
                existing.frequency += 1
                out.append(existing)
        return out

    def extract_relationships(self, chunk_ids: list[str], chunk_texts: list[str]) -> None:
        """Extract relationships for a batch of chunks + their merged entities."""
        entities = list(self.entities_by_title.values())
        if len(entities) < MIN_ENTITIES_FOR_RELATION:
            return
        prompt = _load_prompt("default_extract_relationships")
        entity_payload = [
            {"title": e.title, "type": e.entity_type, "description": e.description}
            for e in entities
        ]
        user_content = (
            "Entities:\n"
            + json.dumps(entity_payload, ensure_ascii=False)
            + "\n\nText:\n"
            + "\n".join(chunk_texts)
        )
        messages = [
            ChatMessage(role="system", content=self._system(prompt)),
            ChatMessage(role="user", content=user_content),
        ]
        try:
            resp = self.chat_client.chat(messages)
        except Exception:
            logger.exception("LLM relationship extraction failed")
            return
        items = parse_llm_json_array(resp)
        for item in items:
            source = str(item.get("source") or "").strip()
            target = str(item.get("target") or "").strip()
            if not source or not target:
                continue
            try:
                strength = int(item.get("strength") or 5)
            except (TypeError, ValueError):
                strength = 5
            self.relationships.append(
                Relationship(
                    source=source,
                    target=target,
                    relation_type=str(item.get("type") or ""),
                    description=str(item.get("description") or ""),
                    strength=strength,
                )
            )


# ---------------------------------------------------------------------------
# Neo4j write
# ---------------------------------------------------------------------------


class Neo4jGraphStore:
    """Write extracted entities/relationships into Neo4j (MERGE semantics)."""

    def __init__(self, uri: str, user: str, password: str, database: str | None = None):
        self._uri = uri
        self._auth = (user, password)
        self._database = database

    def write_graph(self, kb_id: str, entities: list[Entity], relationships: list[Relationship]) -> None:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(self._uri, auth=self._auth)
        try:
            with driver.session(database=self._database) as session:
                session.execute_write(self._write_nodes, kb_id, entities)
                session.execute_write(self._write_relationships, kb_id, relationships)
        finally:
            driver.close()

    @staticmethod
    def _write_nodes(tx, kb_id: str, entities: list[Entity]) -> None:
        node_data = [
            {
                "name": e.title,
                "kb_id": kb_id,
                "props": {"description": e.description, "entity_type": e.entity_type},
                "chunks": e.chunk_ids,
                "labels": ["Entity"],
                "page_id": None,
                "page_slug": None,
            }
            for e in entities
        ]
        query = """
            UNWIND $data AS row
            MERGE (n:Entity {name: row.name, kb_id: row.kb_id})
            SET n += row.props
            SET n.chunks = [x IN coalesce(n.chunks, []) | x] + row.chunks
            RETURN distinct 'done' AS result
        """
        tx.run(query, data=node_data)

    @staticmethod
    def _write_relationships(tx, kb_id: str, relationships: list[Relationship]) -> None:
        rel_data = [
            {
                "source": r.source,
                "target": r.target,
                "kb_id": kb_id,
                "type": r.relation_type or "RELATED_TO",
                # Neo4j 属性只接受标量/标量数组，不能存 Map —— description/strength
                # 必须摊平成独立属性（此前写成 rel.attributes=<map> 会直接报
                # Neo.ClientError.Statement.TypeError，建图 100% 失败）。
                "description": r.description,
                "strength": r.strength,
            }
            for r in relationships
        ]
        query = """
            UNWIND $data AS row
            MATCH (a:Entity {name: row.source, kb_id: row.kb_id})
            MATCH (b:Entity {name: row.target, kb_id: row.kb_id})
            MERGE (a)-[rel:`RELATED_TO` {kb_id: row.kb_id}]->(b)
            SET rel.type = row.type
            SET rel.description = row.description
            SET rel.strength = row.strength
            RETURN distinct 'done' AS result
        """
        tx.run(query, data=rel_data)


# ---------------------------------------------------------------------------
# Convenience: full build for a knowledge base
# ---------------------------------------------------------------------------


def build_graph(
    chat_cfg: ChatConfig,
    kb_id: str,
    chunks: list[dict],
    neo4j_store: Neo4jGraphStore | None = None,
    language: str = "中文",
) -> dict:
    """Extract entities/relationships from chunks and persist to Neo4j.

    chunks: [{"id": ..., "content": ...}] — chunk rows from the knowledge store.
    Returns {"entities": n, "relationships": m}.
    """
    client = ChatClient(chat_cfg)
    extractor = GraphExtractor(client, language=language)

    # 1. entity extraction per chunk (concurrent, capped)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_EXTRACTIONS) as pool:
        futures = {
            pool.submit(extractor.extract_entities, c["id"], c["content"]): c
            for c in chunks
            if c.get("content")
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("concurrent entity extraction failed")

    # 2. relationship extraction in batches
    for i in range(0, len(chunks), RELATION_BATCH_SIZE):
        batch = chunks[i : i + RELATION_BATCH_SIZE]
        extractor.extract_relationships(
            [c["id"] for c in batch],
            [c["content"] for c in batch],
        )

    # 3. persist
    if neo4j_store is not None and (extractor.entities_by_title or extractor.relationships):
        neo4j_store.write_graph(
            kb_id,
            list(extractor.entities_by_title.values()),
            extractor.relationships,
        )

    return {
        "entities": len(extractor.entities_by_title),
        "relationships": len(extractor.relationships),
    }