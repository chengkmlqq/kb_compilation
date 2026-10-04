"""Wiki / graph Celery tasks — post-ingestion knowledge construction.

Registered task classes (consumed by the scheduler's execute_modo_job):
- KbWikiBuildTask  — extract entities from a KB's chunks and build wiki pages
- KbGraphBuildTask — extract entities/relationships and write the Neo4j graph
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from api.db import get_sessionmaker
from api.config import get_settings
from api.models.knowledge import DocChunk
from api.services.chat import load_chat_config
from api.services.graph import Neo4jGraphStore, build_graph
from api.services.wiki import build_wiki
from worker.tasks.scheduler import TASK_CLASS_REGISTRY

logger = logging.getLogger(__name__)

TASK_CLASS_WIKI_BUILD = "KbWikiBuildTask"
TASK_CLASS_GRAPH_BUILD = "KbGraphBuildTask"


def _load_kb_chunks(kb_id: str) -> list[dict]:
    db = get_sessionmaker()()
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
    db = get_sessionmaker()()
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
    db = get_sessionmaker()()
    try:
        chat_cfg = load_chat_config(db)
        store = _resolve_neo4j_store(params)
        result = build_graph(
            chat_cfg,
            kb_id,
            chunks,
            neo4j_store=store,
            language=params.get("language", "中文"),
        )
        result["job_id"] = job_id
        if store is None:
            # 未配置 Neo4j：抽取仍会跑（实体/关系计数有意义），只是不落图
            logger.warning("graph build for kb=%s: Neo4j 未配置，仅抽取未落图", kb_id)
        return result
    finally:
        db.close()


def _resolve_neo4j_store(params: dict) -> Neo4jGraphStore | None:
    """构造 Neo4j 写入目标：task_params 优先，其次容器 env（NEO4J_*）。

    compose 通过 env 注入 NEO4J_URI/USERNAME/PASSWORD/DATABASE，正常部署下
    无需在任务参数里重复传；task_params 保留用于测试/临时改指向。
    """
    settings = get_settings()
    uri = params.get("neo4jUri") or settings.NEO4J_URI
    user = params.get("neo4jUser")
    user = str(user) if user is not None else settings.NEO4J_USERNAME
    password = params.get("neo4jPassword")
    password = str(password) if password is not None else settings.NEO4J_PASSWORD
    database = params.get("neo4jDatabase") or settings.NEO4J_DATABASE or None
    if not (uri and password):
        return None
    return Neo4jGraphStore(uri=uri, user=user, password=password, database=database)


def register_task_handlers() -> None:
    TASK_CLASS_REGISTRY[TASK_CLASS_WIKI_BUILD] = _handle_wiki_build
    TASK_CLASS_REGISTRY[TASK_CLASS_GRAPH_BUILD] = _handle_graph_build


register_task_handlers()
