"""Document processing Celery tasks — parse + chunk + embed + store.

Task wiring follows the scheduler contract:
    TASK_CLASS_REGISTRY[task_class] = handler(job_id, task_params)
so modo_cron_task entries (or API enqueues) can trigger document ingestion.

Two tasks:
- `worker.tasks.doc_process.process_document_job` — full pipeline for one
  document (parse -> chunk -> embed -> write doc_chunk).
- `worker.tasks.doc_process.embed_document_job` — embed-only re-run (e.g. after
  the embedding model changed), reusing stored chunk text.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.knowledge import DocChunk, KbDocument
from api.services.embedding import get_embedding_client, l2_normalize
from api.services.ingest import ingest_document
from worker.tasks.scheduler import TASK_CLASS_REGISTRY

logger = logging.getLogger(__name__)

# task_class values accepted in modo_cron_task / API enqueue.
TASK_CLASS_PROCESS_DOC = "KbDocumentProcessTask"
TASK_CLASS_EMBED_DOC = "KbDocumentEmbedTask"


def _load_document(document_id: str) -> KbDocument | None:
    db = get_sessionmaker()()
    try:
        return db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
    finally:
        db.close()


def _load_document_bytes(storage_path: str | None) -> bytes:
    """Load file bytes from a storage_path (local disk or minio:// bucket/key)."""
    if not storage_path:
        return b""
    try:
        from api.services.storage import get_bytes

        return get_bytes(storage_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to read document bytes from %s: %s", storage_path, e)
        return b""


def process_document(document_id: str, file_content: bytes, parser_engine: str | None = None) -> dict:
    """Run the full ingest pipeline for a document (called by the job task).

    - file_content: preloaded bytes (inline / test path). When empty, bytes
      are loaded from the document's `storage_path` (upload flow writes the
      file to local disk and records the absolute path on kb_document).
    """
    db = get_sessionmaker()()
    try:
        document = db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
        if not document:
            return {"success": False, "error": f"document not found: {document_id}"}

        if not file_content:
            file_content = _load_document_bytes(document.storage_path)
            if not file_content:
                return {
                    "success": False,
                    "error": f"document bytes unavailable (storage_path={document.storage_path!r})",
                }

        client = get_embedding_client(db)
        return ingest_document(db, document, file_content, client, parser_engine=parser_engine)
    finally:
        db.close()


def embed_document(document_id: str) -> dict:
    """Re-embed an existing document's chunks (embedding-model-change path).

    Chunks live in the business store; their new vectors go to the vector
    store (kb_embedding) via VectorStore.
    """
    from api.services.vector_store import get_vector_store

    db = get_sessionmaker()()
    try:
        chunks = (
            db.execute(
                select(DocChunk).where(DocChunk.document_id == document_id).order_by(DocChunk.seq)
            )
            .scalars()
            .all()
        )
        if not chunks:
            return {"success": False, "error": "no chunks to embed"}
        client = get_embedding_client(db)
        texts = [c.content for c in chunks]
        vectors = client.embed_texts(texts)
        store = get_vector_store()
        for chunk, vec in zip(chunks, vectors):
            store.upsert(chunk.kb_id, chunk.id, l2_normalize(vec))
        return {"success": True, "document_id": document_id, "embedded": len(chunks)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler registry handlers (called with job_id, task_params)
# ---------------------------------------------------------------------------


def _handle_process_document(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    document_id = str(params.get("documentId") or params.get("document_id") or "").strip()
    if not document_id:
        return {"success": False, "error": "task_params must include documentId"}
    kb_id = str(params.get("kbId") or params.get("kb_id") or "").strip()
    parser_engine = params.get("parserEngine") or params.get("parser_engine")
    # Bytes are not storable in modo_job.task_params (text column); callers
    # fetch from object storage via the storage path on the document row.
    file_content = b""
    result = process_document(document_id, file_content, parser_engine)
    result["job_id"] = job_id

    # 自动触发 wiki 构建（AgentGateway 链路）：文档解析/向量化成功后，
    # enqueue KbAgentGatewayTask → agent-gateway 内跑技能 kb-wiki-builder
    # （build_wiki.py）读 chunks → LLM → 写 wiki 页。技能 ZIP 随任务下发
    # （agent_gateway.py 1A/2A 从 kb_skill 表取 package_zip），网关侧无需预装。
    # 成功标志：ingest_document 返回 parse_state == "READY"（无 success 键）。
    if result.get("parse_state") == "READY" and kb_id:
        try:
            enqueued = _enqueue_wiki_skill_task(kb_id, document_id)
            result["wiki_skill_triggered"] = enqueued
        except Exception as exc:  # noqa: BLE001 — 触发失败不使文档处理任务失败
            logger.warning("failed to trigger wiki skill task: %s", exc)
            result["wiki_skill_triggered"] = False
    return result


def _enqueue_wiki_build_task(kb_id: str, document_id: str) -> bool:
    """enqueue KbSkillWikiBuildTask（worker 内执行 wiki 构建）。

    LLM 配置（WIKI_LLM_BASE_URL/MODEL/API_KEY）由 worker 容器 env 直接读取，
    无需经 gateway runner 注入。任务参数携带 kb_id/document_id。
    """
    from worker.celery_app import celery_app

    task_params = json.dumps(
        {"kbId": kb_id, "documentId": document_id, "language": "中文"},
        ensure_ascii=False,
    )
    job_id = f"WIKI_BUILD_{uuid.uuid4().hex[:12]}"
    try:
        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="default",
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时返回 False，不抛
        logger.warning("failed to enqueue wiki build task: %s", exc)
        return False
    # 写 modo_job 行（任务监控可见）——与 scheduler._enqueue_job 契约一致
    from api.db import get_sessionmaker
    from api.models.framework import Job

    db = get_sessionmaker()()
    try:
        db.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class="KbSkillWikiBuildTask",
                queue_name="default",
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
            )
        )
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        return False
    finally:
        db.close()


def _enqueue_wiki_skill_task(kb_id: str, document_id: str) -> bool:
    """向 agent-gateway 提交技能构建任务（KbAgentGatewayTask）。

    config 携带:
      - skill: 技能名（kb_skill 表 scope=system 的 wiki 构建技能）
      - kb_id / document_id: 技能脚本定位文档（读 chunks 后生成 wiki 页）
      - kb_base_url: 技能脚本调 kb API 的入口（宿主 nginx）
    """
    from api.config import get_settings

    settings = get_settings()
    skill_name = (settings.WIKI_SKILL_NAME or "kb-wiki-builder").strip()
    kb_base_url = (settings.KB_PUBLIC_BASE_URL or "http://10.1.215.50").strip()
    llm_base = (settings.WIKI_LLM_BASE_URL or "").strip()
    llm_model = (settings.WIKI_LLM_MODEL or "").strip()
    llm_key = (settings.WIKI_LLM_API_KEY or "").strip()

    from worker.celery_app import celery_app

    # gateway runner 注入技能环境: kb_id→WEKNORA_KB_ID, doc_name→WEKNORA_DOC_NAME,
    # model/base_url/api_key→WEKNORA_LLM_*（技能脚本 llm_config 优先读取）
    llm_cfg = {}
    if llm_base:
        llm_cfg["base_url"] = llm_base
    if llm_model:
        llm_cfg["model"] = llm_model
    if llm_key:
        llm_cfg["api_key"] = llm_key
    task_params = json.dumps(
        {
            "input": (
                "请完成知识库 wiki 构建任务：使用技能工具 run_skill_script("
                f"skill_name='{skill_name}', script='build_wiki.py') 执行技能脚本，"
                f"把知识库 {kb_id} 中的文档 {document_id} 编译进 wiki。"
                "不要使用 MCP 工具查询或写入知识库，不要调用 list_skills/load_skill 之外的工具；"
                "脚本会从环境变量 WEKNORA_KB_ID / WEKNORA_DOC_NAME 读取目标并自动完成全部工作，"
                "直接运行它即可。"
            ),
            "config": {
                "skill": skill_name,
                "kb_id": kb_id,
                "doc_name": document_id,
                "kb_base_url": kb_base_url,
                **llm_cfg,
            },
        },
        ensure_ascii=False,
    )
    job_id = f"WIKI_SKILL_{uuid.uuid4().hex[:12]}"
    try:
        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="default",
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时返回 False，不抛
        logger.warning("failed to enqueue wiki skill task: %s", exc)
        return False
    # 写 modo_job 行（任务监控可见）——与 scheduler._enqueue_job 契约一致
    from api.db import get_sessionmaker
    from api.models.framework import Job

    db = get_sessionmaker()()
    try:
        db.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class="KbAgentGatewayTask",
                queue_name="default",
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
            )
        )
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        return False
    finally:
        db.close()


def _handle_embed_document(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    document_id = str(params.get("documentId") or params.get("document_id") or "").strip()
    if not document_id:
        return {"success": False, "error": "task_params must include documentId"}
    result = embed_document(document_id)
    result["job_id"] = job_id
    return result


def register_task_handlers() -> None:
    """Register doc-processing handlers into the scheduler registry.

    Called at worker startup (worker/tasks/__init__.py imports this module).
    """
    TASK_CLASS_REGISTRY[TASK_CLASS_PROCESS_DOC] = _handle_process_document
    TASK_CLASS_REGISTRY[TASK_CLASS_EMBED_DOC] = _handle_embed_document


register_task_handlers()
