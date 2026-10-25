"""切片智能增强编排触发（2026-10-25 对齐 duowen ChunkExtend）。

文档解析切分完成后、向量化入库前，按 KB/文件增强配置触发「切片智能增强」
编排（tape_name=切片智能增强，seed scripts/seed_tape_data/切片智能增强.json）：
编排 LLM 逐片提取关键词/问题 + 文档级摘要/关键词，产物 bindings.enhancement：

    {"title": str, "abstract": str, "doc_keywords": [...],
     "chunks": [{"seq": int, "keywords": [...], "questions": [...]}, ...]}

入库侧（api/services/ingest.py）在向量 upsert 时把 enhance 字段随向量同介质
写入（PG enhance_meta 列 / ES enhance_* 字段）。

同步门语义：run_chunk_enhance_sync 阻塞轮询到 run 终态再返回 —— 满足
「增强完成后再入库」（Q2 用户确认）。失败/超时返回 None，上游照常入库
（无增强字段，检索退化为纯向量+关键词，不阻断上传）。

编排定位按 tape_name 查 effective（与聚合编排同法，无需 dim 配置，
apply_chunk_enhance_tape.py 灌入即生效）。
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from sqlalchemy import select

logger = logging.getLogger(__name__)

TAPE_NAME = "切片智能增强"

# 增强 run 等待上限：逐片 LLM（关键词+问题每片 2 次调用），大文档分钟级；
# 与摘要 Map-Reduce 的等待上限对齐。超时返回 None 不阻断入库。
ENHANCE_RUN_WAIT_TIMEOUT_S = 1800
RUN_POLL_INTERVAL_S = 2.0


def enhancement_enabled(kb_cfg: dict | None, doc_process_config: dict | None) -> bool:
    """增强开关解析：文件级 > KB 级（与 summary_strategy 覆盖链同构）。

    - doc_process_config["enhancement"] ("on"/"off"/""/None)：
        "on"=开启 / "off"=关闭 / 空=跟随 KB
    - kb_cfg（kb_datasource.enhancement_config）：{"enabled": bool, ...}
    """
    if isinstance(doc_process_config, dict):
        file_flag = str(doc_process_config.get("enhancement") or "").strip().lower()
        if file_flag == "on":
            return True
        if file_flag == "off":
            return False
    kb_cfg = dict(kb_cfg or {})
    return bool(kb_cfg.get("enabled"))


def normalize_enhancement_config(raw: dict | None) -> dict:
    """归一 + 校验知识库切片增强配置。

    规则：
      - enabled：bool（默认 False）
      - keywords_dividend：>0 整数（duowen 默认 120；每 N 字提 1 个关键词）
      - questions_dividend：>0 整数（默认 180）
      - max_chunks：>=0 整数（0=全部，默认 0）
    非法值回退默认，不抛错（配置容错，避免表单误填阻断保存）。
    """
    raw = dict(raw or {})
    cfg: dict = {
        "enabled": bool(raw.get("enabled")),
        "keywords_dividend": 120,
        "questions_dividend": 180,
        "max_chunks": 0,
    }
    try:
        v = int(raw.get("keywords_dividend") or 0)
        if v > 0:
            cfg["keywords_dividend"] = v
    except (TypeError, ValueError):
        pass
    try:
        v = int(raw.get("questions_dividend") or 0)
        if v > 0:
            cfg["questions_dividend"] = v
    except (TypeError, ValueError):
        pass
    try:
        v = int(raw.get("max_chunks") or 0)
        if v >= 0:
            cfg["max_chunks"] = v
    except (TypeError, ValueError):
        pass
    return cfg


def _find_tape_by_name(db, tape_name: str):
    """按编排名查已发布（effective）编排；未找到/未发布抛 ValueError。"""
    from api.models.orchestration import Tape

    tape = (
        db.execute(
            select(Tape).where(
                Tape.tape_name == tape_name,
                Tape.status.in_(("effective",)),
            )
        )
        .scalars()
        .first()
    )
    if tape is None:
        raise ValueError(f"切片增强编排「{tape_name}」未找到或未发布，请在服务编排中发布")
    return tape


def _enqueue_enhance_run(db, tape_id: str, kb_id: str, document_id: str, cookie: str = "") -> str:
    """建 kb_tape_run(env=prod) + KbOrchestrationRunTask modo_job + 投 orch 队列。

    骨架对齐 worker/tasks/doc_process.py::_enqueue_summary_task；返回 run_id。
    """
    from api.db import env_scope
    from api.models.framework import Job
    from api.services.orchestration import _create_tape_run

    created = _create_tape_run(
        db, tape_id,
        inputs={
            "kb_id": kb_id,
            "document_id": document_id,
            "doc_id": document_id,  # 兼容存量编排 def/script 读 doc_id
            "x_next_identity": cookie,
            "language": "中文",
            "purpose": "chunk_enhance",
        },
        env="prod",
    )
    run_id, run_env = created["run_id"], created["run_env"]
    job_id = f"CHUNK_ENHANCE_{uuid.uuid4().hex[:12]}"
    task_params = json.dumps({"runId": run_id, "env": run_env}, ensure_ascii=False)
    # 顺序契约：先 commit Job 行、再投 broker（worker 消费时行必须可见）
    db.add(
        Job(
            id=job_id,
            task_id=job_id,
            task_class="KbOrchestrationRunTask",
            queue_name="orch",
            task_params=task_params,
            trigger_type="API",
            state="PENDING",
        )
    )
    db.commit()

    try:
        from worker.celery_app import celery_app

        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="orch",
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时抛错，由调用方降级
        logger.warning("failed to enqueue chunk enhance run %s: %s", run_id, exc)
        raise
    return run_id


def _wait_run_terminal(run_id: str, timeout_s: int, poll_s: float = RUN_POLL_INTERVAL_S) -> str | None:
    """同步轮询 kb_tape_run 直到终态；返回 success/failed/cancelled，超时返回 None。"""
    from api.db import get_sessionmaker as _gsm
    from api.models.orchestration import TapeRun

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        db = _gsm()()
        try:
            st = db.execute(select(TapeRun.status).where(TapeRun.id == run_id)).scalar()
        finally:
            db.close()
        if st in ("success", "failed", "cancelled"):
            return str(st)
        time.sleep(poll_s)
    return None


def _read_run_binding(run_id: str, key: str):
    """读 kb_tape_run.bindings 的单个键（节点执行后 context.bindings 合并写回）。"""
    from api.db import get_sessionmaker as _gsm
    from api.models.orchestration import TapeRun

    db = _gsm()()
    try:
        b = db.execute(select(TapeRun.bindings).where(TapeRun.id == run_id)).scalar()
    finally:
        db.close()
    return (b or {}).get(key) if isinstance(b, dict) else None


def run_chunk_enhance_sync(
    db, kb_id: str, document_id: str, cookie: str = ""
) -> dict | None:
    """同步门执行切片增强编排：触发 → 轮询终态 → 读 enhancement。

    返回 enhancement dict；编排未发布/触发失败/超时/无产物 → None（不阻断入库）。
    """
    try:
        tape = _find_tape_by_name(db, TAPE_NAME)
    except Exception as exc:  # noqa: BLE001 — 编排缺失不阻断入库
        logger.warning("chunk enhance skipped (tape unavailable): %s", exc)
        return None

    try:
        run_id = _enqueue_enhance_run(db, tape.id, kb_id, document_id, cookie)
    except Exception as exc:  # noqa: BLE001 — 触发失败不阻断入库
        logger.warning("chunk enhance enqueue failed: %s", exc)
        return None

    status = _wait_run_terminal(run_id, ENHANCE_RUN_WAIT_TIMEOUT_S)
    if status != "success":
        logger.warning(
            "chunk enhance run %s ended %s (kb=%s doc=%s); ingest without enhancement",
            run_id, status, kb_id, document_id,
        )
        return None

    try:
        enhancement = _read_run_binding(run_id, "enhancement")
    except Exception as exc:  # noqa: BLE001
        logger.warning("chunk enhance read binding failed: %s", exc)
        return None
    if not isinstance(enhancement, dict) or not enhancement:
        logger.warning("chunk enhance run %s produced empty enhancement", run_id)
        return None
    logger.info(
        "chunk enhance done: kb=%s doc=%s run=%s chunks=%d",
        kb_id, document_id, run_id,
        len(enhancement.get("chunks") or []),
    )
    return enhancement