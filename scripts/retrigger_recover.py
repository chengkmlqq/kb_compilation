#!/usr/bin/env python3
"""宿主机侧自愈恢复驱动（v3）：重建 NG集团规范 切片评估 + 发起 电信语义层（串行），
轮询到全部终态。每步用一次性 docker exec 进 kb-api-server 执行（容器重建也不受影响）。

v3 自愈：并行会话会反复重建 worker 容器（部署），celery 任务随 worker 死亡丢失，run 变
孤儿（status 恒 running、无人执行）。检测：若 status=queued/running 且 build-worker 容器
创建时间晚于 run 创建时间（+60s 容差）→ 判定该 run 已死 → 置 failed 释放防重 guard、
清理其占位行、重新发起同一知识库，直至最终跑完。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

API = "kb-api-server"
WORKER = "kb-celery-build-worker"
VENV = "/srv/kb/.venv/bin/python"
TAPE_ID = "92427d69b323473fb8f639d615cda439"  # 切片评估

KBS = [
    ("eb6ee0edcdc349ef980b642ff317c2de", "NG集团规范"),
    ("fdf7054949894847861316c7cd682bda", "电信语义层"),
]


def py_in(code: str) -> str:
    r = subprocess.run(
        ["docker", "exec", API, VENV, "-c", code],
        capture_output=True, text=True, timeout=180,
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        raise RuntimeError(f"exec rc={r.returncode}: {(r.stderr or '')[-300:]}")
    return out


def _query(sql: str) -> str:
    assert '"' not in sql, "查询内不应含双引号（wrapper 用双引号包裹）"
    code = (
        "import sys, json; sys.path.insert(0,'/srv/kb')\n"
        "from sqlalchemy import text\n"
        "from api.db import get_sessionmaker\n"
        "db=get_sessionmaker()()\n"
        "try:\n"
        f"    rows=db.execute(text(\"{sql}\")).all()\n"
        "    print(json.dumps([[str(c) if c is not None else None for c in r] for r in rows], ensure_ascii=False))\n"
        "finally:\n"
        "    db.close()\n"
    )
    return py_in(code)


def _exec_db(code: str) -> None:
    full = (
        "import sys; sys.path.insert(0,'/srv/kb')\n"
        "from sqlalchemy import text\n"
        "from api.db import get_sessionmaker\n"
        "db=get_sessionmaker()()\n"
        "try:\n" + code +
        "    db.commit()\n"
        "finally:\n"
        "    db.close()\n"
    )
    py_in(full)


def active_run_for(kb_id: str) -> str:
    """返回该 kb 当前 queued/running 的 run_id（无则空串）。"""
    data = _query(
        f"SELECT id, inputs FROM kb_tape_run WHERE tape_id='{TAPE_ID}' "
        f"AND status IN ('queued','running')"
    )
    for row in json.loads(data or "[]"):
        rid, inputs = row[0], row[1] or ""
        try:
            ins = json.loads(inputs)
        except Exception:
            continue
        if str(ins.get("kb_id") or "") == kb_id:
            return rid
    return ""


def last_done_for(kb_id: str) -> str:
    """该 kb 在 v3 时代（2026-10-23 08:00 后）最近一条终态 run 的 status。"""
    data = _query(
        f"SELECT status FROM kb_tape_run WHERE tape_id='{TAPE_ID}' "
        f"AND status IN ('success','failed') "
        f"AND created_at >= '2026-10-23 08:00:00' "
        f"AND inputs LIKE '%{kb_id}%' ORDER BY updated_at DESC LIMIT 1"
    )
    rows = json.loads(data or "[]")
    return rows[0][0] if rows else ""


def launch(kb_id: str, qid: str) -> str:
    code = (
        "import sys; sys.path.insert(0,'/srv/kb')\n"
        "from sqlalchemy import text\n"
        "from api.db import get_sessionmaker\n"
        "from api.services.orchestration import execute_tape\n"
        "db=get_sessionmaker()()\n"
        "try:\n"
        f"    db.execute(text(\"INSERT INTO kb_quality_run (id,kb_id,scope,status,dim,created_by,created_at,updated_at) VALUES ('{qid}','{kb_id}','all','queued','chunk','system',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)\"))\n"
        "    db.commit()\n"
        f"    r=execute_tape(db, '{TAPE_ID}', {{'kb_id': '{kb_id}', 'doc_ids': '', 'scope': 'all', 'quality_id': '{qid}', 'dim': 'chunk'}}, env='prod')\n"
        "    print('RUNID=' + str(r.get('run_id') or ''))\n"
        "finally:\n"
        "    db.close()\n"
    )
    out = py_in(code)
    for line in reversed(out.splitlines()):
        if line.strip().startswith("RUNID="):
            return line.strip()[6:].strip()
    raise RuntimeError(f"launch 未取到 RUNID，stdout={out[-200:]}")


def run_info(run_id: str) -> tuple[str, datetime | None]:
    """返回 (status, created_at)。"""
    data = _query(
        f"SELECT status, created_at FROM kb_tape_run WHERE id='{run_id}'"
    )
    rows = json.loads(data or "[]")
    if not rows:
        return "gone", None
    st, ct = rows[0]
    dt = None
    if ct:
        try:
            dt = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
        except Exception:
            dt = None
    return st, dt


def worker_created_epoch() -> float:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.Created}}", WORKER],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"inspect worker 失败: {(r.stderr or '')[-200:]}")
    try:
        return datetime.fromisoformat(r.stdout.strip()).timestamp()
    except Exception:
        return 0.0


def kill_active_for(kb_id: str) -> None:
    """把该 kb 当前 queued/running 的切片评估 run 全部置 failed（释放 tape 防重 guard），
    并清理该 kb 所有 queued 的 chunk 占位行（含孤儿）。"""
    code = (
        "    rows=db.execute(text(\"SELECT id FROM kb_tape_run WHERE tape_id='" + TAPE_ID + "' AND status IN ('queued','running') AND inputs LIKE '%" + kb_id + "%'\")).all()\n"
        "    for (rid,) in rows:\n"
        f"        db.execute(text(\"UPDATE kb_tape_run SET status='failed', error='worker 容器重建导致评估中断，自愈重发' WHERE id=:i\"), {{'i': rid}})\n"
        "    bad=db.execute(text(\"SELECT id FROM kb_quality_run WHERE kb_id=:kb AND dim='chunk' AND status='queued'\"), {'kb': '" + kb_id + "'}).all()\n"
        "    ids=[r[0] for r in bad]\n"
        "    if ids:\n"
        "        db.execute(text(\"DELETE FROM kb_quality_report WHERE run_id IN :ids\"), {'ids': tuple(ids)})\n"
        "        db.execute(text(\"DELETE FROM kb_quality_run WHERE id IN :ids\"), {'ids': tuple(ids)})\n"
    )
    _exec_db(code)


def launch_with_retry(kb_id: str, qid: str, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            return launch(kb_id, qid)
        except RuntimeError as exc:
            last = exc
            print(f"[recover] launch 失败(第{i+1}次): {exc}，清 guard 后重试", flush=True)
            kill_active_for(kb_id)
            time.sleep(15)
    raise RuntimeError(f"launch 重试耗尽: {last}")


def main() -> None:
    for kb_id, name in KBS:
        # 幂等：复用进行中的 run / 跳过已成功的
        rid = active_run_for(kb_id)
        qid = ""
        if rid:
            print(f"[recover] {name} 已有进行中 run {rid}，复用等待", flush=True)
        else:
            done = last_done_for(kb_id)
            if done == "success":
                print(f"[recover] {name} 已有 v3 success run，跳过", flush=True)
                continue
            qid = uuid.uuid4().hex[:32]
            rid = launch_with_retry(kb_id, qid)
            print(f"[recover] {name} 已发起 run_id={rid} quality_id={qid} {time.strftime('%F %T')}", flush=True)

        deadline = time.monotonic() + 8 * 3600
        last = ""
        while time.monotonic() < deadline:
            time.sleep(60)
            try:
                st, rct = run_info(rid)
            except RuntimeError as exc:
                print(f"[recover] {name} 轮询异常(重试): {exc}", flush=True)
                time.sleep(30)
                continue
            if st != last:
                print(f"[recover] {name} {st} {time.strftime('%H:%M:%S')}", flush=True)
                last = st
            if st in ("success", "failed"):
                print(f"[recover] {name} 终态: {st}", flush=True)
                break
            # 自愈：run 未终态但 worker 容器已在 run 创建之后重建 → 任务已死
            if st in ("queued", "running") and rct is not None:
                try:
                    wc = worker_created_epoch()
                except RuntimeError as exc:
                    print(f"[recover] {name} inspect worker 异常(跳过本轮): {exc}", flush=True)
                    continue
                if wc > rct.timestamp() + 60:
                    print(f"[recover] {name} 检测到 worker 重建(run {rct:%H:%M:%S} < worker {datetime.fromtimestamp(wc, timezone.utc):%H:%M:%S})，判定任务丢失，自愈重发", flush=True)
                    kill_active_for(kb_id)
                    qid = uuid.uuid4().hex[:32]
                    rid = launch_with_retry(kb_id, qid)
                    print(f"[recover] {name} 重发 run_id={rid} quality_id={qid} {time.strftime('%F %T')}", flush=True)
                    last = ""
                    continue
        else:
            print(f"[recover] {name} 超时 8h", flush=True)
            sys.exit(2)
    print("[recover] 全部完成", flush=True)


if __name__ == "__main__":
    main()