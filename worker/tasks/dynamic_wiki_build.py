#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态 wiki 构建触发链路（方案 B）——worker 侧。

文档解析 READY 后：读文档（大文档用摘要）→ worker 进程内 LLM 判定
知识点树 → 动态生成「文档专属」编排（scripts/gen_dynamic_wiki_tape.py
的 payload）→ create_tape + save_tape_design → 建 kb_tape_run(prod)
+ Job + 投 orch 执行。

知识库无需配置 build_tape_id；KB/文档级显式绑定的编排仍优先
（见 doc_process._trigger_post_ready 分支）。

开关：modo_dim(SYSTEM_CONFIG).DYNAMIC_WIKI_BUILD，值非空 = 启用
（空 = 回落题材分析链路，兼容既有部署）。也可经参数 kp_source 直接
注入判定文本（摘要驱动场景）。

判定 LLM：知识库绑定模型（summary_model_id），经 weknora_rpc 直连；
大文档（切片全文超 DYNAMIC_MAX_FULLTEXT，默认 14000 字）自动回落
摘要（优先摘要 wiki 页，其次 kb_document.summary 字段；都没有则
全文截断）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid

logger = logging.getLogger("kb.dynamic_wiki")

# 开关在哪一组维度参数里（与题材分析 DOMAIN_RESOLVE_TAPE 同款机制）
SYSTEM_CONFIG_GROUP = "SYSTEM_CONFIG"
DYNAMIC_WIKI_BUILD_DIM_KEY = "DYNAMIC_WIKI_BUILD"
# 全文超该阈值 → 用摘要做知识点判定
DYNAMIC_MAX_FULLTEXT = 14000
# 摘要等待上限：Map-Reduce 大文档摘要可能数分钟
SUMMARY_WAIT_TIMEOUT_S = 1800
RUN_POLL_INTERVAL_S = 2.0

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "builtin_engine", "scripts")


# ─────────────────────────────────────────────────────────────────────
# 开关
# ─────────────────────────────────────────────────────────────────────
def dynamic_wiki_build_enabled() -> bool:
    """modo_dim(SYSTEM_CONFIG).DYNAMIC_WIKI_BUILD 非空 = 启用。"""
    from api.db import get_sessionmaker as _gsm

    db = _gsm()()
    try:
        from api.models.framework import Dim

        row = db.execute(
            Dim.__table__.select().where(
                Dim.dim_group == SYSTEM_CONFIG_GROUP,
                Dim.dim_code == DYNAMIC_WIKI_BUILD_DIM_KEY,
            )
        ).first()
        return bool(row and (row.dim_value or "").strip())
    except Exception:  # noqa: BLE001 — 参数表/项缺失 = 未启用
        logger.warning("failed to read %s", DYNAMIC_WIKI_BUILD_DIM_KEY, exc_info=True)
        return False
    finally:
        db.close()


def set_dynamic_wiki_build_enabled(enabled: bool = True) -> None:
    """写入 SYSTEM_CONFIG 开关（幂等 upsert）。"""
    from api.db import get_sessionmaker as _gsm

    db = _gsm()()
    try:
        from api.models.framework import Dim
        from sqlalchemy import select

        row = db.execute(
            select(Dim).where(
                Dim.dim_group == SYSTEM_CONFIG_GROUP,
                Dim.dim_code == DYNAMIC_WIKI_BUILD_DIM_KEY,
            )
        ).scalars().first()
        if row:
            row.dim_value = "1" if enabled else ""
            db.add(row)
        else:
            db.add(Dim(dim_group=SYSTEM_CONFIG_GROUP,
                       dim_code=DYNAMIC_WIKI_BUILD_DIM_KEY,
                       dim_name="动态wiki构建(方案B)",
                       dim_value="1" if enabled else ""))
        db.commit()
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# 读取文档判定源（全文 / 摘要）
# ─────────────────────────────────────────────────────────────────────
def read_doc_source(kb_id: str, document_id: str,
                    max_fulltext: int = DYNAMIC_MAX_FULLTEXT) -> dict:
    """读文档切片全文；超阈值时优先摘要（摘要页 → summary 字段）。

    返回 {"text": str, "source": "fulltext"|"summary"|"truncated", "chunk_count": int}。
    """
    from api.db import get_sessionmaker as _gsm
    from api.models.knowledge import DocChunk, KbDocument
    from sqlalchemy import select

    rows = []
    db = _gsm()()
    try:
        rows = db.execute(
            select(DocChunk.content).where(
                DocChunk.kb_id == kb_id,
                DocChunk.document_id == document_id,
                DocChunk.enabled == 1,
            ).order_by(DocChunk.seq)
        ).scalars().all()
        chunks: list[str] = [str(r or "") for r in rows]
        doc = db.execute(
            select(KbDocument).where(KbDocument.id == document_id)
        ).scalars().first()
        doc_summary = (doc.summary or "") if doc else ""
        doc_name = (doc.file_name or "") if doc else ""
    finally:
        db.close()

    full_text = "\n\n".join(c.strip() for c in chunks if c and c.strip()).strip()
    if len(full_text) <= max_fulltext:
        return {"text": full_text, "source": "fulltext", "chunk_count": len(chunks),
                "doc_name": doc_name}
    # 大文档：优先摘要页，其次 summary 字段，最后全文截断
    summary = doc_summary
    if not summary:
        try:
            import hashlib

            sys.path.insert(0, SCRIPT_DIR)
            import weknora_rpc as wr

            wr.load_config()
            wr.KB = kb_id
            r = wr.read_page(f"summary/{hashlib.md5(document_id.encode()).hexdigest()}",
                             kb_id=kb_id)
            if "error" not in r and (r.get("content") or "").strip():
                summary = r.get("content")
        except Exception:  # noqa: BLE001
            summary = ""
    if summary:
        return {"text": summary, "source": "summary",
                "chunk_count": len(chunks), "doc_name": doc_name}
    return {"text": (full_text[:10000] + "\n\n……（中段省略）……\n\n" + full_text[-4000:]),
            "source": "truncated", "chunk_count": len(chunks), "doc_name": doc_name}


# ─────────────────────────────────────────────────────────────────────
# LLM 知识点判定（worker 进程内直连）
# ─────────────────────────────────────────────────────────────────────
KP_PROMPT = """你是知识库构建专家。请阅读下面给定的文档内容（可能来自全文或摘要），
识别其中包含的知识点，输出一个知识点清单。

【判定要求】
1. 知识点 = 文档中相对独立、值得单独建 wiki 页的知识单元（指标、概念、对象、流程、规则、口径等）。
2. 每个知识点给出 name（简短中文名，用作 wiki 页标题）与 path（多级目录路径，用 / 分隔，
   如「收入指标/出帐金额」；层级由你根据知识点从属关系判定，允许 1~3 级）。
3. extract_hint 给后续抽取提供聚焦线索（该知识点对应原文的典型关键词/句式，30 字内）。
4. 数量控制在 3~15 个；宁少勿滥，只收录文档确有内容的。
5. 若文档过短/无实质内容，返回空数组。

【文档内容】
{document_text}

【输出要求——严格 JSON，禁止任何其他文字】
{{"knowledge_points": [{{"name": "知识点名", "path": "一级/二级", "extract_hint": "聚焦线索"}}]}}
"""


def detect_knowledge_points(kb_id: str, document_text: str,
                            doc_name: str = "", timeout_s: int = 600) -> list:
    """LLM 判定知识点树。失败抛 RuntimeError（调用方决定是否阻断）。"""
    sys.path.insert(0, SCRIPT_DIR)
    import weknora_rpc as wr

    wr.load_config()
    wr.KB = kb_id
    prompt = KP_PROMPT.format(document_text=(document_text or "")[:12000])
    raw = wr.llm_call([{"role": "user", "content": prompt}],
                      max_tokens=3000, temperature=0.2, timeout=timeout_s)
    text = (raw or "").strip()
    text = wr.ensure_full_json(text) if hasattr(wr, "ensure_full_json") else text
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        kps = parsed.get("knowledge_points") or []
    elif isinstance(parsed, list):
        kps = parsed
    else:
        kps = []
    cleaned = []
    for k in kps:
        if not isinstance(k, dict):
            continue
        name = str(k.get("name") or "").strip()
        if not name:
            continue
        cleaned.append({
            "name": name,
            "path": str(k.get("path") or name).strip("/"),
            "extract_hint": str(k.get("extract_hint") or "").strip(),
        })
    return cleaned


# ─────────────────────────────────────────────────────────────────────
# 动态 tape 创建 + run 投递
# ─────────────────────────────────────────────────────────────────────
def build_tape_name(document_id: str, doc_name: str = "") -> str:
    """tape_name 带文档标识，便于编排管理页识别；64 字符内。"""
    suffix = re.sub(r"[^\w\u4e00-\u9fff-]", "-", (doc_name or "")).strip("-")[:20]
    return f"wiki构建-动态-{suffix or document_id[:8]}"


def doc_root_name(doc_name: str = "") -> str:
    """清洗文档名 → wiki 根目录名（去扩展名/非法路径字符，防目录串级）。

    2026-10-28 文档根目录：每个文档构建时建「以文件名命名的根目录」，
    知识点多级目录统一挂在该根下，多文档不再在 KB 根下混叠。
    - 去扩展名：多模态数据管理平台方案V111452-d.docx → 多模态数据管理平台方案V111452-d
    - 替换 / \\ : * ? " < > | 与空白 → 连字符，防目录路径串级/注入
    - 空名/纯标点名兜底「未命名文档」
    """
    name = str(doc_name or "").strip()
    if not name:
        return "未命名文档"
    # 去扩展名（最后一个 . 之后无 / 的部分视为扩展名）
    base = name
    stem = base.rsplit(".", 1)
    if len(stem) == 2 and stem[1] and "/" not in stem[1] and "\\" not in stem[1] and len(stem[1]) <= 12:
        base = stem[0]
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "-", base).strip("-")[:80]
    return cleaned or "未命名文档"


def _create_dynamic_tape(db, payload: dict, user_id: str = "system") -> str:
    """create_tape + save_tape_design（幂等：同名 tape 先更新）。

    返回 tape_id。注意：save_tape_design 会覆盖式同步 kb_tape_step，
    因此同一文档重复上传复用的都是同一份动态编排（topology 不变，
    知识点数据随 run inputs 变）。
    """
    from api.models.orchestration import Tape
    from api.services.orchestration import (
        create_tape,
        save_tape_design,
    )
    from sqlalchemy import select

    existing = db.execute(
        select(Tape).where(Tape.tape_name == payload["tape_name"])
    ).scalars().first()
    if existing:
        tape_id = existing.id
        # ensure category/status 与最新 payload 对齐
        from api.services.orchestration import ensure_tape_category

        existing.category = ensure_tape_category(
            db, payload.get("category") or "通用", cat_type="tape", user_id=user_id
        )
        existing.tape_label = payload.get("tape_label") or existing.tape_label
        existing.tape_descr = payload.get("tape_descr") or existing.tape_descr
        existing.tape_type = payload.get("tape_type") or existing.tape_type
        existing.status = "effective"
        db.commit()
        save_tape_design(db, tape_id, payload["nodes"], payload["edges"])
        return tape_id

    tape = create_tape(db, payload, user_id=user_id)
    tape_id = tape.id
    save_tape_design(db, tape_id, payload["nodes"], payload["edges"])
    # 设计保存后置 effective（_create_tape_run 允许 draft/effective，置 effective
    # 便于编排管理页直接看到可执行状态）
    from api.services.orchestration import publish_tape

    try:
        publish_tape(db, tape_id)
    except Exception:  # noqa: BLE001 — 发布失败不阻断（draft 也可执行）
        logger.warning("publish dynamic tape %s failed", tape_id, exc_info=True)
    return tape_id


def enqueue_dynamic_wiki_build(
    kb_id: str,
    document_id: str,
    parent_job_id: str | None = None,
    cookie: str = "",
    kp_source: str | None = None,  # 外部已提供判定文本（如摘要 run 产物）时注入
    summary_run_id: str | None = None,  # 摘要 run 未完成时先等待
    max_fulltext: int = DYNAMIC_MAX_FULLTEXT,
) -> dict:
    """方案 B 完整链路：读文档 → 判知识点 → 动态建 tape → 执行。

    返回诊断字段（dynamic_generated / tape_id / run_id / kp_count /
    kp_source / error 等），不抛异常（失败记 error 返回，由调用方决定）。
    """
    out: dict = {"dynamic_generated": False}

    # 0) 开关
    if not dynamic_wiki_build_enabled():
        out["dynamic_skipped"] = f"{DYNAMIC_WIKI_BUILD_DIM_KEY} 未启用"
        return out

    # 1) 等摘要（可选）：大文档判定源优先摘要
    if summary_run_id:
        from api.db import get_sessionmaker as _gsm

        st = _wait_run_terminal(summary_run_id, SUMMARY_WAIT_TIMEOUT_S)
        out["summary_run_status"] = st

    # 2) 读取判定源 & 业务字段
    from api.db import get_sessionmaker as _gsm
    from api.models.knowledge import KbDocument
    from sqlalchemy import select

    db = _gsm()()
    try:
        doc = db.execute(
            select(KbDocument).where(KbDocument.id == document_id)
        ).scalars().first()
        doc_name = (doc.file_name or "") if doc else ""
    finally:
        db.close()

    try:
        rd: dict | None = None
        if kp_source:
            source_text, src_kind = kp_source, "external"
        else:
            rd = read_doc_source(kb_id, document_id, max_fulltext=max_fulltext)
            source_text, src_kind = rd["text"], rd["source"]
        out["kp_source"] = src_kind
        out["chunk_count"] = (rd or {}).get("chunk_count", 0) if rd else None
        if not source_text.strip():
            out["dynamic_skipped"] = "文档无内容可判定"
            return out

        # 3) LLM 判定知识点
        try:
            kps = detect_knowledge_points(kb_id, source_text, doc_name=doc_name)
        except Exception as exc:  # noqa: BLE001 — LLM 失败可重试/回落题材分析
            out["error"] = f"知识点判定失败: {exc}"
            return out
        if not kps:
            out["kp_count"] = 0
            out["dynamic_skipped"] = "知识点判定为空"
            return out
        out["kp_count"] = len(kps)

        # 4) 动态生成编排 & 建 tape
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../..")
        from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload

        tape_name = build_tape_name(document_id, doc_name)
        payload = build_dynamic_tape_payload(
            tape_name=tape_name,
            tape_descr=f"动态 wiki 构建（方案B）：{doc_name or document_id} 文档专属编排，"
                       f"{len(kps)} 个知识点按目录构建。",
        )
        db = _gsm()()
        try:
            tape_id = _create_dynamic_tape(db, payload)
        finally:
            db.close()
        out["tape_id"] = tape_id
        out["tape_name"] = tape_name

        # 5) 建 run + Job + 投 orch（对齐 _enqueue_build_engine_task 骨架）
        from api.services.orchestration import _create_tape_run

        db = _gsm()()
        try:
            created = _create_tape_run(
                            db, tape_id,
                            inputs={
                                "kb_id": kb_id,
                                "document_id": document_id,
                                "doc_id": document_id,
                                "kid": document_id,
                                "doc_name": doc_name,
                                # 2026-10-28 文档根目录：预置清洗后的文档名，编排 n02 的
                                # doc_root 消费；知识点目录统一挂在「文档名/」根下
                                "doc_root": doc_root_name(doc_name),
                                "source_file": doc_name,
                                "x_next_identity": cookie,
                                "kp_tree": {"knowledge_points": kps},
                                "limit": "",
                                "only_codes": "",
                                "build_summary": "",
                                "language": "中文",
                            },
                            env="prod",
                        )
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"动态编排 run 创建失败: {exc}"
            return out
        finally:
            db.close()
        run_id, run_env = created["run_id"], created["run_env"]
        out["run_id"] = run_id
        out["dynamic_generated"] = True

        # Job 行（任务监控可见）+ 投 orch —— 顺序契约：先 commit Job 再投 broker
        job_id = f"ORCH_RUN_{uuid.uuid4().hex[:12]}"
        task_params = json.dumps({"runId": run_id, "env": run_env}, ensure_ascii=False)
        db2 = _gsm()()
        try:
            from api.models.framework import Job

            db2.add(
                Job(
                    id=job_id,
                    task_id=job_id,
                    task_class="KbOrchestrationRunTask",
                    queue_name="orch",
                    task_params=task_params,
                    trigger_type="API",
                    state="PENDING",
                    parent_id=parent_job_id,
                )
            )
            db2.commit()
        except Exception:  # noqa: BLE001
            db2.rollback()
            out["error"] = "动态编排 Job 写入失败"
            return out
        finally:
            db2.close()
        try:
            from worker.celery_app import celery_app

            celery_app.send_task(
                "worker.tasks.scheduler.execute_modo_job",
                args=[job_id],
                task_id=job_id,
                queue="orch",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to enqueue dynamic build run %s: %s", run_id, exc)
            out["error"] = f"动态编排投递失败: {exc}"
            return out
        out["job_id"] = job_id
        return out
    except Exception as exc:  # noqa: BLE001 — 兜底不使解析任务失败
        out["error"] = f"动态 wiki 构建失败: {exc}"
        logger.warning("dynamic wiki build failed (kb=%s doc=%s): %s",
                       kb_id, document_id, exc, exc_info=True)
        return out


def _wait_run_terminal(run_id: str, timeout_s: int, poll_s: float = RUN_POLL_INTERVAL_S) -> str | None:
    """同步轮询 kb_tape_run 到终态；返回 success/failed/cancelled，超时 None。"""
    from api.db import get_sessionmaker as _gsm
    from api.models.orchestration import TapeRun

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        db = _gsm()()
        try:
            st = db.execute(
                TapeRun.__table__.select().where(TapeRun.id == run_id)
            ).first()
            st = st.status if st else None
        finally:
            db.close()
        if st in ("success", "failed", "cancelled"):
            return str(st)
        time.sleep(poll_s)
    return None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="动态 wiki 构建（方案B）触发链路")
    ap.add_argument("--kb", required=True, help="知识库 ID")
    ap.add_argument("--doc", required=True, help="文档 ID")
    ap.add_argument("--enable", action="store_true", help="启用开关")
    ap.add_argument("--disable", action="store_true", help="关闭开关")
    args = ap.parse_args()
    if args.enable or args.disable:
        set_dynamic_wiki_build_enabled(args.enable)
        print(f"DYNAMIC_WIKI_BUILD 已{'启用' if args.enable else '关闭'}")
    else:
        r = enqueue_dynamic_wiki_build(args.kb, args.doc)
        print(json.dumps(r, ensure_ascii=False, indent=2))