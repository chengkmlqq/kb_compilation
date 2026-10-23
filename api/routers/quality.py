"""质量评估 API（质量评估页面 v1，2026-10-14；单维度评估拆分 2026-10-21）。

页面能力：
- 发起评估：单文档 / 批量文档（调「质量评估-QA」编排 execute，异步）；
  单维度评估（文档转换/切片/图数据）按 dim 走独立编排
- 任务列表：kb_quality_run 分页（状态/得分/问题数），支持 dim 过滤
- 任务详情：kb_quality_run + kb_quality_report 明细（指标/问题/归因）

鉴权：_require_caller（登录态；管理页操作由编排执行链路负责）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.knowledge import QualityReport, QualityRun
from api.services.identity import decode_identity_cookie
from api.services.orchestration import execute_tape
from api.services.scope import is_admin

logger = logging.getLogger("kb.quality")

router = APIRouter(prefix="/quality", tags=["quality"])

# 编排名（seed_framework_db.py / seed_tape_data/ 固化）
QUALITY_TAPE_NAME = "质量评估-QA"
# dim → 单维度编排名（2026-10-21 拆分）
DIM_TAPE_NAMES = {
    "doc": "文档转换评估",
    "chunk": "切片评估",
    "graph": "图数据评估",
}
DIMS = ("all", "doc", "chunk", "graph")


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return {"user_id": identity.user_id, "team_name": identity.team_name or "",
            "is_admin": is_admin(db, identity.user_id)}


class QualityLaunchRequest(BaseModel):
    kb_id: str
    doc_ids: list[str] = []
    scope: str = "all"  # single|multi|all
    dim: str = "all"  # all|doc|chunk|graph（2026-10-21 单维度评估）


def _tape_id_by_name(db: Session, name: str) -> str:
    from api.models.orchestration import Tape

    t = db.execute(select(Tape).where(Tape.tape_name == name)).scalars().first()
    if not t:
        raise HTTPException(status_code=404, detail=f"编排不存在: {name}（先跑 seed 建种子编排）")
    return t.id


@router.post("/runs")
def launch_quality(req: QualityLaunchRequest, db: Session = Depends(get_db),
                   caller: dict = Depends(_require_caller)) -> dict:
    """发起质量评估（异步）：调对应编排 execute。

    dim=all 走「质量评估-QA」（三维度合一，默认，兼容旧调用方）；
    dim=doc|chunk|graph 走单维度编排（文档转换评估/切片评估/图数据评估），
    任务行记 dim，页面按 dim 过滤渲染。

    scope 自动推导：doc_ids 1 个 = single，>1 = multi，空 = all（整库）。
    """
    dim = (req.dim or "all").strip().lower()
    if dim not in DIMS:
        raise HTTPException(status_code=400, detail=f"不支持的评估维度: {dim}（可选 {list(DIMS)}）")
    scope = req.scope
    if not scope or scope not in ("single", "multi", "all"):
        scope = "all" if not req.doc_ids else ("single" if len(req.doc_ids) == 1 else "multi")
    # 先建 queued 占位行（编排汇总节点会 UPDATE 它，避免「占位行+自建行」重复记录）
    from uuid import uuid4

    qrun = QualityRun(id=uuid4().hex[:32], kb_id=req.kb_id, scope=scope,
                      doc_ids=req.doc_ids or None, status="queued", dim=dim,
                      created_by=caller.get("user_id") or "")
    db.add(qrun)
    db.commit()
    inputs = {
        "kb_id": req.kb_id,
        "doc_ids": ",".join(req.doc_ids),
        "scope": scope,
        "quality_id": qrun.id,
        "dim": dim,
    }
    try:
        tape_name = DIM_TAPE_NAMES.get(dim, QUALITY_TAPE_NAME)
        tape_id = _tape_id_by_name(db, tape_name)
        # 2026-10-22 fix：质量评估必须跑 prod 环境。占位行（上方 db.add 在请求默认
        # prod 上下文）与评估脚本读写（kb_document/doc_chunk/wiki_page + 回写
        # kb_quality_run/kb_quality_report）都在正式表，UI 也读正式表；若按手动执行
        # 默认 env=None→dev，脚本 get_sessionmaker() 被 env 路由到 _dev 表——
        # 读的是旧快照库（未同步新库）、结果落 _dev 表占位行永远 queued，评估不生效。
        # 与上传链一致：数据在 prod，编排节点须同环境读写。
        r = execute_tape(db, tape_id, inputs, env="prod")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    run_id = r.get("run_id") or ""
    return {"success": True, "data": {"run_id": run_id, "quality_id": qrun.id,
                                      "scope": scope, "status": "queued", "dim": dim}}


@router.get("/runs")
def list_quality_runs(
    kb_id: str = Query("", max_length=64),
    status: str = Query("", max_length=20),
    dim: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """质量评估任务列表（kb_quality_run 分页）。

    dim 可选：'' = 全部（含 all 三维度合一与单维度任务）；'all'/'doc'/'chunk'/'graph' 精确过滤。
    """
    from sqlalchemy import func

    # 2026-10-16 fix(1038 Out of sort memory)：kb_quality_run.issues 是聚合后的
    # 问题清单 JSON，实测整库评估单行可达 ~600KB——全列 SELECT + ORDER BY 时
    # 优化器按成本模型选 filesort，宽行塞 sort_buffer（默认 256KB）即溢出 →
    # 列表端点 500（与任务监控 parents 9c5d15b / 编排 runs dc63463 同源）。
    # 排序只取窄列 id（filesort 只碰 id 行，永不爆 buffer），再按 id 回表取
    # 全行并按位置还原顺序（不依赖优化器是否选索引）。
    conds = []
    if kb_id:
        conds.append(QualityRun.kb_id == kb_id)
    if status:
        conds.append(QualityRun.status == status)
    if dim:
        conds.append(QualityRun.dim == dim)

    total_stmt = select(func.count()).select_from(QualityRun)
    id_stmt = select(QualityRun.id)
    if conds:
        total_stmt = total_stmt.where(*conds)
        id_stmt = id_stmt.where(*conds)
    total = db.execute(total_stmt).scalar() or 0
    run_ids: list[str] = list(
        db.execute(
            id_stmt.order_by(QualityRun.created_at.desc(), QualityRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    )
    items: list[dict] = []
    if run_ids:
        pos = {rid: i for i, rid in enumerate(run_ids)}
        # 2026-10-23 性能审计：列表只取窄列——issues/attribution 是聚合后的大
        # JSON（整库评估单行可达 ~600KB），列表仅展示 issues_count；metrics 四列
        # 保留（列表需各维度得分）。issues_count 在 MySQL 用 JSON_LENGTH 库内数，
        # 其余方言（sqlite 测试）回落加载 issues 列计数。
        cols = [
            QualityRun.id,
            QualityRun.kb_id,
            QualityRun.scope,
            QualityRun.doc_ids,
            QualityRun.status,
            QualityRun.dim,
            QualityRun.doc_metrics,
            QualityRun.chunk_metrics,
            QualityRun.wiki_metrics,
            QualityRun.graph_metrics,
            QualityRun.summary,
            QualityRun.error,
            QualityRun.created_by,
            QualityRun.created_at,
        ]
        rows = db.execute(select(*cols).where(QualityRun.id.in_(run_ids))).all()
        issue_counts: dict[str, int] = {}
        if db.get_bind().dialect.name == "mysql":
            cnt = db.execute(
                text("SELECT id, JSON_LENGTH(issues) FROM kb_quality_run WHERE id IN :ids"),
                {"ids": tuple(run_ids)},
            ).all()
            issue_counts = {str(i): int(c or 0) for i, c in cnt}
        else:
            for r in db.execute(
                select(QualityRun.id, QualityRun.issues).where(QualityRun.id.in_(run_ids))
            ).all():
                issue_counts[str(r[0])] = len(r[1] or [])
        for row in sorted(rows, key=lambda x: pos.get(x.id, len(run_ids))):
            items.append(_run_item_row(row, issue_counts.get(row.id, 0)))
    return {"success": True, "data": {"total": total, "page": page, "page_size": page_size,
                                      "items": items}}


@router.get("/docs")
def list_quality_file_details(
    kb_id: str = Query("", max_length=64),
    status: str = Query("", max_length=20),
    keyword: str = Query("", max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    dim: str = Query("doc", max_length=20),
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """质量评估-单文件粒度明细（每文件一行，最新状态）。

    dim=doc|chunk|graph|wiki 对应 kb_quality_doc_eval / kb_quality_chunk_eval /
    kb_quality_graph_eval / kb_quality_wiki_eval。各维度评估每次落库按 kb 整批
    覆盖写入，本接口返回该知识库当前各文件的评估结果（文件粒度）。
    支持按文件名关键字过滤；分页返回。
    """
    from sqlalchemy import func
    from typing import cast

    dim = (dim or "doc").strip().lower()
    if dim not in ("doc", "chunk", "graph", "wiki"):
        raise HTTPException(status_code=400, detail=f"不支持的评估维度: {dim}（可选 doc/chunk/graph/wiki）")

    if dim == "doc":
        from api.models.knowledge import QualityDocEval

        model = QualityDocEval
    elif dim == "chunk":
        from api.models.knowledge import QualityChunkEval

        model = QualityChunkEval
    elif dim == "graph":
        from api.models.knowledge import QualityGraphEval

        model = QualityGraphEval
    else:
        from api.models.knowledge import QualityWikiEval

        model = QualityWikiEval

    any_model = cast(object, model)

    conds = []
    if kb_id:
        conds.append(model.kb_id == kb_id)
    if keyword:
        conds.append(model.file_name.like(f"%{keyword}%"))

    total_stmt = select(func.count()).select_from(model)
    id_stmt = select(model.id)
    if conds:
        total_stmt = total_stmt.where(*conds)
        id_stmt = id_stmt.where(*conds)
    total = db.execute(total_stmt).scalar() or 0
    ids: list[str] = list(
        db.execute(
            id_stmt.order_by(model.file_name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    )
    items: list[dict] = []
    if ids:
        pos = {rid: i for i, rid in enumerate(ids)}
        rows = db.execute(select(model).where(model.id.in_(ids))).scalars().all()
        # 知识库 id → 中文名（kb_datasource.name），一次批量查回填
        kb_ids = {r.kb_id for r in rows if r.kb_id}
        kb_names: dict[str, str] = {}
        if kb_ids:
            from api.models.knowledge import KbDatasource

            kb_rows = db.execute(
                select(KbDatasource.id, KbDatasource.name).where(KbDatasource.id.in_(kb_ids))
            ).all()
            kb_names = {kid: name or "" for kid, name in kb_rows}
        for r in sorted(rows, key=lambda r: pos.get(r.id, len(ids))):
            item: dict = {
                "doc_id": r.doc_id,
                "kb_id": r.kb_id,
                "kb_name": kb_names.get(r.kb_id, "") if r.kb_id else "",
                "run_id": r.run_id,
                "file_name": r.file_name,
                "score": r.score,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            if dim == "doc":
                item.update({
                    "file_ext": r.file_ext,
                    "file_size": r.file_size,
                    "parse_state": r.parse_state,
                    "parse_error": r.parse_error,
                    "parse_engine": r.parse_engine,
                    "duration_ms": r.duration_ms,
                    "chunk_count": r.chunk_count,
                    "md_length": r.md_length,
                    "garbled": bool(r.garbled),
                    "garbled_hits": r.garbled_hits or [],
                    "file_name_garbled": bool(r.file_name_garbled),
                    "content_garbled": bool(r.content_garbled),
                })
            else:
                item["issues"] = r.issues or []
                if dim == "chunk":
                    item.update({
                        "chunks": r.chunks,
                        "avg_len": r.avg_len,
                        "wiki_pages": r.wiki_pages,
                        "wiki_start_time": r.wiki_start_time.isoformat() if r.wiki_start_time else None,
                        "wiki_build_sec": r.wiki_build_sec,
                        "doc_process_sec": r.doc_process_sec,
                        "wiki_tape": r.wiki_tape,
                        "eval_duration_sec": r.eval_duration_sec,
                        "conclusion": r.conclusion,
                        "error": r.error,
                    })
                elif dim == "graph":
                    item.update({
                        "nodes": r.nodes,
                        "rels": r.rels,
                        "dumb_nodes": r.dumb_nodes,
                        "untraceable_rels": r.untraceable_rels,
                        "wiki_missing": r.wiki_missing,
                    })
                else:  # wiki
                    item.update({
                        "pages": r.pages,
                        "empty": r.empty,
                        "orphan": r.orphan,
                        "broken_links": r.broken_links,
                        "placeholder": r.placeholder,
                        "missing_source": r.missing_source,
                        "no_folder": r.no_folder,
                    })
            items.append(item)
    return {"success": True, "data": {"total": total, "page": page, "page_size": page_size,
                                      "dim": dim, "items": items}}


@router.get("/runs/{quality_id}")
def quality_run_detail(quality_id: str, db: Session = Depends(get_db),
                       caller: dict = Depends(_require_caller)) -> dict:
    """质量评估任务详情：run 汇总 + 三维度 report 明细。"""
    run = db.get(QualityRun, quality_id)
    if not run:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    # 2026-10-16 fix(1038 Out of sort memory)：kb_quality_report.issues 同为
    # 大 JSON（实测单行 ~580KB），库内 ORDER BY dim 仍要 filesort 宽行 →
    # 详情端点 500。改为取回后在应用层按 dim 排序（每 run 至多 3 行）。
    reports = db.execute(
        select(QualityReport).where(QualityReport.run_id == run.id)
    ).scalars().all()
    dims = {}
    for rp in sorted(reports, key=lambda x: x.dim or ""):
        dims[rp.dim] = {"metrics": rp.metrics or {}, "issues": rp.issues or []}
    data = {**_run_item(run), "reports": dims}
    return {"success": True, "data": data}


@router.delete("/runs/{quality_id}")
def delete_quality_run(
    quality_id: str,
    db: Session = Depends(get_db),
    caller: dict = Depends(_require_caller),
) -> dict:
    """删除质量评估任务（连同三维度报告 kb_quality_report）。

    - 进行中（queued/running）：非管理员拒绝（后台编排仍在写回，删除会产生
      孤儿 report）；管理员可强制删除（用于清理卡死/误发任务）。
    - 只删 kb_quality_run / kb_quality_report，不动编排运行记录（任务监控留档）。
    - 权限：与页面现状一致（列表/发起均不限归属），登录即可删除；如需按
      发起人/知识库权限收紧，另行加 can_manage 校验。
    """
    run = db.get(QualityRun, quality_id)
    if not run:
        raise HTTPException(status_code=404, detail="评估任务不存在")
    if run.status in ("queued", "running") and not caller.get("is_admin"):
        raise HTTPException(status_code=409, detail="评估任务执行中，暂不能删除（管理员可强制删除）")
    db.execute(delete(QualityReport).where(QualityReport.run_id == run.id))
    db.delete(run)
    db.commit()
    return {"success": True, "data": {"id": run.id, "deleted": True}}


def _run_item_row(row, issues_count: int) -> dict:
    """列表用窄列版 run item（2026-10-23）：attribution 不在列表展示，置空。"""
    scores = {}
    for dim, key in (("doc", "doc_metrics"), ("chunk", "chunk_metrics"),
                     ("wiki", "wiki_metrics"), ("graph", "graph_metrics")):
        m = getattr(row, key) or {}
        if not isinstance(m, dict):
            m = {}
        scores[dim] = m.get("final_score") if m.get("final_score") is not None else m.get("health_score")
    return {
        "id": row.id,
        "kb_id": row.kb_id,
        "scope": row.scope,
        "doc_ids": row.doc_ids or [],
        "status": row.status,
        "dim": row.dim or "all",
        "scores": scores,
        "issues_count": issues_count,
        "attribution": [],
        "summary": row.summary or "",
        "error": row.error or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _run_item(r: QualityRun) -> dict:
    scores = {}
    for dim, key in (("doc", "doc_metrics"), ("chunk", "chunk_metrics"),
                     ("wiki", "wiki_metrics"), ("graph", "graph_metrics")):
        m = getattr(r, key) or {}
        if not isinstance(m, dict):
            m = {}
        # 综合分优先（final_score = 程序×0.6+语义×0.4），老数据无则回落程序分
        scores[dim] = m.get("final_score") if m.get("final_score") is not None else m.get("health_score")
    return {
        "id": r.id,
        "kb_id": r.kb_id,
        "scope": r.scope,
        "doc_ids": r.doc_ids or [],
        "status": r.status,
        "dim": r.dim or "all",
        "scores": scores,
        "issues_count": len(r.issues or []),
        "attribution": r.attribution or [],
        "summary": r.summary or "",
        "error": r.error or "",
        "created_by": r.created_by or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
