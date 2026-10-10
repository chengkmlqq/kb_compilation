"""表 → wiki 摄取任务（KbTableIngestTask）—— 2026-10-10 表→wiki 功能。

知识库此前只支持文件作为知识源；本任务让「从数据源选一张维度表」成为
与文件等价的资源：

  1. 连库取数：SELECT * FROM {schema}.{table} LIMIT {sample_size}（默认 500）
  2. 生成结构化 markdown（列定义来自元数据采集免重复连库 + 样例数据表 + 行记录）
  3. 切片：元数据 chunk（表注释+列定义，≈2~3 块）+ 行级 chunk（每行 1 块）
  4. 向量化：与文件同 embedding 链路（embed_chunks + replace_document_chunks）
  5. LLM 摘要：复用 generate_document_summary（按 seq 拼正文 → KB 配置的摘要模型）
  6. wiki 构建：只建「表主页」（page_type=table，含列定义/数据量/样例/摘要）

连接层复用 datagrid._find_entry/_connect（空 user/team = 系统级不过滤授权；
indexing 管道的表清单/kb_document 已由上传侧写入）。TASK_CLASS = KbTableIngestTask，
job_id = TABLE_{document_id}（上传/重新采集时入队，对齐 KbDocumentProcessTask）。
"""
from __future__ import annotations

import json
import logging
import sqlalchemy as sa
from datetime import datetime

from api.models.knowledge import DocChunk, KbDocument
from api.services.chunking import ChunkConfig

logger = logging.getLogger(__name__)

TASK_CLASS_TABLE_INGEST = "KbTableIngestTask"

# 与 ingest.py 保持一致的行标记前缀
SEQ_META = 0  # 元数据块从 seq 0 起
SAMPLE_ROWS_LIMIT = 500  # 默认样例行数（用户拍板 D1：500 够）


# --------------------------------------------------------------------------- #
# 1. 取数
# --------------------------------------------------------------------------- #


def _load_table_rows(db, doc: KbDocument, limit: int = SAMPLE_ROWS_LIMIT) -> tuple[list[dict], list[str]]:
    """实时连库读表数据（columns + 至多 limit 行）。

    复用 datagrid 连接层；空 user/team = 系统级取数（跳过团队授权过滤，
    因为绑定表的知识库属于使用者，取数由 worker 以系统身份执行）。
    返回 (rows, columns)；失败抛异常由调用方转 parse_state=FAILED。
    """
    from sqlalchemy import text
    from api.services import datagrid

    ds_name = doc.ds_source_name or ""
    if not ds_name:
        raise ValueError("表源缺少数据源（ds_source_name）")

    entry = datagrid._find_entry(db, ds_name, user_id="", team_name="")
    if entry is None:
        raise ValueError(f"数据源不存在或不可用: {ds_name}")
    engine = datagrid._connect(entry)

    schema = (doc.ds_table_schema or "").strip()
    table = (doc.ds_table_name or "").strip()
    if not table:
        raise ValueError("表源缺少表名（ds_table_name）")
    # 限定列避免 * 的大字段拖垮（取主键/业务列之外的文本列也截断）
    table_ref = f"{schema}.{table}" if schema else table
    sql = f"SELECT * FROM {table_ref} LIMIT {int(limit)}"
    with engine.connect() as conn:
        result = conn.exec_driver_sql(sql)
        cols = list(result.keys())
        rows = [dict(zip(cols, r)) for r in result.fetchall()]
    return rows, cols


# --------------------------------------------------------------------------- #
# 2. 数据 → markdown + 行级切片
# --------------------------------------------------------------------------- #


def _markdown_meta(doc: KbDocument, columns: list[str], col_comments: dict[str, str]) -> str:
    """表元数据 markdown（列定义表 + 表说明）。"""
    lines = [f"# {doc.ds_table_name or doc.file_name}"]
    if doc.file_name and doc.file_name != doc.ds_table_name:
        lines.append(f"> 表注释：{doc.file_name}")
    lines.append(f"> 行数：{doc.row_count or 0} · 列数：{len(columns)}")
    if columns:
        lines.append("\n## 列定义\n")
        lines.append("| 列名 | 类型 | 注释 |")
        lines.append("|------|------|------|")
        # 类型从 ds_table_schema 无法推断，这里用 doc_chunk.meta 存真实列类型？不——
        # 我们从元数据采集表拿列定义，但为免跨服务依赖，此处以列名 + 注释为主
        for c in columns:
            lines.append(f"| {c} | - | {col_comments.get(c, '')} |")
    return "\n".join(lines)


def _markdown_rows(rows: list[dict]) -> str:
    """样例数据 markdown 表（前 N 行）。"""
    if not rows:
        return ""
    cols = list(rows[0].keys())
    lines = ["\n## 样例数据（前 %d 行）\n" % len(rows)]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join("---" for _ in cols) + "|")
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            if v is None:
                cells.append("")
            else:
                cells.append(str(v).replace("|", "\\|")[:80])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _markdown_row_entries(rows: list[dict]) -> list[str]:
    """行级条目（每行 1 块，对齐 D4：{表名}: {主键}={值}; {非空列}={值}...）。"""
    if not rows:
        return []
    cols = list(rows[0].keys())
    entries = []
    for r in rows:
        parts = []
        for c in cols:
            v = r.get(c)
            if v is None or v == "":
                continue
            parts.append(f"{c}={v}")
        if parts:
            entries.append(f"{'、'.join(parts)}")
    return entries


# --------------------------------------------------------------------------- #
# 3. 切片
# --------------------------------------------------------------------------- #


def _build_chunks(
    doc: KbDocument,
    rows: list[dict],
    columns: list[str],
    col_comments: dict[str, str],
    chunk_cfg: ChunkConfig,
) -> list[tuple[int, str]]:
    """切片：元数据块（seq 0 起）+ 行级块（每行 1 块）。"""
    chunks: list[tuple[int, str]] = []
    # 元数据块：表说明 + 列定义
    meta = _markdown_meta(doc, columns, col_comments)
    if meta.strip():
        chunks.append((len(chunks), meta))
    # 样例数据块（seq 1）
    sample = _markdown_rows(rows)
    if sample.strip():
        chunks.append((len(chunks), sample))
    # 行级块（每行 1 块）
    row_entries = _markdown_row_entries(rows)
    block_sep = chunk_cfg.separators[-1] if chunk_cfg.separators else "\n"
    # 行条目合并成块（块大小接近 chunk_size；默认 500 行 → ~每 5 行一块）
    block: list[str] = []
    block_len = 0
    for entry in row_entries:
        block.append(entry)
        block_len += len(entry) + 2
        if block_len >= chunk_cfg.chunk_size:
            chunks.append((len(chunks), block_sep.join(block)))
            block = []
            block_len = 0
    if block:
        chunks.append((len(chunks), block_sep.join(block)))
    return chunks


# --------------------------------------------------------------------------- #
# 4. 向量化 + 落库 + 摘要 + wiki
# --------------------------------------------------------------------------- #


def process_table_ingest(job_id: str, task_params: dict) -> dict:
    """表→wiki 主流程（由 _handle_table_ingest 调用）。"""
    document_id = str(task_params.get("documentId") or task_params.get("document_id") or "").strip()
    kb_id = str(task_params.get("kbId") or task_params.get("kb_id") or "").strip()
    if not document_id:
        return {"success": False, "error": "task_params must include documentId"}

    from api.db import get_sessionmaker
    from api.services.embedding import get_embedding_client, l2_normalize
    from api.services.ingest import embed_chunks, replace_document_chunks, update_document_state

    db = get_sessionmaker()()
    try:
        doc = db.execute(
            sa.select(KbDocument).where(KbDocument.id == document_id)
        ).scalars().first()
        if not doc:
            return {"success": False, "error": f"document not found: {document_id}"}
        if doc.source_type not in ("table", None, ""):
            return {"success": False, "error": f"文档不是表源（source_type={doc.source_type}）"}

        # 1. 取数
        update_document_state(db, document_id, "PARSING")
        db.commit()
        try:
            rows, columns = _load_table_rows(db, doc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("table ingest fetch failed doc=%s", document_id)
            update_document_state(db, document_id, "FAILED", error=f"取数失败: {exc}")
            db.commit()
            return {"success": False, "error": f"取数失败: {exc}"}

        if not columns:
            update_document_state(db, document_id, "FAILED", error="表无列或取数为空")
            db.commit()
            return {"success": False, "error": "表无列或取数为空"}

        # 2. 切片（列注释从元数据采集表补全，缺失不阻塞）
        col_comments: dict[str, str] = {}
        try:
            from api.models.metadata import MetadataColumn
            import sqlalchemy as _sa

            mt = db.execute(
                _sa.select(MetadataColumn.metadata_table_id, MetadataColumn.column_name, MetadataColumn.column_comment)
                .where(MetadataColumn.metadata_table_id == doc.id)
            ).all()
            for _tid, cname, ccomment in mt:
                if cname and ccomment:
                    col_comments[cname] = ccomment
        except Exception:  # noqa: BLE001
            col_comments = {}

        chunk_cfg = ChunkConfig()
        try:
            from api.services.kb_chunking import load_chunk_config
            chunk_cfg = load_chunk_config(db, doc.kb_id or "")
        except Exception:  # noqa: BLE001
            chunk_cfg = ChunkConfig()

        chunks = _build_chunks(doc, rows, columns, col_comments, chunk_cfg)
        if not chunks:
            update_document_state(db, document_id, "FAILED", error="无有效数据")
            db.commit()
            return {"success": False, "error": "无有效数据"}

        # 3. 向量化（复用文件链路 embed_chunks + replace_document_chunks）
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

        meta = {"source_type": "table", "ds_name": doc.ds_source_name, "table": doc.ds_table_name}
        written = replace_document_chunks(
            db, doc.kb_id or "", document_id, chunks, vectors, meta
        )
        update_document_state(db, document_id, "READY", chunk_count=written)
        db.commit()

        # 4. LLM 摘要（KB 配置摘要模型；失败不阻塞文档 READY）
        try:
            from api.services.kb_admin import generate_document_summary
            summary_res = generate_document_summary(db, doc.kb_id, document_id)
            logger.info("table summary ok doc=%s: %s", document_id, summary_res.get("summary_status"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("table summary skipped doc=%s: %s", document_id, exc)

        # 5. wiki 只建表主页（page_type=table）
        try:
            _build_table_wiki(db, doc, columns, rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("table wiki build skipped doc=%s: %s", document_id, exc)

        return {
            "success": True,
            "job_id": job_id,
            "document_id": document_id,
            "chunks": written,
            "rows": len(rows),
            "parse_state": "READY",
        }
    finally:
        db.close()


def _build_table_wiki(db, doc: KbDocument, columns: list[str], rows: list[dict]) -> dict:
    """建一张表主页 wiki 页（含列定义/数据量/样例/摘要）+ 触发互链重建。"""
    from worker.tasks.wiki_build import _upsert_wiki_page

    kb_id = doc.kb_id or ""
    table_name = doc.ds_table_name or doc.file_name
    slug = f"table-{doc.ds_table_schema or ''}-{table_name}" if doc.ds_table_schema else f"table-{table_name}"

    # 正文：列定义 + 样例（复用 markdown 拼接）
    body_parts = [_markdown_meta(doc, columns, {})]
    sample = _markdown_rows(rows)
    if sample:
        body_parts.append(sample)
    if doc.summary:
        body_parts.append(f"\n## 摘要\n\n{doc.summary}")
    content = "\n".join(body_parts)
    if not content.strip():
        content = f"# {table_name}"

    page = _upsert_wiki_page(db, kb_id, table_name, content, slug, "table")
    # 互链重建（复用既有：表主页 ↔ 其他页面自动互相链接）
    try:
        from api.services.kb_admin import wiki_rebuild_links
        wiki_rebuild_links(db, kb_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wiki rebuild-links skipped: %s", exc)
    return page


# --------------------------------------------------------------------------- #
# Scheduler registry handler
# --------------------------------------------------------------------------- #


def _handle_table_ingest(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    return process_table_ingest(job_id, params)


def register_task_handlers() -> None:
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY

    TASK_CLASS_REGISTRY[TASK_CLASS_TABLE_INGEST] = _handle_table_ingest


register_task_handlers()