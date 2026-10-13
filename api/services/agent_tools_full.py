#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 工具注册层 —— 对齐 WeKnora internal/agent/tools 全目录（2026-10-13）。

注册 function-calling 工具（schema + executor），由 routers/agents.py 装配注入，
经 allowed_tools 白名单裁剪（空=全部允许）。全部只读/受控：
- 推理：thinking / todo_write（无副作用占位）
- RAG：knowledge_search(=kb_search) / grep_chunks / list_knowledge_chunks /
       get_document_info / database_query（只读 SELECT + 安全表白名单）
- 图谱：query_knowledge_graph（Neo4j 只读，GraphReader）
- Wiki 读：wiki_search / wiki_read_page / wiki_read_source_doc / wiki_flag_issue / wiki_read_issue
- Wiki 写：wiki_write_page / wiki_replace_text / wiki_rename_page / wiki_delete_page / wiki_update_issue
- 数据：data_schema / data_analysis（CSV/XLSX → DuckDB 内存表执行 SELECT）
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from sqlalchemy import select, text

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 工具目录（前端 tool-catalog 与装配共用）
# ─────────────────────────────────────────────────────────────
TOOL_CATALOG: list[dict] = [
    {"name": "thinking", "group": "推理", "description": "显式思考标注：多步推理/自反思时使用，无副作用。", "capability": ""},
    {"name": "todo_write", "group": "推理", "description": "把复杂任务拆解为可跟踪子任务并记录，无副作用。", "capability": ""},
    {"name": "knowledge_search", "group": "RAG 检索", "description": "知识库混合检索（向量+关键词），返回文档片段与分数。", "capability": "vector,keyword"},
    {"name": "kb_search", "group": "RAG 检索", "description": "知识库检索（knowledge_search 兼容别名）。", "capability": "vector,keyword"},
    {"name": "grep_chunks", "group": "RAG 检索", "description": "关键词精确检索当前知识库分块，返回命中片段。", "capability": "vector,keyword"},
    {"name": "list_knowledge_chunks", "group": "RAG 检索", "description": "分页列出知识库文档分块（可指定文档）。", "capability": "vector,keyword"},
    {"name": "get_document_info", "group": "RAG 检索", "description": "读取文档元信息（文件名/解析状态/分块数/摘要）。", "capability": "vector,keyword"},
    {"name": "database_query", "group": "RAG 检索", "description": "对知识库元数据库执行只读 SELECT（安全表白名单+行数上限）。", "capability": "vector,keyword"},
    {"name": "query_knowledge_graph", "group": "图谱", "description": "查询当前知识库的知识图谱（Neo4j，只读），返回节点与关系。", "capability": "graph"},
    {"name": "wiki_search", "group": "Wiki", "description": "在当前知识库 wiki 页面中检索。", "capability": "wiki"},
    {"name": "wiki_read_page", "group": "Wiki", "description": "读取 wiki 页面全文（slug）。", "capability": "wiki"},
    {"name": "wiki_read_source_doc", "group": "Wiki", "description": "读取页面对应的源文档分块原文。", "capability": "wiki"},
    {"name": "wiki_flag_issue", "group": "Wiki", "description": "在 wiki 页面登记问题（issue，pending 状态）。", "capability": "wiki"},
    {"name": "wiki_read_issue", "group": "Wiki", "description": "列出页面/知识库的 wiki 问题。", "capability": "wiki"},
    {"name": "wiki_update_issue", "group": "Wiki", "description": "更新 wiki 问题状态（pending/ignored/resolved）。", "capability": "wiki"},
    {"name": "wiki_write_page", "group": "Wiki 写", "description": "创建或更新 wiki 页面（按 slug 幂等 upsert）。", "capability": "wiki"},
    {"name": "wiki_replace_text", "group": "Wiki 写", "description": "在 wiki 页面中替换文本后保存。", "capability": "wiki"},
    {"name": "wiki_rename_page", "group": "Wiki 写", "description": "修改 wiki 页面标题（slug 不变）。", "capability": "wiki"},
    {"name": "wiki_delete_page", "group": "Wiki 写", "description": "软删除 wiki 页面（按 slug）。", "capability": "wiki"},
    {"name": "data_schema", "group": "数据分析", "description": "列出知识库中的表格文件（CSV/XLSX）及其表结构/列/样例。", "capability": "vector,keyword"},
    {"name": "data_analysis", "group": "数据分析", "description": "对 CSV/Excel 文件执行 SQL 分析（DuckDB 内存表：所有 sheet 装载进 data 表，源 sheet 名在 __sheet_name 列，SQL 用 FROM data）。", "capability": "vector,keyword"},
]

CATALOG_BY_NAME = {t["name"]: t for t in TOOL_CATALOG}
DATA_EXTS = {"csv", "xlsx"}
# 全上下文执行器工具名（per_kb_executor 分派用）
EXECUTOR_NAMES = {t["name"] for t in TOOL_CATALOG}


def _fn_schema(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


def _txt(v: Any, cap: int = 8000) -> str:
    s = str(v)
    return s[:cap] + ("…" if len(s) > cap else "")


# ─────────────────────────────────────────────────────────────
# 内部助手
# ─────────────────────────────────────────────────────────────
def _infer_col_type(samples: list) -> str:
    """从前 200 行样本推断 DuckDB 列类型（INTEGER / DOUBLE / VARCHAR）。"""
    if not samples:
        return "VARCHAR"
    vals = [v for v in samples if v is not None and str(v).strip() != ""]
    if not vals:
        return "VARCHAR"
    if all(_is_int(v) for v in vals):
        return "BIGINT"
    if all(_is_num(v) for v in vals):
        return "DOUBLE"
    return "VARCHAR"


def _is_int(v) -> bool:
    try:
        int(str(v).strip().replace(",", ""))
        return True
    except Exception:  # noqa: BLE001
        return False


def _is_num(v) -> bool:
    try:
        float(str(v).strip().replace(",", ""))
        return True
    except Exception:  # noqa: BLE001
        return False


def _chunk_hits_to_text(hits, cap: int = 6000) -> str:
    if not hits:
        return "（检索无命中）"
    lines = []
    for i, h in enumerate(hits, 1):
        title = (getattr(h, "document_title", "") or "").strip() or (getattr(h, "document_id", "") or "")[:8]
        content = (getattr(h, "content", "") or "").strip()
        if len(content) > 400:
            content = content[:400] + "…"
        score = getattr(h, "score", 0) or 0
        lines.append(f"[{i}] {title} (score={score:.3f})\n{content}")
    return _txt("\n\n".join(lines), cap)


def _graph_reader():
    try:
        from api.services.graph_query import get_graph_reader
        return get_graph_reader()
    except Exception:  # noqa: BLE001
        return None


def _parse_data_file(kb_doc) -> list[dict]:
    """解析表格文件为 [{"table": 表名, "columns": [...], "rows": [[...]]}]（懒解析）。"""
    ext = (kb_doc.file_ext or "").lower().lstrip(".")
    path = kb_doc.storage_path or ""
    if not path:
        raise ValueError(f"文档无存储路径: {kb_doc.file_name}")
    raw = open(path, "rb").read()
    if ext == "xlsx":
        from api.services import xlsx_lite
        sheets = xlsx_lite.read_sheets(path)  # xlsx_lite 吃文件路径
        out = []
        for sh in sheets:
            rows = sh.get("rows") or []
            if not rows:
                continue
            header = rows[0]
            out.append({"table": sh.get("name") or "sheet", "columns": header, "rows": rows[1:]})
        return out
    if ext == "csv":
        import csv as _csv
        import io
        text_body = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text_body = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text_body is None:
            raise ValueError("CSV 解码失败（utf-8/gbk 均不可）")
        rdr = list(_csv.reader(io.StringIO(text_body)))
        if not rdr:
            raise ValueError("CSV 为空")
        header = rdr[0]
        return [{"table": kb_doc.file_name.rsplit(".", 1)[0], "columns": header, "rows": rdr[1:]}]
    raise ValueError(f"不支持的表格类型: {ext}")


# ─────────────────────────────────────────────────────────────
# 装配入口
# ─────────────────────────────────────────────────────────────
def build_full_tool_context(
    db,
    agent,
    *,
    kb,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> tuple[list[dict], Callable[[dict], str] | None]:
    """按当前知识库装配全套工具（WeKnora 对齐）。返回 (tools, executor)。"""
    from api.models.knowledge import DocChunk, KbDocument, WikiPage, WikiPageIssue
    from api.services.agents import config_from_dict, resolve_agent_qa_overrides
    from api.services.retrieval import config_from_kb, hybrid_search
    from api.services import kb_admin

    cfg = config_from_dict(agent.config or {})
    retrieval_cfg = config_from_kb(kb, resolve_agent_qa_overrides(agent)) if kb else None
    top_k = max(1, min(int(getattr(cfg, "embedding_top_k", 10) or 10), 20))
    kb_id = kb.id if kb else ""

    def _hybrid(query: str, topk: int = 0, keyword_only: bool = False) -> list:
        if not kb:
            return []
        emb = None
        if not keyword_only and retrieval_cfg and getattr(retrieval_cfg, "vector_enabled", True):
            try:
                from api.services.embedding import get_embedding_client
                emb = get_embedding_client(db, user_id=caller_user_id, team_name=caller_team_name,
                                            is_sys_admin=is_sys_admin).embed_query(query)
            except Exception:  # noqa: BLE001
                emb = None
        return hybrid_search(db, kb.id, query, emb, retrieval_cfg)[: (topk or top_k)]

    executors: dict[str, Callable[[dict], str]] = {}

    # ── 推理（无副作用占位）──
    executors["thinking"] = lambda tc: "ok（思考已记录）"
    executors["todo_write"] = lambda tc: "ok（子任务清单已记录）"

    # ── RAG ──
    def _search(args: dict) -> str:
        q = str(args.get("query") or "").strip()
        if not q:
            return "缺少 query"
        hits = _hybrid(q)
        return _chunk_hits_to_text(hits)

    executors["knowledge_search"] = _search
    executors["kb_search"] = _search

    def _grep(args: dict) -> str:
        q = str(args.get("query") or "").strip()
        if not q:
            return "缺少 query"
        hits = _hybrid(q, keyword_only=True)
        return _chunk_hits_to_text(hits)

    executors["grep_chunks"] = _grep

    def _list_chunks(args: dict) -> str:
        doc_id = str(args.get("document_id") or "").strip()
        off = max(0, int(args.get("offset") or 0))
        lim = max(1, min(int(args.get("limit") or 20), 100))
        stmt = select(DocChunk).where(DocChunk.kb_id == kb_id, DocChunk.enabled.is_(True))
        if doc_id:
            stmt = stmt.where(DocChunk.document_id == doc_id)
        chunks = db.execute(stmt.order_by(DocChunk.document_id, DocChunk.seq).offset(off).limit(lim)).scalars().all()
        lines = []
        for c in chunks:
            lines.append(f"[{c.document_id[:8]}#{c.seq}] {_txt(c.content, 300)}")
        return _txt("\n\n".join(lines) or "（无分块）")

    executors["list_knowledge_chunks"] = _list_chunks

    def _doc_info(args: dict) -> str:
        doc_id = str(args.get("document_id") or "").strip()
        stmt = select(KbDocument).where(KbDocument.kb_id == kb_id)
        if doc_id:
            stmt = stmt.where(KbDocument.id == doc_id)
        docs = db.execute(stmt.order_by(KbDocument.created_at.desc()).limit(20)).scalars().all()
        lines = []
        for d in docs:
            lines.append(
                f"- {d.file_name} | id={d.id} | ext={d.file_ext} | parse={d.parse_state} | "
                f"chunks={d.chunk_count} | rows={d.row_count or '—'}\n  {d.summary or ''}"[:500]
            )
        return _txt("\n".join(lines) or "（无文档）")

    executors["get_document_info"] = _doc_info

    def _db_query(args: dict) -> str:
        sql = str(args.get("sql") or "").strip()
        lim = max(1, min(int(args.get("limit") or 200), 500))
        if not sql:
            return "缺少 sql"
        m = re.match(r"^\s*(select)\b", sql, re.I)
        if not m:
            return "仅允许 SELECT 语句"
        if re.search(r";\s*[^\s]", sql, re.I):
            return "禁止多语句"
        # 安全表白名单（WeKnora 对齐：knowledge_bases/knowledges/chunks → kb 对应表）
        tbls = re.findall(r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.I)
        safe = {"kb_datasource", "kb_document", "doc_chunk", "wiki_page", "wiki_folder", "wiki_page_issue"}
        if any(t.lower() not in safe for t in tbls):
            return f"仅允许查询安全表: {sorted(safe)}"
        wrapped = f"SELECT * FROM ( {sql} ) AS _r LIMIT {lim}"
        try:
            result = db.execute(text(wrapped))
            cols = [str(c) for c in result.keys()]
            rows = result.fetchall()
        except Exception as e:  # noqa: BLE001
            return f"查询失败: {e}（可用 SELECT * FROM <安全表> LIMIT 5 查看真实列名）"
        lines = [" | ".join(cols)]
        for r in rows[:200]:
            lines.append(" | ".join(_txt(v, 80) for v in r))
        lines.append(f"（{len(rows)} 行结果，显示前 200）")
        return _txt("\n".join(lines))

    executors["database_query"] = _db_query

    # ── 图谱 ──
    def _graph(args: dict) -> str:
        gr = _graph_reader()
        if gr is None:
            return "知识图谱未启用（未配置 Neo4j）"
        q = str(args.get("query") or "").strip()
        lim = max(1, min(int(args.get("limit") or 100), 200))
        try:
            d = gr.search(kb_id, q, limit=lim) if q else gr.overview(kb_id, limit=lim)
        except Exception as e:  # noqa: BLE001
            return f"图谱查询失败: {e}"
        return _txt(json.dumps(d, ensure_ascii=False, indent=1), 6000)

    executors["query_knowledge_graph"] = _graph

    # ── Wiki 读 ──
    def _wiki_search(args: dict) -> str:
        q = str(args.get("query") or "").strip()
        if not q:
            return "缺少 query"
        d = kb_admin.wiki_search(db, kb_id, q, limit=max(1, min(int(args.get("limit") or 20), 50)))
        pages = d.get("pages") or d.get("results") or []
        lines = []
        for p in pages[:20]:
            slug = p.get("slug", "") if isinstance(p, dict) else getattr(p, "slug", "")
            title = p.get("title", "") if isinstance(p, dict) else getattr(p, "title", "")
            lines.append(f"- {title}（slug={slug}）")
        return _txt("\n".join(lines) or "（wiki 无命中）")

    executors["wiki_search"] = _wiki_search

    def _read_page(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return "缺少 slug"
        p = kb_admin.get_wiki_page(db, kb_id, slug)
        if not p:
            return f"页面不存在: {slug}"
        return _txt(f"# {p.get('title','')}\n\n{p.get('content','')}", 8000)

    executors["wiki_read_page"] = _read_page

    def _read_source(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return "缺少 slug"
        p = kb_admin.get_wiki_page(db, kb_id, slug)
        if not p:
            return f"页面不存在: {slug}"
        refs = p.get("source_refs") or []
        doc_ids = []
        for r in refs:
            if isinstance(r, dict) and r.get("doc"):
                doc_ids.append(str(r["doc"]))
        if not doc_ids:
            return f"页面 {slug} 无源文档引用"
        chunks = db.execute(
            select(DocChunk).where(DocChunk.kb_id == kb_id, DocChunk.document_id.in_(doc_ids))
            .order_by(DocChunk.document_id, DocChunk.seq).limit(30)
        ).scalars().all()
        lines = [f"[{c.document_id[:8]}#{c.seq}] {_txt(c.content, 300)}" for c in chunks]
        return _txt("\n\n".join(lines) or "（无源文分块）")

    executors["wiki_read_source_doc"] = _read_source

    def _flag_issue(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return "缺少 slug"
        issue = WikiPageIssue(
            id=__import__("uuid").uuid4().hex[:32],
            kb_id=kb_id, slug=slug,
            issue_type=str(args.get("issue_type") or "agent_reported")[:64],
            description=str(args.get("description") or "")[:2000],
            detail=str(args.get("detail") or "")[:4000],
            status="pending", reported_by=caller_user_id or "agent",
        )
        db.add(issue)
        db.commit()
        return f"已登记问题 {issue.id}（{issue.issue_type}）"

    executors["wiki_flag_issue"] = _flag_issue

    def _read_issue(args: dict) -> str:
        d = kb_admin.wiki_list_issues(db, kb_id, slug=str(args.get("slug") or ""), status=str(args.get("status") or ""))
        items = d.get("issues") or d.get("items") or []
        lines = [f"- {i.get('issue_type','')} [{i.get('status','')}] {i.get('description','')[:120]} (id={i.get('id','')})"
                 for i in items[:50]]
        return _txt("\n".join(lines) or "（无问题）")

    executors["wiki_read_issue"] = _read_issue

    def _update_issue(args: dict) -> str:
        issue_id = str(args.get("issue_id") or "").strip()
        status = str(args.get("status") or "").strip()
        if not issue_id or status not in ("pending", "ignored", "resolved"):
            return "需要 issue_id 且 status ∈ pending/ignored/resolved"
        d = kb_admin.wiki_update_issue_status(db, kb_id, issue_id, status)
        return f"问题 {issue_id} → {status}"

    executors["wiki_update_issue"] = _update_issue

    # ── Wiki 写 ──
    def _write_page(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return "缺少 slug"
        title = str(args.get("title") or "").strip() or slug
        content = str(args.get("content") or "").strip()
        if not content:
            return "内容为空"
        page = db.execute(select(WikiPage).where(WikiPage.kb_id == kb_id,
                                                 WikiPage.slug == slug)).scalars().first()
        if page:
            page.title = title
            page.content = content
            db.add(page)
            db.commit()
            return f"已更新页面 {slug}"
        page = WikiPage(
            id=__import__("uuid").uuid4().hex[:32], kb_id=kb_id, slug=slug, title=title,
            content=content, page_type=str(args.get("page_type") or "concept")[:32],
            status="active",
        )
        db.add(page)
        db.commit()
        return f"已创建页面 {slug}"

    executors["wiki_write_page"] = _write_page

    def _replace_text(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        old = str(args.get("old_text") or "")
        new = str(args.get("new_text") or "")
        if not slug or old == "":
            return "需要 slug 与 old_text"
        page = db.execute(select(WikiPage).where(WikiPage.kb_id == kb_id,
                                                 WikiPage.slug == slug)).scalars().first()
        if not page:
            return f"页面不存在: {slug}"
        cnt = page.content.count(old)
        if cnt == 0:
            return f"页面 {slug} 中未找到目标文本"
        page.content = page.content.replace(old, new)
        db.add(page)
        db.commit()
        return f"已替换 {cnt} 处"

    executors["wiki_replace_text"] = _replace_text

    def _rename_page(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        new_title = str(args.get("new_title") or "").strip()
        if not slug or not new_title:
            return "需要 slug 与 new_title"
        page = db.execute(select(WikiPage).where(WikiPage.kb_id == kb_id,
                                                 WikiPage.slug == slug)).scalars().first()
        if not page:
            return f"页面不存在: {slug}"
        page.title = new_title
        db.add(page)
        db.commit()
        return f"已重命名 {slug} → {new_title}（slug 不变）"

    executors["wiki_rename_page"] = _rename_page

    def _delete_page(args: dict) -> str:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return "缺少 slug"
        page = db.execute(select(WikiPage).where(WikiPage.kb_id == kb_id,
                                                 WikiPage.slug == slug)).scalars().first()
        if not page:
            return f"页面不存在: {slug}"
        page.status = "archived"
        db.add(page)
        db.commit()
        return f"已软删除页面 {slug}"

    executors["wiki_delete_page"] = _delete_page

    # ── 数据分析 ──
    def _find_data_docs(args: dict) -> list:
        stmt = select(KbDocument).where(
            KbDocument.kb_id == kb_id, KbDocument.source_type == "file",
            KbDocument.file_ext.in_(sorted(DATA_EXTS)),
        )
        doc_id = str(args.get("document_id") or "").strip()
        fname = str(args.get("file_name") or "").strip()
        if doc_id:
            stmt = stmt.where(KbDocument.id == doc_id)
        if fname:
            stmt = stmt.where(KbDocument.file_name.contains(fname))
        return db.execute(stmt.order_by(KbDocument.created_at.desc()).limit(20)).scalars().all()

    def _data_schema(args: dict) -> str:
        docs = _find_data_docs(args)
        if not docs:
            return "知识库中无 CSV/XLSX 表格文件"
        lines = []
        target = None
        doc_id = str(args.get("document_id") or "").strip()
        fname = str(args.get("file_name") or "").strip()
        if doc_id or fname:
            target = docs[0]
        if target:
            try:
                tables = _parse_data_file(target)
            except Exception as e:  # noqa: BLE001
                return f"解析失败: {e}"
            for t in tables:
                lines.append(f"表 {t['table']}：{len(t['columns'])} 列，{len(t['rows'])} 行")
                lines.append("列: " + ", ".join(str(c) for c in t["columns"][:50]))
                if t["rows"]:
                    lines.append("样例: " + " | ".join(str(v)[:40] for v in t["rows"][0][:10]))
            return _txt("\n".join(lines))
        for d in docs[:20]:
            lines.append(f"- {d.file_name}（id={d.id}，rows={d.row_count or '—'}）")
        lines.append("\n指定 document_id 或 file_name 查看具体表结构")
        return _txt("\n".join(lines))

    executors["data_schema"] = _data_schema

    def _data_analysis(args: dict) -> str:
        sql = str(args.get("sql") or "").strip()
        if not sql:
            return "缺少 sql"
        docs = _find_data_docs(args)
        if not docs:
            return "知识库中无 CSV/XLSX 表格文件"
        target = docs[0]
        try:
            tables = _parse_data_file(target)
        except Exception as e:  # noqa: BLE001
            return f"解析失败: {e}"
        try:
            import duckdb
        except ImportError:
            return "duckdb 未安装，数据分析不可用"
        # 对齐 WeKnora：所有 sheet 装载进同一张 data 表，源 sheet 名进 __sheet_name 列
        all_cols: list[str] = []
        for t in tables:
            for c in t["columns"]:
                if str(c) not in all_cols:
                    all_cols.append(str(c))
        conn = duckdb.connect(":memory:")
        try:
            col_defs = []
            for ci, c in enumerate(all_cols):
                samples = []
                for t in tables:
                    idx = t["columns"].index(c) if c in t["columns"] else -1
                    if idx >= 0:
                        samples += [r[idx] if idx < len(r) else "" for r in t["rows"][:200]]
                col_defs.append(f'"{c.replace(chr(34), chr(34) * 2)}" {_infer_col_type(samples)}')
            col_defs.append('"__sheet_name" VARCHAR')
            conn.execute(f'CREATE TABLE IF NOT EXISTS "data" ({", ".join(col_defs)})')
            for t in tables:
                idxs = [t["columns"].index(c) if c in t["columns"] else -1 for c in all_cols]
                ph = ", ".join("?" for _ in all_cols + ["__sheet_name"])
                rows = []
                for r in t["rows"]:
                    vals = [r[i] if (i >= 0 and i < len(r)) else None for i in idxs]
                    vals.append(t["table"])
                    rows.append(tuple(vals))
                if rows:
                    conn.executemany(f'INSERT INTO "data" VALUES ({ph})', rows)
            try:
                df = conn.execute(sql).fetchall()
            except Exception as e:  # noqa: BLE001
                return f"SQL 执行失败: {e}"
            cols = list(conn.description) if conn.description else []
            lines = [" | ".join(c[0] if c else "" for c in cols)]
            for r in df[:100]:
                lines.append(" | ".join(_txt(v, 60) for v in r))
            lines.append(f"（{len(df)} 行结果，显示前 100）")
            return _txt("\n".join(lines))
        finally:
            conn.close()

    executors["data_analysis"] = _data_analysis

    # 组装 tools（含全部 catalog 中装配了的工具）
    tools = []
    for name, fn in executors.items():
        cat = CATALOG_BY_NAME.get(name)
        if not cat:
            continue
        props: dict[str, Any] = {}
        required: list[str] = []
        if name in ("knowledge_search", "kb_search", "grep_chunks"):
            props = {"query": {"type": "string", "description": "检索关键词/问题"},
                     "top_k": {"type": "integer", "description": f"返回条数(1-{top_k})"}}
            required = ["query"]
        elif name == "list_knowledge_chunks":
            props = {"document_id": {"type": "string"}, "offset": {"type": "integer"},
                     "limit": {"type": "integer"}}
        elif name == "get_document_info":
            props = {"document_id": {"type": "string"}}
        elif name == "database_query":
            props = {"sql": {"type": "string", "description": "只读 SELECT（安全表白名单）"},
                     "limit": {"type": "integer"}}
            required = ["sql"]
        elif name == "query_knowledge_graph":
            props = {"query": {"type": "string"}, "limit": {"type": "integer"}}
        elif name == "wiki_search":
            props = {"query": {"type": "string"}, "limit": {"type": "integer"}}
            required = ["query"]
        elif name in ("wiki_read_page", "wiki_read_source_doc", "wiki_delete_page"):
            props = {"slug": {"type": "string"}}
            required = ["slug"]
        elif name == "wiki_flag_issue":
            props = {"slug": {"type": "string"}, "issue_type": {"type": "string"},
                     "description": {"type": "string"}, "detail": {"type": "string"}}
            required = ["slug", "description"]
        elif name == "wiki_read_issue":
            props = {"slug": {"type": "string"}, "status": {"type": "string"}}
        elif name == "wiki_update_issue":
            props = {"issue_id": {"type": "string"}, "status": {"type": "string"}}
            required = ["issue_id", "status"]
        elif name == "wiki_write_page":
            props = {"slug": {"type": "string"}, "title": {"type": "string"},
                     "content": {"type": "string"}, "page_type": {"type": "string"}}
            required = ["slug", "content"]
        elif name == "wiki_replace_text":
            props = {"slug": {"type": "string"}, "old_text": {"type": "string"},
                     "new_text": {"type": "string"}}
            required = ["slug", "old_text", "new_text"]
        elif name == "wiki_rename_page":
            props = {"slug": {"type": "string"}, "new_title": {"type": "string"}}
            required = ["slug", "new_title"]
        elif name in ("data_schema",):
            props = {"document_id": {"type": "string"}, "file_name": {"type": "string"}}
        elif name == "data_analysis":
            props = {"sql": {"type": "string"}, "document_id": {"type": "string"},
                     "file_name": {"type": "string"}}
            required = ["sql"]
        else:  # thinking / todo_write
            props = {"note": {"type": "string", "description": "思考/计划说明（仅记录）"}}
        tools.append(_fn_schema(name, cat["description"], props, required))

    def executor(tool_call: dict) -> str:
        name = str(tool_call.get("name") or "")
        fn = executors.get(name)
        if fn is None:
            return f"未授权的工具调用: {name!r}"
        try:
            args = json.loads(str(tool_call.get("arguments") or "{}"))
        except (TypeError, json.JSONDecodeError):
            return "工具参数解析失败：arguments 不是合法 JSON"
        try:
            return fn(args)
        except Exception as e:  # noqa: BLE001 — 工具失败回传文本，不炸 SSE
            return f"工具 {name} 执行失败: {e}"

    return tools, executor
