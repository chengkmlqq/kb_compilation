#!/usr/bin/env python3
"""清空指定维度的旧评估记录并按知识库重新触发整库评估（2026-10-23 通用版）。

用途（api-server 容器内执行）：
  1) 收集当前指定维度（dim）的评估任务（含其 kb_quality_report）并删除
  2) 对目标知识库按 scope=all 重新触发对应编排（execute_tape env=prod，与页面
     发起等价），串行执行——编排防重复发起，必须等上一个 run 到终态

dim → 编排名：
  doc   → 文档转换评估（纯程序，秒级）
  chunk → 切片评估（LLM 逐文件，大库数小时）
  graph → 图数据评估（Neo4j 查询，秒级）
  wiki  → 质量评估-QA（dim=all 三维度合一，含 wiki 文件粒度落库）

用法（api-server 容器内）：
  docker cp scripts/retrigger_quality_eval.py kb-api-server:/tmp/
  docker exec kb-api-server nohup /srv/kb/.venv/bin/python /tmp/retrigger_quality_eval.py \
      --dim chunk --include-empty > /tmp/retrigger_chunk.log 2>&1 &
  # 进度：docker exec kb-api-server tail -f /tmp/retrigger_chunk.log
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid

sys.path.insert(0, "/srv/kb")

from sqlalchemy import delete, select, text  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.knowledge import QualityReport, QualityRun  # noqa: E402
from api.services.orchestration import execute_tape  # noqa: E402

DIM_TAPES = {
    "doc": "文档转换评估",
    "chunk": "切片评估",
    "graph": "图数据评估",
    "wiki": "质量评估-QA",  # dim=all 三维度合一（wiki 维度文件粒度在落库节点写明细表）
}
POLL_INTERVAL_S = 15


def tape_id_by_name(db, name: str) -> str:
    row = db.execute(select(text("id")).select_from(text("kb_tape")).where(text("tape_name=:n")), {"n": name}).first()
    if row is None:
        raise SystemExit(f"编排不存在: {name}")
    return str(row[0])


def wait_run_terminal(db, run_id: str, timeout_s: int) -> str:
    """轮询 kb_tape_run 到成功/失败（超时返回 timeout）。

    注意：同一会话连续 SELECT 在 MySQL REPEATABLE READ 下共享同一快照，
    轮询前必须 rollback 结束旧事务，否则永远读不到更新后的状态。
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        db.rollback()  # 结束旧快照，读到最新状态
        row = db.execute(
            text("SELECT status, COALESCE(error,'') FROM kb_tape_run WHERE id=:rid"),
            {"rid": run_id},
        ).first()
        if row is not None and row[0] in ("success", "failed"):
            return str(row[0])
        time.sleep(POLL_INTERVAL_S)
    return "timeout"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dim", default="doc", choices=list(DIM_TAPES.keys()),
                        help="评估维度: doc/chunk/graph/wiki")
    parser.add_argument("--kbs", default="", help="显式指定知识库 id 列表（逗号分隔）")
    parser.add_argument("--include-empty", action="store_true",
                        help="对所有有文档的知识库触发（否则只对曾评估过的库）")
    parser.add_argument("--timeout", type=int, default=6 * 3600, help="单库等待上限秒（切片大库需长）")
    args = parser.parse_args()

    dim = args.dim
    tape_name = DIM_TAPES[dim]
    # wiki 走 dim=all 编排「质量评估-QA」，run 记 dim=all（2026-10-23 修复：
    # 原误写 "doc" 导致 wiki 重触发误删 doc 记录、qrun 记错维度页面不可见）
    run_dim = "all" if dim == "wiki" else dim
    db = get_sessionmaker()()
    try:
        # 1) 确定目标知识库 + 清空其旧评估记录
        if args.kbs:
            kbs = sorted({k.strip() for k in args.kbs.split(",") if k.strip()})
            old_runs = db.execute(
                select(QualityRun).where(
                    QualityRun.dim == run_dim, QualityRun.kb_id.in_(kbs)
                )
            ).scalars().all()
        elif args.include_empty:
            rows = db.execute(text("SELECT DISTINCT kb_id FROM kb_document")).all()
            kbs = sorted({str(r[0]) for r in rows})
            old_runs = db.execute(
                select(QualityRun).where(
                    QualityRun.dim == run_dim, QualityRun.kb_id.in_(kbs)
                )
            ).scalars().all()
        else:
            old_runs = db.execute(
                select(QualityRun).where(QualityRun.dim == run_dim)
            ).scalars().all()
            kbs = sorted({r.kb_id for r in old_runs})
        if not kbs:
            print(f"[retrigger] 目标知识库为空，无需触发 (dim={dim})")
            return
        print(f"[retrigger] dim={dim} 目标知识库 {len(kbs)} 个；待清空 {run_dim} 评估任务 {len(old_runs)} 条")
        if old_runs:
            old_ids = [r.id for r in old_runs]
            db.execute(delete(QualityReport).where(QualityReport.run_id.in_(old_ids)))
            db.execute(delete(QualityRun).where(QualityRun.dim == run_dim, QualityRun.kb_id.in_(kbs)))
            db.commit()
            print(f"[retrigger] 已删除 kb_quality_run(dim={run_dim}) {len(old_ids)} 条 + 对应 report")

        # 2) 逐库重触发整库评估（串行，等终态）
        tape_id = tape_id_by_name(db, tape_name)
        print(f"[retrigger] 编排「{tape_name}」id={tape_id}，逐库发起 scope=all ...")
        for kb_id in kbs:
            qrun = QualityRun(
                id=uuid.uuid4().hex[:32], kb_id=kb_id, scope="all", doc_ids=None,
                status="queued", dim=run_dim, created_by="system",
            )
            db.add(qrun)
            db.commit()
            inputs = {
                "kb_id": kb_id,
                "doc_ids": "",
                "scope": "all",
                "quality_id": qrun.id,
                "dim": dim,
            }
            try:
                r = execute_tape(db, tape_id, inputs, env="prod")
                run_id = str(r.get("run_id") or "")
                print(f"[retrigger] {kb_id} 已发起 run_id={run_id} quality_id={qrun.id}，等待终态...")
                db.commit()
            except Exception as exc:  # noqa: BLE001 — 编排已有一个在跑/触发失败，标记 failed
                print(f"[retrigger] {kb_id} 发起失败: {exc}")
                qrun.status = "failed"
                qrun.error = str(exc)[:500]
                db.commit()
                continue
            status = wait_run_terminal(db, run_id, args.timeout)
            print(f"[retrigger] {kb_id} 评估结束: {status} (run_id={run_id})")
        print("[retrigger] 全部完成")
    finally:
        db.close()


if __name__ == "__main__":
    main()