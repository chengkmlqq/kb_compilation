"""预制 markdown + 切片摄取任务（KbPreChunkedIngestTask）—— 2026-10-20 方案B。

外部系统（data_supply_shandong 等）本地已完成 doc→md 转换与文本切片，本任务
让这些产物直接进入知识库，跳过 kb 侧的解析（parse）与切片（chunk）两步：

  1. 读文档行（md 源文件已由上传侧持久化，作为 preview/download 源）
  2. 读切片 sidecar（kb_documents/{kb_id}/{doc_id}.chunks.json，上传侧写入）
  3. 校验/过滤切片（空 content 剔除；无有效切片 → FAILED）
  4. 向量化：与文件同链路（embed_chunks + replace_document_chunks）
  5. 状态 READY 后触发下游：摘要 / 构建引擎 / 题材分析（复用
     doc_process._trigger_post_ready，与文件解析链完全一致）

与文件上传链的关系：本任务只在「外部预制切片」路径使用（上传端点为
POST /kbs/{kb_id}/documents/pre-chunked）；普通上传仍走 KbDocumentProcessTask
（parse→chunk→embed）。重解析（reparse）会按 kb 配置重新解析/切分，覆盖
预切切片——语义为「重解析 = 放弃外部预制切片」。

Task wiring follows the scheduler contract:
    TASK_CLASS_REGISTRY[task_class] = handler(job_id, task_params)
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa

from api.models.knowledge import KbDocument
from api.services.ingest import (
    embed_chunks,
    replace_document_chunks,
    update_document_state,
)

logger = logging.getLogger(__name__)

TASK_CLASS_PRECHUNK_INGEST = "KbPreChunkedIngestTask"


def _load_chunks_json(chunks_path: str) -> list[dict]:
    """读切片 sidecar（storage layer，本地磁盘或 MinIO）。"""
    if not chunks_path:
        return []
    try:
        from api.services.storage import get_bytes

        raw = get_bytes(chunks_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read pre-chunked sidecar %s: %s", chunks_path, exc)
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError) as exc:  # noqa: BLE001
        logger.warning("pre-chunked sidecar %s invalid json: %s", chunks_path, exc)
        return []
    if isinstance(data, dict):
        data = data.get("chunks") or data.get("items") or []
    if not isinstance(data, list):
        return []
    return [it for it in data if isinstance(it, dict)]


def _normalize_chunks(items: list[dict]) -> list[tuple[int, str]]:
    """切片归一：保留传入 seq（缺失/非法则按序补），剔除空 content。"""
    out: list[tuple[int, str]] = []
    for idx, it in enumerate(items):
        content = str(it.get("content") or "").strip()
        if not content:
            continue
        seq_val = it.get("seq")
        if seq_val is None:
            seq_val = it.get("chunk_index") or it.get("index")
        if isinstance(seq_val, (int, str)):
            try:
                seq = int(seq_val)
            except (TypeError, ValueError):
                seq = idx
        else:
            seq = idx
        out.append((seq, content))
    return out


def process_prechunked_ingest(job_id: str, task_params: dict) -> dict:
    """预制 md+切片摄取主流程（由 _handle_prechunk_ingest 调用）。"""
    document_id = str(task_params.get("documentId") or task_params.get("document_id") or "").strip()
    kb_id = str(task_params.get("kbId") or task_params.get("kb_id") or "").strip()
    chunks_path = str(task_params.get("chunksPath") or task_params.get("chunks_path") or "").strip()
    # 操作者身份 cookie（透传给编排脚本调平台 API；2026-10-12 对齐文件链）
    caller_cookie = str(task_params.get("cookie") or "")
    if not document_id:
        return {"success": False, "error": "task_params must include documentId"}

    from api.db import get_sessionmaker
    from api.services.embedding import get_embedding_client

    db = get_sessionmaker()()
    try:
        doc = db.execute(
            sa.select(KbDocument).where(KbDocument.id == document_id)
        ).scalars().first()
        if not doc:
            return {"success": False, "error": f"document not found: {document_id}"}

        # 1. 读切片 sidecar
        update_document_state(db, document_id, "PARSING")
        db.commit()
        items = _load_chunks_json(chunks_path)
        chunks = _normalize_chunks(items)
        if not chunks:
            update_document_state(
                db, document_id, "FAILED",
                error="预制切片为空（sidecar 缺失/格式非法/内容全空）",
            )
            db.commit()
            return {"success": False, "error": "no pre-chunked chunks available"}

        # 2. 向量化（复用文件链路 embed_chunks + replace_document_chunks）
        update_document_state(db, document_id, "EMBEDDING")
        db.commit()
        texts = [t for _, t in chunks]
        try:
            client = get_embedding_client(db, kb_id=doc.kb_id or "")
            vectors = embed_chunks(client, texts)
        except Exception as exc:  # noqa: BLE001
            logger.exception("embed failed doc=%s", document_id)
            update_document_state(db, document_id, "FAILED", error=f"embed: {exc}")
            db.commit()
            return {"success": False, "error": f"embed: {exc}"}

        meta = {"source_file": doc.file_name or "", "parse_engine": "prechunked"}
        written = replace_document_chunks(
            db, doc.kb_id or "", document_id, chunks, vectors, meta
        )
        update_document_state(db, document_id, "READY", chunk_count=written)
        db.commit()

        # 3. 触发下游（摘要 / 构建引擎 / 题材分析，与文件解析链同一规则）
        out: dict = {
            "success": True,
            "job_id": job_id,
            "document_id": document_id,
            "chunks": written,
            "parse_state": "READY",
        }
        if kb_id:
            try:
                from worker.tasks.doc_process import _trigger_post_ready

                out.update(_trigger_post_ready(kb_id, document_id, job_id, caller_cookie))
            except Exception as exc:  # noqa: BLE001 — 触发失败不使摄取任务失败
                logger.warning("post-ready trigger skipped doc=%s: %s", document_id, exc)
        return out
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Scheduler registry handler
# --------------------------------------------------------------------------- #


def _handle_prechunk_ingest(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    return process_prechunked_ingest(job_id, params)


def register_task_handlers() -> None:
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY

    TASK_CLASS_REGISTRY[TASK_CLASS_PRECHUNK_INGEST] = _handle_prechunk_ingest


register_task_handlers()
