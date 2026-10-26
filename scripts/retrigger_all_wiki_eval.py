#!/usr/bin/env python3
"""宿主机侧全量 wiki(切片)评估驱动：清空旧 dim=chunk 记录后，对系统全部 9 个有文档的
知识库串行重发起「切片评估」（scope=all, env=prod），轮询到终态；worker 容器重建致
celery 任务丢失时判死重发（复用 retrigger_recover.py v3 自愈模式）。

用法（宿主机）：
  nohup python3 scripts/retrigger_all_wiki_eval.py >> /tmp/retrigger_all_wiki_eval.log 2>&1 &
  tail -f /tmp/retrigger_all_wiki_eval.log
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

# 全部有文档的知识库（2026-10-23 23:xx 盘点，含从未评估的心电图/供管制度35a8e888）
KBS = [
    ("610ece764fec40ac85e24dfa521154fa", "济南市监局知识库"),
    ("eb6ee0edcdc349ef980b642ff317c2de", "NG集团规范"),
    ("bb663ac4dcd141078c7b2dd806a12261", "心电图知识库"),
    ("35a8e88896d04da4b96e6407544bc878", "供管制度知识库"),
    ("9c4f58df500748a1bc9cee928c7bce23", "茶叶知识库"),
    ("7bab0d08b806409eb84f46432f62ddda", "供管制度知识库"),
    ("4d81657d4a8c4b0a86854d09433e2534", "木业知识库"),
    ("030e4f0d722044a68e22d2a436a69d92", "test"),
    ("fdf7054949894847861316c7cd682bda", "电信语义层"),
]


def py_in(code: str) -> str:
    last = (None, "")
    for attempt in range(5):
        r = subprocess.run(
            ["docker", "exec", API, VENV, "-c", code],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip()
        last = (r.returncode, (r.stderr or "")[-300:])
        # 容器重建/重启窗口：exec 会 SIGKILL(137) 或连接失败 —— 退避重试
        print(f"[all] exec 失败 rc={r.returncode}（第{attempt+1}/5 次，10s 后重试）: {last[1]}", flush=True)
        time.sleep(10)
    raise RuntimeError(f"exec 重试耗尽 rc={last[0]}: {last[1]}")


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


def clear_old_chunk_runs() -> int:
    """清空全部 dim=chunk 评估记录（kb_quality_run + kb_quality_report）。"""
    code = (
        "    rows=db.execute(text(\"SELECT id FROM kb_quality_run WHERE dim='chunk'\")).all()\n"
        "    ids=[r[0] for r in rows]\n"
        "    if ids:\n"
        "        db.execute(text(\"DELETE FROM kb_quality_report WHERE run_id IN :ids\"), {'ids': tuple(ids)})\n"
        "        db.execute(text(\"DELETE FROM kb_quality_run WHERE id IN :ids\"), {'ids': tuple(ids)})\n"
        "    print('CLEARED=' + str(len(ids)))\n"
    )
    out = _exec_db(code)
    # _exec_db 不返回 stdout；单独查一次
    data = _query("SELECT COUNT(*) FROM kb_quality_run WHERE dim='chunk'")
    rows = json.loads(data or "[]")
    return int(rows[0][0]) if rows else 0


def active_run_for(kb_id: str) -> str:
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
    data = _query(f"SELECT status, created_at FROM kb_tape_run WHERE id='{run_id}'")
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
            print(f"[all] launch 失败(第{i+1}次): {exc}，清 guard 后重试", flush=True)
            kill_active_for(kb_id)
            time.sleep(15)
    raise RuntimeError(f"launch 重试耗尽: {last}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-clear", action="store_true",
                        help="断点续跑：跳过清空旧记录，直接复用进行中 run / 续发未完成的库")
    args = parser.parse_args()

    print(f"[all] 开始全量切片评估驱动 {time.strftime('%F %T')}", flush=True)
    # 1) 清空旧 dim=chunk 记录（含 kb_quality_report）；断点续跑时跳过
    if not args.no_clear:
        remain = clear_old_chunk_runs()
        print(f"[all] 已清空旧 dim=chunk 评估记录，剩余 {remain} 条", flush=True)
    else:
        print("[all] 断点续跑模式：跳过清空", flush=True)
    # 同时清掉历史孤儿 run（tape 级 guard 防阻塞；仅限切片评估 tape 且 kb 在目标集）
    for kb_id, name in KBS:
        try:
            kill_active_for(kb_id)
        except RuntimeError as exc:
            print(f"[all] {name} 清孤儿失败(继续): {exc}", flush=True)
    print("[all] 已清理目标库的孤儿/进行中切片 run", flush=True)

    # 2) 逐库串行重发（全量语义：不跳过已有 success 的库）
    for kb_id, name in KBS:
        rid = active_run_for(kb_id)
        qid = ""
        if rid:
            print(f"[all] {name} 已有进行中 run {rid}，复用等待", flush=True)
        else:
            # 全量重评：清空后一律重新发起（不判 last_done success 跳过）
            qid = uuid.uuid4().hex[:32]
            rid = launch_with_retry(kb_id, qid)
            print(f"[all] {name} 已发起 run_id={rid} quality_id={qid} {time.strftime('%F %T')}", flush=True)

        deadline = time.monotonic() + 10 * 3600
        last = ""
        while time.monotonic() < deadline:
            time.sleep(60)
            try:
                st, rct = run_info(rid)
            except RuntimeError as exc:
                print(f"[all] {name} 轮询异常(重试): {exc}", flush=True)
                time.sleep(30)
                continue
            if st != last:
                print(f"[all] {name} {st} {time.strftime('%H:%M:%S')}", flush=True)
                last = st
            if st in ("success", "failed"):
                print(f"[all] {name} 终态: {st}", flush=True)
                break
            if st in ("queued", "running") and rct is not None:
                try:
                    wc = worker_created_epoch()
                except RuntimeError as exc:
                    print(f"[all] {name} inspect worker 异常(跳过本轮): {exc}", flush=True)
                    continue
                if wc > rct.timestamp() + 60:
                    print(f"[all] {name} 检测到 worker 重建(run {rct:%H:%M:%S} < worker {datetime.fromtimestamp(wc, timezone.utc):%H:%M:%S})，判定任务丢失，自愈重发", flush=True)
                    kill_active_for(kb_id)
                    qid = uuid.uuid4().hex[:32]
                    rid = launch_with_retry(kb_id, qid)
                    print(f"[all] {name} 重发 run_id={rid} quality_id={qid} {time.strftime('%F %T')}", flush=True)
                    last = ""
                    continue
        else:
            print(f"[all] {name} 超时 10h", flush=True)
            sys.exit(2)
    print("[all] 全部完成", flush=True)


if __name__ == "__main__":
    main()
