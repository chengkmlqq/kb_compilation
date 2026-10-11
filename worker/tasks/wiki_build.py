"""KbSkillWikiBuildTask — 上传文档后的 wiki 构建（worker 内直接执行）。

背景（2026-10-03）：原实现把 wiki 构建交给 agent-gateway 技能任务
（KbAgentGatewayTask → gateway 内 build_wiki.py）。本任务把该能力收进
worker：直接读文档 chunks → 调 LLM（WIKI_LLM_* 配置，默认 Infer AI）
生成摘要页 + 实体页 → 经 wiki_create_page / wiki_update_page 写库，
全程进度写入 modo_job.error_message（任务监控页实时可见 + SSE 日志流）。

任务参数（task_params）：
  {
    "kbId": <kb_id>,
    "documentId": <document_id>,
    "language": "中文"          # 可选
  }
"""
from __future__ import annotations

import json
import logging
import os
import time

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.knowledge import DocChunk
from api.services.kb_admin import wiki_create_page, wiki_update_page

logger = logging.getLogger(__name__)

TASK_CLASS_WIKI_BUILD = "KbSkillWikiBuildTask"

PROMPT = """你是知识库 wiki 构建专家。根据下面的文档内容，生成结构化 wiki 页面数据。
必须返回**纯 JSON**（不要 markdown 代码块），格式：
{{
  "title": "文档标题（中文，去除序号前缀）",
  "summary": "文档摘要（200-400 字，概括主题、适用范围、核心要点）",
  "entities": [
    {{"name": "实体/概念名", "definition": "一句话定义（30-80 字）"}}
  ]
}}
entities 数量 3-8 个，提取文档中最重要的业务概念、制度条款、专有名词。
只输出 JSON，不要任何额外文字。

文档内容：
{content}"""


def _write_progress(job_id: str, message: str) -> None:
    """把进度写入 MinIO logs/<job_id>.log + modo_job.error_message（best-effort）。"""
    from worker.tasks.log_sink import append_job_log

    append_job_log(job_id, message)


def _llm_config() -> tuple[str, str, str]:
    """(base_url, model, api_key)：WIKI_LLM_* env（默认对齐 Hermes 供应商 Infer AI）。"""
    base = (os.getenv("WIKI_LLM_BASE_URL") or "https://inferaiapi.com/v1").strip()
    model = (os.getenv("WIKI_LLM_MODEL") or "deepseek-v4-pro").strip()
    key = (os.getenv("WIKI_LLM_API_KEY") or "").strip()
    return base, model, key


def _llm_chat(messages: list[dict]) -> str:
    """OpenAI 兼容 LLM 调用。"""
    import urllib.request

    base, model, key = _llm_config()
    if not key:
        raise RuntimeError("WIKI_LLM_API_KEY 未配置，无法调用 LLM")
    url = f"{base.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"LLM 响应异常: {json.dumps(data)[:300]}")


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"LLM 未返回 JSON: {text[:200]}")
    return json.loads(text[start : end + 1])


def _load_document_chunks(db, document_id: str) -> list[dict]:
    """读文档的 chunks（content 全文）。"""
    rows = (
        db.execute(
            select(DocChunk.content, DocChunk.seq)
            .where(DocChunk.document_id == document_id)
            .order_by(DocChunk.seq)
        )
        .all()
    )
    return [{"seq": r.seq, "content": r.content} for r in rows]


def _md5_hex(text: str, n: int) -> str:
    import hashlib

    return hashlib.md5(text.encode("utf-8")).hexdigest()[:n]


def _build_prompt(chunks: list[dict], max_chunks: int = 60, chunk_chars: int = 2000) -> str:
    parts = []
    for c in chunks[:max_chunks]:
        text = (c.get("content") or "").strip()
        if len(text) > chunk_chars:
            text = text[:chunk_chars] + "…"
        if text:
            parts.append(f"[块{c.get('seq') or 0}]\n{text}")
    content = "\n\n".join(parts)
    if not content:
        raise RuntimeError("文档 chunks 为空，无法构建 wiki")
    return PROMPT.format(content=content[:30000])


def _upsert_wiki_page(db, kb_id: str, title: str, content: str, slug: str, page_type: str) -> dict:
    """幂等写 wiki 页：slug 冲突则 update（wiki_create_page 抛 ValueError）。"""
    try:
        wiki_create_page(
            db, kb_id,
            {"title": title, "content": content, "slug": slug, "page_type": page_type},
            user_id="system",
        )
        return {"slug": slug, "page_type": page_type, "created": True}
    except ValueError as e:
        if "已存在" in str(e) or "exists" in str(e).lower():
            # 已存在则更新；同时恢复 status=active（软删/归档的页重建时重新激活）
            wiki_update_page(db, kb_id, slug, {"title": title, "content": content, "status": "active"})
            return {"slug": slug, "page_type": page_type, "updated": True}
        raise


def _find_wiki_build_tape(db) -> str:
    """按名找「wiki 构建」编排模板的 tape_id（seed 建的 wiki构建-基础原子版）。

    找不到返回空字符串——调用方据此回落旧黑盒实现（编排未 seed 时的兼容路径）。
    """
    from sqlalchemy import select

    from api.models.orchestration import Tape

    row = db.execute(
        select(Tape.id).where(Tape.tape_name == "wiki构建-基础原子版")
    ).scalars().first()
    return str(row or "")


def _dispatch_wiki_build_orchestration(db, kb_id: str, document_id: str) -> dict | None:
    """投递 wiki 构建编排并等终态（2026-10-12）。

    用编排执行 wiki 构建：建 kb_tape_run + send_task execute_run（orch 队列），
    然后轮询 run 到终态返回。与旧黑盒同语义（返回即已完成），只是执行逻辑
    变成编排图（细粒度基础原子组合）。编排缺失时返回 None 交给回落。
    """
    import time as _time

    import uuid

    from api.models.orchestration import Tape, TapeRun
    from sqlalchemy import select

    tape_id = _find_wiki_build_tape(db)
    if not tape_id:
        return None

    tape = db.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
    inputs = {"kb_id": kb_id, "document_id": document_id}
    run_id = uuid.uuid4().hex[:32]
    db.add(
        TapeRun(
            id=run_id,
            tape_id=tape_id,
            tape_name=tape.tape_name if tape else "wiki构建",
            status="queued",
            inputs=inputs,
            step_results=[],
            bindings=inputs,
        )
    )
    db.commit()

    from worker.celery_app import celery_app

    celery_app.send_task("worker.tasks.orch_runner.execute_run", args=[run_id], queue="orch")

    # 等终态（编排异步执行；LLM 调用可能数分钟，给足超时）
    deadline = _time.monotonic() + 1800
    while _time.monotonic() < deadline:
        _time.sleep(3)
        row = db.execute(
            select(TapeRun).where(TapeRun.id == run_id)
        ).scalars().first()
        if row is None:
            return {"success": False, "error": "编排 run 记录丢失"}
        if row.status in ("success", "failed"):
            return {
                "success": row.status == "success",
                "orchestration_run_id": run_id,
                "status": row.status,
                "error": getattr(row, "error", None),
                "step_results": (row.step_results or [])[-3:],
            }
    return {"success": False, "error": "编排执行超时(1800s)", "orchestration_run_id": run_id}


def _handle_wiki_build(job_id: str, task_params: str | None) -> dict:
    """execute_modo_job 分发的 handler：wiki 构建（优先走编排，回落旧黑盒）。

    2026-10-12：改为「系统提供基础原子能力（kb_read_doc/kb_llm_chat/
    wiki_upsert_page/wiki_rebuild_links），编排自己实现 wiki 构建逻辑」——
    投递「wiki构建-基础原子版」编排模板执行；模板未 seed 时自动回落原黑盒
    实现（下方 _handle_wiki_build_legacy），保证升级过程零中断。
    """
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    kb_id = str(params.get("kbId") or params.get("kb_id") or "").strip()
    document_id = str(params.get("documentId") or params.get("document_id") or "").strip()
    if not kb_id or not document_id:
        return {"success": False, "error": "task_params must include kbId and documentId"}

    # 优先：编排执行（基础原子组合）
    try:
        from api.db import session_scope

        with session_scope() as db:
            result = _dispatch_wiki_build_orchestration(db, kb_id, document_id)
        if result is not None:
            result.setdefault("kb_id", kb_id)
            result.setdefault("document_id", document_id)
            return result
    except Exception as exc:  # noqa: BLE001
        _write_progress(job_id, f"[wiki_build] 编排投递异常，回落旧实现: {exc}")

    # 回落：旧黑盒（编排模板未 seed / 投递异常）
    return _handle_wiki_build_legacy(job_id, task_params)


def _handle_wiki_build_legacy(job_id: str, task_params: str | None) -> dict:
    """旧黑盒实现（编排路径的回落，保留兼容）。"""
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    kb_id = str(params.get("kbId") or params.get("kb_id") or "").strip()
    document_id = str(params.get("documentId") or params.get("document_id") or "").strip()
    if not kb_id or not document_id:
        return {"success": False, "error": "task_params must include kbId and documentId"}

    started = time.monotonic()

    # 1) 读文档 chunks
    db = get_sessionmaker()()
    try:
        chunks = _load_document_chunks(db, document_id)
    finally:
        db.close()
    if not chunks:
        return {"success": False, "error": f"文档无 chunks: {document_id}"}
    _write_progress(job_id, f"[step 1/4] loaded {len(chunks)} chunks, elapsed={int(time.monotonic()-started)}s")

    # 2) LLM 生成摘要 + 实体
    prompt = _build_prompt(chunks)
    _write_progress(job_id, f"[step 2/4] calling LLM with {len(prompt)} chars, elapsed={int(time.monotonic()-started)}s")
    raw = _llm_chat([{"role": "user", "content": prompt}])
    data = _parse_llm_json(raw)
    title = str(data.get("title") or f"文档 {document_id}").strip()
    summary = str(data.get("summary") or "").strip()
    entities = [e for e in (data.get("entities") or []) if str(e.get("name") or "").strip()][:6]
    _write_progress(job_id, f"[step 3/4] LLM done: title={title}, entities={len(entities)}, elapsed={int(time.monotonic()-started)}s")

    # 3) 写 wiki（幂等 upsert）
    db = get_sessionmaker()()
    try:
        pages: list[dict] = []
        pages.append(
            _upsert_wiki_page(db, kb_id, title, summary, f"doc-{_md5_hex(document_id, 10)}", "summary")
        )
        for ent in entities:
            name = str(ent.get("name") or "").strip()
            definition = str(ent.get("definition") or "").strip()
            if not name:
                continue
            content_md = f"# {name}\n\n{definition}"
            pages.append(
                _upsert_wiki_page(db, kb_id, name, content_md, f"entity-{_md5_hex(name, 8)}", "entity")
            )

        # 4) 重建 wiki 互链
        try:
            from api.services.kb_admin import wiki_rebuild_links

            wiki_rebuild_links(db, kb_id)
        except Exception as exc:  # noqa: BLE001 — 互链重建失败不阻塞主流程
            logger.warning("wiki rebuild-links skipped: %s", exc)
    finally:
        db.close()

    _write_progress(job_id, f"[step 4/4] wiki pages written: {len(pages)}, elapsed={int(time.monotonic()-started)}s")
    return {
        "success": True,
        "job_id": job_id,
        "kb_id": kb_id,
        "document_id": document_id,
        "title": title,
        "pages": pages,
    }


def register_task_handlers() -> None:
    from worker.tasks.scheduler import TASK_CLASS_REGISTRY

    TASK_CLASS_REGISTRY[TASK_CLASS_WIKI_BUILD] = _handle_wiki_build


register_task_handlers()