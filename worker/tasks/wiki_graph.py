"""Wiki / graph Celery tasks — post-ingestion knowledge construction.

Registered task classes (consumed by the scheduler's execute_modo_job):
- KbWikiBuildTask  — extract entities from a KB's chunks and build wiki pages
- KbGraphBuildTask — extract entities/relationships and write the Neo4j graph
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from api.db import get_knowledge_sessionmaker
from api.models.knowledge import DocChunk
from api.services.chat import load_chat_config
from api.services.graph import Neo4jGraphStore, build_graph
from api.services.wiki import build_wiki
from worker.tasks.scheduler import TASK_CLASS_REGISTRY

logger = logging.getLogger(__name__)

TASK_CLASS_WIKI_BUILD = "KbWikiBuildTask"
TASK_CLASS_GRAPH_BUILD = "KbGraphBuildTask"


def _load_kb_chunks(kb_id: str) -> list[dict]:
    db = get_knowledge_sessionmaker()()
    try:
        rows = (
            db.execute(
                select(DocChunk.id, DocChunk.content)
                .where(DocChunk.kb_id == kb_id, DocChunk.enabled.is_(True))
                .order_by(DocChunk.seq)
            )
            .all()
        )
        return [{"id": r.id, "content": r.content} for r in rows]
    finally:
        db.close()


def _params(task_params: str | None) -> dict:
    try:
        return json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _handle_wiki_build(job_id: str, task_params: str | None) -> dict:
    params = _params(task_params)
    kb_id = str(params.get("kbId") or params.get("kb_id") or "").strip()
    if not kb_id:
        return {"success": False, "error": "task_params must include kbId"}
    chunks = _load_kb_chunks(kb_id)
    db = get_knowledge_sessionmaker()()
    try:
        chat_cfg = load_chat_config(db)
        from api.services.graph import GraphExtractor
        from api.services.chat import ChatClient

        client = ChatClient(chat_cfg)
        extractor = GraphExtractor(client, language=params.get("language", "中文"))
        for c in chunks:
            extractor.extract_entities(c["id"], c["content"])
        entities = list(extractor.entities_by_title.values())
        chunks_by_id = {c["id"]: c["content"] for c in chunks}
        result = build_wiki(db, kb_id, entities, chunks_by_id, created_by=params.get("createdBy"))
        result["job_id"] = job_id
        return result
    finally:
        db.close()


def _handle_graph_build(job_id: str, task_params: str | None) -> dict:
    params = _params(task_params)
    kb_id = str(params.get("kbId") or params.get("kb_id") or "").strip()
    if not kb_id:
        return {"success": False, "error": "task_params must include kbId"}
    chunks = _load_kb_chunks(kb_id)
    db = get_knowledge_sessionmaker()()
    try:
        chat_cfg = load_chat_config(db)
        store = None
        if params.get("neo4jUri") and params.get("neo4jUser") is not None:
            store = Neo4jGraphStore(
                uri=params["neo4jUri"],
                user=str(params["neo4jUser"]),
                password=str(params.get("neo4jPassword") or ""),
                database=params.get("neo4jDatabase"),
            )
        result = build_graph(
            chat_cfg,
            kb_id,
            chunks,
            neo4j_store=store,
            language=params.get("language", "中文"),
        )
        result["job_id"] = job_id
        return result
    finally:
        db.close()


def register_task_handlers() -> None:
    TASK_CLASS_REGISTRY[TASK_CLASS_WIKI_BUILD] = _handle_wiki_build
    TASK_CLASS_REGISTRY[TASK_CLASS_GRAPH_BUILD] = _handle_graph_build


register_task_handlers()
