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

import json
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


def context_template(content: str, title: str = "", abstract: str = "") -> str:
    """向量化文本 XML 模板（2026-10-25 对齐 duowen es.py::context_template）。

    结构化包装让向量「感知」标题/摘要语义（duowen 检索质量关键之一）：
      <document-slice><metadata><title>…</title><abstract>…</abstract>
      </metadata><chunk>正文</chunk></document-slice>
    """
    abstract_tag = f"<abstract>{abstract}</abstract>" if abstract else ""
    if abstract_tag:
        return (
            "<document-slice>\n"
            f"    <metadata>\n        <title>{title}</title>\n        {abstract_tag}\n    </metadata>\n"
            f"    <chunk>{content}</chunk>\n"
            "</document-slice>"
        )
    return (
        "<document-slice>\n"
        f"    <metadata>\n        <title>{title}</title>\n    </metadata>\n"
        f"    <chunk>{content}</chunk>\n"
        "</document-slice>"
    )


def replace_document_chunks(
    db: Session,
    kb_id: str,
    document_id: str,
    chunks: list[tuple[int, str]],
    vectors: list[list[float]],
    meta: dict | None = None,
    parent_seq_by_child: dict[int, int] | None = None,
    parent_texts: dict[int, str] | None = None,
    image_chunks: list[dict] | None = None,
    image_vectors: list[list[float]] | None = None,
) -> int:
    """Write chunks for a document, replacing any previous chunks (idempotent).

    Chunk rows (id/kb_id/seq/content/meta) go to the BUSINESS store (`db`);
    embeddings go to the vector store (kb_embedding via VectorStore).

    When `parent_seq_by_child`/`parent_texts` are supplied (parent-child mode),
    each child row's meta carries `parent_seq` + `parent_content` so retrieval
    can match on the child vector but return the parent text.

    `image_chunks` (2026-10-19, 对齐 WeKnora image:multimodal)：解析期 VLM 生成
    的图片语义子 chunk（chunk_type=image_ocr/image_caption），随文本 chunks
    一并写库 + upsert embedding；`image_vectors` 与其一一对应。

    2026-10-25 增强入库：调用方先经 write_doc_chunk_rows 落切片（供增强编排
    读 doc_chunk），增强完成后此函数只做向量 upsert（带 enhance 字段）。
    兼容旧调用（一步到位）：本函数内部自动落切片再写向量，行为不变。

    Returns the number of chunks written.
    """
    rows, image_rows, chunk_ids = build_doc_chunk_rows(
        db, kb_id, document_id, chunks, meta,
        parent_seq_by_child=parent_seq_by_child,
        parent_texts=parent_texts,
        image_chunks=image_chunks,
    )
    # 2026-10-16: 批量 INSERT 替代逐条 db.add——单条语句一次拿齐全部行锁，
    # 缩短事务持锁窗口，降低并发 ingest 时的 MySQL 1213 死锁概率
    # （此前逐条 add 的二级索引间隙锁竞争是死锁根因之一）。seq 与向量逐一对应，语义不变。
    # 旧切片 id（重解析/重摄入时需先删向量介质里的旧向量）
    stale_ids = [
        row[0]
        for row in db.execute(
            select(DocChunk.id).where(DocChunk.document_id == document_id)
        ).all()
    ]
    write_doc_chunk_rows(db, document_id, rows)
    return upsert_chunk_vectors(
        db, kb_id, rows, vectors, image_rows=image_rows, image_vectors=image_vectors,
        stale_chunk_ids=stale_ids,
    )


def build_doc_chunk_rows(
    db: Session,
    kb_id: str,
    document_id: str,
    chunks: list[tuple[int, str]],
    meta: dict | None = None,
    parent_seq_by_child: dict[int, int] | None = None,
    parent_texts: dict[int, str] | None = None,
    image_chunks: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """构造切片行（不落库）。返回 (文本 rows, 图片 rows, 全部 chunk_id 列表)。"""
    base_meta = meta or {}
    rows: list[dict] = []
    for seq, text in chunks:
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
    image_rows: list[dict] = []
    for idx, ic in enumerate(image_chunks or []):
        cid = ic.get("id") or _doc_id()
        row_meta = dict(ic.get("meta") or {})
        row_meta.update(base_meta)
        image_rows.append(
            {
                "id": cid,
                "kb_id": kb_id,
                "document_id": document_id,
                "seq": ic.get("seq", 0),
                "content": ic.get("content", ""),
                "meta": row_meta,
                "chunk_type": ic.get("chunk_type", "text"),
                "enabled": True,
            }
        )
    chunk_ids = [r["id"] for r in rows] + [r["id"] for r in image_rows]
    return rows, image_rows, chunk_ids


def write_doc_chunk_rows(db: Session, document_id: str, rows: list[dict]) -> None:
    """删除旧切片并批量写入新切片行（业务库 doc_chunk）。"""
    from api.models.knowledge import DocChunk

    stale_ids = [
        row[0]
        for row in db.execute(
            select(DocChunk.id).where(DocChunk.document_id == document_id)
        ).all()
    ]
    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    db.flush()
    if rows:
        db.execute(insert(DocChunk), rows)
    return None


def upsert_chunk_vectors(
    db: Session,
    kb_id: str,
    rows: list[dict],
    vectors: list[list[float]],
    image_rows: list[dict] | None = None,
    image_vectors: list[list[float]] | None = None,
    enhance_by_chunk: dict[str, dict] | None = None,
    stale_chunk_ids: list[str] | None = None,
) -> int:
    """向量 upsert（向量介质）：文本行与向量一一对应；图片行有向量才 upsert。

    2026-10-25 增强：enhance_by_chunk {chunk_id: enhance_dict} 非空时走
    upsert_with_enhance（PG enhance_meta 列 / ES enhance_* 字段同 doc 写入）。
    stale_chunk_ids：被替换掉的旧切片向量（重解析/重摄取代价——先删再写）。
    """
    from api.models.knowledge import KbDatasource
    from api.services.vector_store import get_vector_store

    kb_row = db.get(KbDatasource, kb_id)
    store = get_vector_store(kb_row.vector_store_id if kb_row else None)
    if stale_chunk_ids:
        store.delete_by_chunks(stale_chunk_ids)
    for row, vec in zip(rows, vectors):
        enh = (enhance_by_chunk or {}).get(row["id"])
        if enh:
            store.upsert_with_enhance(kb_id, row["id"], vec, enh)
        else:
            store.upsert(kb_id, row["id"], vec)
    # 2026-10-19 图片语义子 chunk（image_ocr/image_caption）：与文本 chunks
    # 同批写库 + 向量化（对齐 WeKnora image:multimodal 产出可检索图片 chunk）。
    # image_vectors 与 image_chunks 一一对应；embed 降级时向量缺失 → 仅写业务库
    # （keyword 检索仍可命中），有向量的才 upsert 向量库。
    for idx, ic in enumerate(image_rows or []):
        cid = ic.get("id") or ""
        if image_vectors and idx < len(image_vectors):
            store.upsert(kb_id, cid, image_vectors[idx])
    return len(rows) + len(image_rows or [])


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
    vlm_config_raw: dict = {}
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
            vlm_config_raw = dict(kb_row.vlm_config or {})
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

    # 2026-10-19 图片语义子 chunk（对齐 WeKnora image:multimodal）：KB 开启
    # vlm_config.enabled 且解析出图片 → VLM OCR + Caption，生成 image_ocr/
    # image_caption chunk。失败不阻断 ingest（图片增强是尽力而为）。
    image_chunks: list[dict] = []
    if images:
        try:
            from api.services.image_chunks import build_image_chunks

            image_chunks = build_image_chunks(
                db=kb_db,
                kb_id=document.kb_id,
                document_id=document.id,
                file_name=document.file_name,
                file_ext=document.file_ext or "",
                images=images,
                vlm_config=vlm_config_raw,
                start_seq=len(chunks),
            )
            if image_chunks:
                logger.info(
                    "doc %s: %d image chunks (image_ocr/image_caption) generated",
                    document.id, len(image_chunks),
                )
        except Exception:  # noqa: BLE001 - 图片增强失败不阻断 ingest
            logger.warning("image chunks generation failed for doc %s", document.id, exc_info=True)
            image_chunks = []

    # 2.5 切片落库（供增强编排 n1 读 doc_chunk）——2026-10-25 增强链路：
    # 无增强时按原路径（embed → replace_document_chunks 一步入库）；
    # 有增强时先落切片，编排 LLM 逐片加工（读 doc_chunk）后再向量化入库。
    meta = {"source_file": document.file_name, "parse_engine": used_engine}
    enhance_by_chunk: dict[str, dict] | None = None
    kb_enhance_cfg: dict = {}
    doc_process_config: dict = {}
    try:
        from api.models.knowledge import KbDatasource

        kb_row = kb_db.get(KbDatasource, document.kb_id)
        kb_enhance_cfg = dict((kb_row.enhancement_config or {}) if kb_row else {})
    except Exception:  # noqa: BLE001 — 配置读取失败 = 不增强，不阻断
        kb_enhance_cfg = {}
    if document.process_config:
        try:
            parsed_pc = json.loads(document.process_config)
            if isinstance(parsed_pc, dict):
                doc_process_config = parsed_pc
        except (TypeError, json.JSONDecodeError):
            doc_process_config = {}
    try:
        from api.services.chunk_enhance import enhancement_enabled

        enhance_on = enhancement_enabled(kb_enhance_cfg, doc_process_config)
    except Exception:  # noqa: BLE001 — 解析失败 = 不增强
        enhance_on = False

    chunk_rows: list[dict] = []
    stale_ids: list[str] = []
    if enhance_on:
        # 先落切片（编排 n1 从 doc_chunk 读）→ 同步门增强 → 带增强向量化入库
        try:
            from api.services.ingest import build_doc_chunk_rows

            chunk_rows, image_rows_build, _ids = build_doc_chunk_rows(
                kb_db, document.kb_id, document.id, chunks, meta,
                parent_seq_by_child=parent_seq_by_child if chunk_cfg.enable_parent_child else None,
                parent_texts=parent_texts if chunk_cfg.enable_parent_child else None,
            )
            stale_ids = [
                row[0]
                for row in kb_db.execute(
                    select(DocChunk.id).where(DocChunk.document_id == document.id)
                ).all()
            ]
            write_doc_chunk_rows(kb_db, document.id, chunk_rows)
            kb_db.commit()
        except Exception as e:  # noqa: BLE001 — 落库失败不阻断：退回无增强一步入库
            logger.exception("pre-write chunks failed for doc %s", document.id)
            kb_db.rollback()
            chunk_rows = []
        if chunk_rows:
            # 触发增强编排（同步门：run 终态后读 enhancement）
            enhancement = None
            try:
                from api.services.chunk_enhance import run_chunk_enhance_sync

                cookie = str(doc_process_config.get("x_next_identity") or "")
                enhancement = run_chunk_enhance_sync(
                    kb_db, document.kb_id, document.id, cookie=cookie
                )
            except Exception as exc:  # noqa: BLE001 — 编排失败不阻断入库
                logger.warning("chunk enhance failed for doc %s: %s", document.id, exc)
                enhancement = None
            if enhancement:
                # 按 seq → chunk_id 映射组装 enhance_by_chunk
                enhance_by_chunk = {}
                seq_to_chunk_id = {int(r["seq"]): r["id"] for r in chunk_rows}
                for c in enhancement.get("chunks") or []:
                    cid = seq_to_chunk_id.get(int(c.get("seq") or -1))
                    if not cid:
                        continue
                    enhance_by_chunk[cid] = {
                        "title": str(enhancement.get("title") or ""),
                        "abstract": str(enhancement.get("abstract") or ""),
                        "doc_keywords": list(enhancement.get("doc_keywords") or []),
                        "chunk_keywords": list(c.get("keywords") or []),
                        "chunk_questions": list(c.get("questions") or []),
                    }
                result["enhanced"] = True
                logger.info(
                    "doc %s enhanced: %d chunks", document.id, len(enhance_by_chunk)
                )

    # 3. embed（失败必须显式 FAILED，否则文档停在 EMBEDDING 残留——历史坑）
    # 2026-10-25 向量化文本对齐 duowen：增强开启且有摘要/标题时用
    # <document-slice> XML 模板包装（标题/摘要进向量语义），否则裸正文
    # （行为与接入前一致）。
    update_document_state(kb_db, document.id, "EMBEDDING")
    kb_db.commit()
    doc_enh_title = ""
    doc_enh_abstract = ""
    if enhance_by_chunk:
        first_enh = next(iter(enhance_by_chunk.values()), None)
        if first_enh:
            doc_enh_title = str(first_enh.get("title") or "")
            doc_enh_abstract = str(first_enh.get("abstract") or "")
    texts: list[str] = []
    if enhance_by_chunk and (doc_enh_title or doc_enh_abstract):
        for _, text in chunks:
            texts.append(context_template(text, doc_enh_title, doc_enh_abstract))
    else:
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
    # 图片子 chunk 向量化（失败降级：图片 chunk 不入向量库，仍写业务库）
    image_vectors: list[list[float]] = []
    if image_chunks:
        try:
            image_vectors = embed_chunks(
                embedding_client, [ic["content"] for ic in image_chunks]
            )
        except Exception:  # noqa: BLE001
            logger.warning("image chunks embed failed for doc %s", document.id, exc_info=True)
            image_vectors = []

    # 4. store
    # MySQL 1213 死锁是瞬态错误（官方建议重启事务重试）：并发 ingest 时多个事务
    # 同时对 doc_chunk 二级索引加 gap/next-key 锁，MySQL 随机选一个受害者回滚。
    # 这里重试最多 3 次（含首跑），每次重试前 rollback 已失效事务；非死锁或重试
    # 耗尽则显式回写 parse_state=FAILED（对齐 parse/embed 两段的兜底，防止文档
    # 卡在 EMBEDDING 残留——历史坑：store 段无兜底导致「任务失败仍显示向量化中」）。
    written = 0
    for attempt in range(3):
        try:
            if chunk_rows:
                # 增强链路：切片已先落库（2.5 段）——这里只写向量（带 enhance）
                written = upsert_chunk_vectors(
                    kb_db, document.kb_id, chunk_rows, vectors,
                    image_rows=None, image_vectors=image_vectors or None,
                    enhance_by_chunk=enhance_by_chunk,
                    stale_chunk_ids=stale_ids if stale_ids else None,
                )
            else:
                written = replace_document_chunks(
                                kb_db, document.kb_id, document.id, chunks, vectors, meta,
                                parent_seq_by_child=parent_seq_by_child if chunk_cfg.enable_parent_child else None,
                                parent_texts=parent_texts if chunk_cfg.enable_parent_child else None,
                                image_chunks=image_chunks or None,
                                image_vectors=image_vectors or None,
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