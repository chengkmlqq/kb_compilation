"""Document ingestion pipeline: parse -> chunk -> embed -> store.

Orchestrates the knowledge-domain write path:
1. Load file bytes (from sys_file storage record or raw bytes)
2. docreader parses to markdown (+ images)
3. TextSplitter chunks the content
4. EmbeddingClient embeds each chunk
5. doc_chunk rows are written to the BUSINESS store (framework relational DB)
   and embeddings to the vector store (kb_embedding via VectorStore)

All steps are idempotent-ish: re-ingesting a document replaces its chunks
(delete-by-document then insert), so a retry after a mid-pipeline failure
does not duplicate data. Celery tasks call the pieces directly with their own
sessions — this module takes explicit db handles (no globals).
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from api.models.knowledge import DocChunk, KbDocument
from api.services.chunking import ChunkConfig
from api.services.chunking_orch import split, split_parent_child
from api.services.embedding import EmbeddingClient
from worker.tasks.parsers.registry import parse_document_by_engine

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 80


def _doc_id() -> str:
    return uuid.uuid4().hex


def parse_document(
    file_name: str,
    file_type: str,
    content: bytes,
    parser_engine: str | None = None,
    engine_rules: list[dict] | None = None,
) -> str:
    """Parse file bytes to markdown via the selected engine.

    Delegates to the engine dispatcher; parser_engine forces a specific engine,
    engine_rules are the KB's type->engine rules. Returns markdown text.
    """
    markdown, _images, _engine = parse_document_by_engine(
        file_name, file_type, content,
        engine_rules=engine_rules,
        forced_engine=parser_engine,
    )
    return markdown


def chunk_text(
    content: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str = "auto",
) -> list[tuple[int, str]]:
    """Split markdown text into (seq, text) chunks via the adaptive engine.

    Backwards-compatible signature (chunk_size/overlap) plus an optional
    strategy; callers should prefer passing a full ChunkConfig.
    """
    if not content or not content.strip():
        return []
    cfg = ChunkConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy=strategy)
    chunks = split(content, cfg)
    return [(c.seq, c.content) for c in chunks]


def embed_chunks(client: EmbeddingClient, texts: list[str]) -> list[list[float]]:
    """Embed chunk texts; returns one vector per text (same order)."""
    if not texts:
        return []
    return client.embed_texts(texts)


def replace_document_chunks(
    db: Session,
    kb_id: str,
    document_id: str,
    chunks: list[tuple[int, str]],
    vectors: list[list[float]],
    meta: dict | None = None,
    parent_seq_by_child: dict[int, int] | None = None,
    parent_texts: dict[int, str] | None = None,
) -> int:
    """Write chunks for a document, replacing any previous chunks (idempotent).

    Chunk rows (id/kb_id/seq/content/meta) go to the BUSINESS store (`db`);
    embeddings go to the vector store (kb_embedding via VectorStore).

    When `parent_seq_by_child`/`parent_texts` are supplied (parent-child mode),
    each child row's meta carries `parent_seq` + `parent_content` so retrieval
    can match on the child vector but return the parent text.

    Returns the number of chunks written.
    """
    # Remove stale chunks first (idempotent re-ingest / retry safety), and
    # their embeddings from the vector store (new chunks get new ids).
    from api.services.vector_store import get_vector_store
    from api.models.knowledge import KbDatasource
    kb_row = db.get(KbDatasource, kb_id)
    store = get_vector_store(kb_row.vector_store_id if kb_row else None)
    stale_ids = [
        row[0]
        for row in db.execute(
            select(DocChunk.id).where(DocChunk.document_id == document_id)
        ).all()
    ]
    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    db.flush()
    store.delete_by_chunks(stale_ids)

    base_meta = meta or {}
    rows: list[dict] = []
    for (seq, text), vec in zip(chunks, vectors):
        chunk_id = _doc_id()
        row_meta = dict(base_meta)
        if parent_seq_by_child and seq in parent_seq_by_child:
            pseq = parent_seq_by_child[seq]
            row_meta["parent_seq"] = pseq
            if parent_texts and pseq in parent_texts:
                row_meta["parent_content"] = parent_texts[pseq]
        rows.append(
            {
                "id": chunk_id,
                "kb_id": kb_id,
                "document_id": document_id,
                "seq": seq,
                "content": text,
                "meta": row_meta,
                "enabled": True,
            }
        )
        store.upsert(kb_id, chunk_id, vec)
    # 2026-10-16: 批量 INSERT 替代逐条 db.add——单条语句一次拿齐全部行锁，
    # 缩短事务持锁窗口，降低并发 ingest 时的 MySQL 1213 死锁概率
    # （此前逐条 add 的二级索引间隙锁竞争是死锁根因之一）。seq 与向量逐一对应，语义不变。
    if rows:
        db.execute(insert(DocChunk), rows)
    return len(rows)


def update_document_state(
    db: Session,
    document_id: str,
    state: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    """Advance kb_document parse state (PENDING -> PARSING -> EMBEDDING -> READY / FAILED)."""
    doc = db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
    if not doc:
        return
    doc.parse_state = state
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if error is not None:
        doc.parse_error = error


def _persist_document_images(
    markdown: str,
    document_id: str,
    images: dict[str, str],
) -> str:
    """文档解析图片持久化（2026-10-07 文档图片可理解改造）：
    images {name: data_url} → 存对象存储 kb_documents/<doc_id>/images/<name>，
    md 中内联 data URL 替换为 ``![name](kb-image://<doc_id>/images/<name>)`` 引用
    （问答时按引用读回注入视觉模型）。

    兼容两种解析器契约：
    - images dict 非空（MinerU 官方/云端：images map + md data URL）
    - images dict 为空（自建 mineru shim：只返回 md，图片为 data URL 内联）——
      此时从 md 正则提取 data:image URL 兜底持久化。
    解析引擎不产出图片时原样返回。
    """
    import base64
    import re

    # 1) 归一：images dict 内的图 + md 内联的 data URL 图（去重）
    refs_seen: set[str] = set()

    def _put(name: str, raw: bytes) -> bool:
        try:
            from api.services.storage import put_bytes, resolve_new_path

            rel = f"kb_documents/{document_id}/images/{name}"
            put_bytes(resolve_new_path(rel), raw, content_type="image/png")
            return True
        except Exception:  # noqa: BLE001 - 存储不可用不阻断 ingest
            return False

    new_md = markdown
    # 1a) images dict 优先（key 为图名）
    for name, data_url in (images or {}).items():
        try:
            raw = base64.b64decode(data_url.split(",")[-1])
        except Exception:  # noqa: BLE001
            continue
        if not raw or not _put(name, raw):
            continue
        ref = f"![{name}](kb-image://{document_id}/images/{name})"
        if data_url in new_md:
            new_md = new_md.replace(data_url, ref)
        else:
            pat = re.escape(data_url)
            new_md = re.sub(rf"!\[[^\]]*\]\({pat}\)", ref, new_md)
        refs_seen.add(f"data:{data_url}")

    # 1b) md 内联 data URL 兜底（自建 mineru shim 契约：无 images map）
    for m in re.finditer(r"!\[[^\]]*\]\((data:image/[^)]+)\)", new_md):
        data_url, alt = m.group(1), m.group(0)
        if data_url in refs_seen:
            continue
        try:
            raw = base64.b64decode(data_url.split(",")[-1])
        except Exception:  # noqa: BLE001
            continue
        name = f"{len(refs_seen)}.png"
        if not raw or not _put(name, raw):
            continue
        ref = f"![{name}](kb-image://{document_id}/images/{name})"
        refs_seen.add(data_url)
        new_md = new_md.replace(m.group(0), ref)
    return new_md


def ingest_document(
    kb_db: Session,
    document: KbDocument,
    file_content: bytes,
    embedding_client: EmbeddingClient,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    parser_engine: str | None = None,
    chunk_cfg: ChunkConfig | None = None,
    process_config: dict | None = None,
) -> dict:
    """Full pipeline for one document. Returns a summary dict.

    - kb_db: business-store session (doc_chunk / kb_document live on the
      framework relational store)
    - file_content: raw bytes of the document
    - chunk_cfg: optional explicit chunking config (highest priority)
    - process_config: optional file-level processing overrides (对齐 WeKnora
      upload process_config). Shape: {"chunking": {...}, "parser_engine_rules": [...]}.
      切片配置优先级：文件级 chunking（merge 到 KB 级之上）> KB 级 indexing_strategy.chunking
      > 默认值。解析引擎规则：文件级 parser_engine_rules > KB 级。
      为 None/空时按 KB 级配置走（此前 process_document 不传 chunk_cfg，导致 KBConfigModal
      设置的切片配置未生效，本函数内补上 KB 级回落）。
    """
    result: dict = {"document_id": document.id, "chunks": 0, "parse_state": "FAILED"}

    pc: dict = process_config if isinstance(process_config, dict) else {}
    pc_chunking = pc.get("chunking") if isinstance(pc.get("chunking"), dict) else None
    pc_rules = pc.get("parser_engine_rules") if isinstance(pc.get("parser_engine_rules"), list) else None

    # resolve chunking config: 文件级 > KB 级 > 默认
    try:
        from api.services.kb_chunking import load_chunking
        from api.services.chunking import ChunkConfig as _CC

        if pc_chunking is not None:
            merged_raw = load_chunking(kb_db, document.kb_id)
            for key, val in pc_chunking.items():
                if val is not None:
                    merged_raw[key] = val
            chunk_cfg = _CC.from_dict(merged_raw)
        elif chunk_cfg is None:
            chunk_cfg = _CC.from_dict(load_chunking(kb_db, document.kb_id))
    except Exception:  # noqa: BLE001 - config lookup must not block ingest
        if chunk_cfg is None:
            chunk_cfg = ChunkConfig(
                chunk_size=chunk_size, chunk_overlap=chunk_overlap, strategy="auto"
            )

    # load the KB's engine rules (type -> engine); resolution happens in the
    # dispatcher, which also applies the availability fallback
    engine_rules: list[dict] | None = pc_rules
    vlm_server_url = ""
    try:
        if engine_rules is None:
            from api.services.kb_chunking import load_engine_rules

            engine_rules = load_engine_rules(kb_db, document.kb_id)
        # 2026-10-07: VLM（图表语义理解）——KB 级 vlm_config.server_url 注入解析
        from api.models.knowledge import KbDatasource

        kb_row = kb_db.execute(
            select(KbDatasource).where(KbDatasource.id == document.kb_id)
        ).scalars().first()
        if kb_row and kb_row.vlm_config:
            vlm_server_url = (kb_row.vlm_config or {}).get("server_url", "") or ""
            # 2026-10-17: VLM 改为知识库绑定的视觉模型(不再依赖旧 server_url/env)
            _vlm = kb_row.vlm_config or {}
            if _vlm.get("enabled"):
                _vlm_mid = str(_vlm.get("model_id") or "").strip()
                _vlm_url = ""
                try:
                    from api.services.models import resolve_vision_model_config
                    _vcfg = resolve_vision_model_config(db=kb_db, model_id=_vlm_mid) if _vlm_mid else None
                    if _vcfg and _vcfg.get("base_url"):
                        _vlm_url = str(_vcfg["base_url"])
                except Exception:  # noqa: BLE001
                    logger.warning("resolve vision model failed for kb %s", document.kb_id, exc_info=True)
                if _vlm_url:
                    vlm_server_url = _vlm_url
    except Exception:  # noqa: BLE001 - config lookup must not block ingest
        engine_rules = None

    # 1. parse
    update_document_state(kb_db, document.id, "PARSING")
    kb_db.commit()
    used_engine = parser_engine
    # 2026-10-18 解析超时保护：docreader Parser.parse_file 对个别 docx 会无限
    # 卡死（实测 DOC_ed384dd1 RUNNING 154 分钟无返回），任务无超时兜底 →
    # 永卡 RUNNING + 孤儿恢复每 60min 循环重投又卡。线程 + 超时：超时即显式
    # FAILED（防状态卡死）；僵尸线程低频可接受。
    import concurrent.futures as _cf

    _PARSE_TIMEOUT_S = 1200  # 20 分钟；正常 docx 解析 <1min，大文件留余量
    try:
        with _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-parse") as _ex:
            _fut = _ex.submit(
                parse_document_by_engine,
                document.file_name,
                document.file_ext or "",
                file_content,
                engine_rules=engine_rules,
                forced_engine=parser_engine,
                vlm_server_url=vlm_server_url,
            )
            markdown, images, used_engine = _fut.result(timeout=_PARSE_TIMEOUT_S)
    except _cf.TimeoutError:
        logger.error(
            "parse timeout after %ss for document %s (engine=%s)",
            _PARSE_TIMEOUT_S, document.id, used_engine,
        )
        update_document_state(kb_db, document.id, "FAILED", error=f"parse timeout (> {_PARSE_TIMEOUT_S}s)")
        kb_db.commit()
        result["error"] = f"parse timeout (> {_PARSE_TIMEOUT_S}s)"
        result["success"] = False
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("parse failed for document %s", document.id)
        update_document_state(kb_db, document.id, "FAILED", error=f"parse: {e}")
        kb_db.commit()
        result["error"] = str(e)
        result["success"] = False  # 2026-10-05: 显式失败标志(execute_modo_job 据此落 FAILED, 防假成功)
        return result

    if not markdown.strip():
        update_document_state(
            kb_db,
            document.id,
            "FAILED",
            error=(
                "解析结果为空：该文件没有可抽取的文本层（常见于扫描件/纯图片 PDF、"
                "加密文档或内容编码不受支持的 mhtml）。请转成可复制文本的 PDF/Markdown 后重新上传。"
            ),
        )
        kb_db.commit()
        result["error"] = "empty parse result"
        result["success"] = False
        return result

    # 2026-10-07: 文档图片持久化——md 内联 data URL → kb-image:// 引用 + 存对象存储
    markdown = _persist_document_images(markdown, document.id, images)

    # 2. chunk (adaptive; parent-child when enabled)
    parent_seq_by_child: dict[int, int] = {}
    parent_texts: dict[int, str] = {}
    if chunk_cfg.enable_parent_child:
        children, parents = split_parent_child(markdown, chunk_cfg)
        chunks = [(c.seq, c.content) for c in children]
        parent_seq_by_child = {c.seq: c.parent_seq for c in children if c.parent_seq is not None}
        # store parent texts; child rows reference them via meta so retrieval
        # can return parent context
        parent_texts = {p.seq: p.content for p in parents}
    else:
        chunks = [(c.seq, c.content) for c in split(markdown, chunk_cfg)]

    if not chunks:
        update_document_state(kb_db, document.id, "FAILED", error="no chunks produced")
        kb_db.commit()
        result["error"] = "no chunks produced"
        result["success"] = False
        return result

    # 3. embed（失败必须显式 FAILED，否则文档停在 EMBEDDING 残留——历史坑）
    update_document_state(kb_db, document.id, "EMBEDDING")
    kb_db.commit()
    texts = [text for _, text in chunks]
    try:
        vectors = embed_chunks(embedding_client, texts)
    except Exception as e:  # noqa: BLE001
        logger.exception("embed failed for document %s", document.id)
        update_document_state(kb_db, document.id, "FAILED", error=f"embed: {e}")
        kb_db.commit()
        result["error"] = f"embed: {e}"
        result["success"] = False
        return result

    # 4. store
    # MySQL 1213 死锁是瞬态错误（官方建议重启事务重试）：并发 ingest 时多个事务
    # 同时对 doc_chunk 二级索引加 gap/next-key 锁，MySQL 随机选一个受害者回滚。
    # 这里重试最多 3 次（含首跑），每次重试前 rollback 已失效事务；非死锁或重试
    # 耗尽则显式回写 parse_state=FAILED（对齐 parse/embed 两段的兜底，防止文档
    # 卡在 EMBEDDING 残留——历史坑：store 段无兜底导致「任务失败仍显示向量化中」）。
    meta = {"source_file": document.file_name, "parse_engine": used_engine}
    written = 0
    for attempt in range(3):
        try:
            written = replace_document_chunks(
                kb_db, document.kb_id, document.id, chunks, vectors, meta,
                parent_seq_by_child=parent_seq_by_child if chunk_cfg.enable_parent_child else None,
                parent_texts=parent_texts if chunk_cfg.enable_parent_child else None,
            )
            break
        except Exception as e:  # noqa: BLE001
            orig = getattr(e, "orig", None)
            is_deadlock = (
                isinstance(e, OperationalError)
                and orig is not None
                and getattr(orig, "args", None)
                and orig.args[0] == 1213
            )
            kb_db.rollback()  # 异常后事务已失效，先回滚才能继续
            if not is_deadlock or attempt == 2:
                logger.exception("store failed for document %s", document.id)
                update_document_state(kb_db, document.id, "FAILED", error=f"store: {e}")
                kb_db.commit()
                result["error"] = str(e)
                result["success"] = False
                return result
            logger.warning(
                "deadlock(1213) writing chunks for document %s, retry %d/2",
                document.id, attempt + 1,
            )
            time.sleep(0.3 * (attempt + 1))
    update_document_state(kb_db, document.id, "READY", chunk_count=written)
    kb_db.commit()

    result["chunks"] = written
    result["parse_state"] = "READY"
    logger.info("ingested document %s: %d chunks (engine=%s)", document.id, written, used_engine)
    return result