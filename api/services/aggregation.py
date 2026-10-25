"""知识聚合配置服务（2026-10-25）——知识库「知识聚合」开关配置。

职责：
  1. normalize_aggregation_config：归一 + 校验知识库聚合配置
     （开启主题聚类时主题列表必填；cron 表达式默认每日 02:00）。
  2. sync_aggregation_crons：按配置增删知识库的聚合定时任务
     （modo_cron_task，id 确定性 agg_topic_{kb_id} / agg_version_{kb_id}，
     幂等 upsert/删除；scan_cron_tasks 扫描器按 cron 表达式周期触发）。
  3. enqueue_aggregation_run：立即触发一次聚合编排（API「立即聚合」与
     cron 触发的 KbAggregationRunTask 共用）——按编排名查 Tape，建
     kb_tape_run（env=prod，与文档同库），写 KbOrchestrationRunTask
     modo_job（任务监控可见），投 orch 队列 DAG 执行。

两个编排的 tape 名常量与 cron 前缀集中在此，前端/worker 复用。
"""
from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select

logger = logging.getLogger(__name__)

# 知识聚合编排（kb_tape.tape_name，与 scripts/seed_tape_data 种子一致）
TAPE_NAME_TOPIC_CLUSTER = "聚类知识-主题聚合"
TAPE_NAME_VERSION_EVOLUTION = "制度版本演进"

# cron 任务 id 前缀（CronTask.id String(64)，kb_id 32 hex，合计 <64）
CRON_ID_PREFIX_TOPIC = "agg_topic_"
CRON_ID_PREFIX_VERSION = "agg_version_"

# 定时频率：用户确认固定「一天一次」，默认每日 02:00（业务低峰）
DEFAULT_CRON_EXPRESSION = "0 2 * * *"

# 编排执行环境：聚合编排读写正式库业务数据（wiki 页/文档摘要），
# 与上传链一致用 prod（execute_tape 手动执行默认 dev 是测试语义，不走）
AGGREGATION_RUN_ENV = "prod"

# cron 触发编排的任务类（worker/tasks/aggregation.py 注册）
TASK_CLASS_AGGREGATION = "KbAggregationRunTask"


def normalize_aggregation_config(raw: dict | None) -> dict:
    """归一 + 校验知识聚合配置。

    规则：
      - topic_cluster_enabled=True 时 topic_cluster_topics 必填（至少 1 个，
        去空白去重）；False 时主题列表清空。
      - cron_expression 非空时格式校验（复用 cron 路由的 5 段式校验）；
        缺省用 DEFAULT_CRON_EXPRESSION。
      - 未识别键忽略；未知键不影响存量语义（字段在 payload 才更新）。
    """
    raw = dict(raw or {})
    cfg: dict = {}
    topic_enabled = bool(raw.get("topic_cluster_enabled"))
    cfg["topic_cluster_enabled"] = topic_enabled
    topics = [
        str(t).strip()
        for t in (raw.get("topic_cluster_topics") or [])
        if str(t).strip()
    ]
    # 去重保序（用户重复录入同主题不重复建簇）
    seen: set[str] = set()
    deduped: list[str] = []
    for t in topics:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    if topic_enabled and not deduped:
        raise ValueError("开启主题聚类后请至少填写一个主题")
    cfg["topic_cluster_topics"] = deduped

    cfg["version_cluster_enabled"] = bool(raw.get("version_cluster_enabled"))

    expr = str(raw.get("cron_expression") or "").strip() or DEFAULT_CRON_EXPRESSION
    _validate_cron_expression(expr)
    cfg["cron_expression"] = expr
    return cfg


def _validate_cron_expression(expr: str) -> str:
    """校验 cron 表达式（5 段式，与 api/routers/cron.py 同规则）。

    支持：*/N、逗号列表、范围、*；分/时/日/月/周。仅做形状校验，
    实际解析由 worker 侧 croniter 完成（表达式非法时扫描器跳过该行）。
    """
    parts = [p.strip() for p in expr.split()]
    if len(parts) != 5:
        raise ValueError(f"定时表达式不合法（需 5 段，如 {DEFAULT_CRON_EXPRESSION}）：{expr}")
    import re

    token_re = re.compile(r"^(\*|[0-9]+)(/([0-9]+))?$|^([0-9]+)-([0-9]+)(/([0-9]+))?$")
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    for i, part in enumerate(parts):
        lo, hi = ranges[i]
        if part == "*":
            continue
        for piece in part.split(","):
            piece = piece.strip()
            if not piece:
                raise ValueError(f"定时表达式不合法：{expr}")
            if piece == "*":
                continue
            m = token_re.match(piece)
            if not m:
                raise ValueError(f"定时表达式不合法：{expr}")
            # 匹配到 * /N 或 a-b(/N) 形态；提取数值边界校验
            if m.group(1) is not None:  # */N
                if m.group(3):
                    step = int(m.group(3))
                    if step < 1:
                        raise ValueError(f"定时表达式不合法：{expr}")
                continue
            # a-b 或 a-b/N
            a, b = int(m.group(4)), int(m.group(5))
            if a > b or a < lo or b > hi:
                raise ValueError(f"定时表达式不合法：{expr}")
    return expr


def aggregation_cron_ids(kb_id: str) -> tuple[str, str]:
    """确定性 cron 任务 id（幂等 upsert 锚点）。"""
    return f"{CRON_ID_PREFIX_TOPIC}{kb_id}", f"{CRON_ID_PREFIX_VERSION}{kb_id}"


def sync_aggregation_crons(db, kb_id: str, kb_name: str, cfg: dict) -> dict:
    """按聚合配置同步定时任务（modo_cron_task）：开启 → upsert，关闭 → 删除。

    返回 {"topic": {"id": ...|None, "enabled": bool},
           "version": {"id": ...|None, "enabled": bool}}。
    """
    from api.models.framework import CronTask

    cfg = normalize_aggregation_config(cfg)
    expr = cfg["cron_expression"]
    topic_id, version_id = aggregation_cron_ids(kb_id)
    out: dict = {}

    def _upsert(cron_id: str, tape_name: str, label: str, topics: list[str] | None) -> str:
        row = db.execute(select(CronTask).where(CronTask.id == cron_id)).scalars().first()
        fire_params = json.dumps(
            {
                "kb_id": kb_id,
                "tape_name": tape_name,
                "topics": topics or [],
                "env": AGGREGATION_RUN_ENV,
            },
            ensure_ascii=False,
        )
        if row is None:
            db.add(
                CronTask(
                    id=cron_id,
                    name=f"知识聚合-{label}",
                    label=f"知识库「{kb_name}」{label}（每日自动执行）",
                    cron_expression=expr,
                    task_class=TASK_CLASS_AGGREGATION,
                    state="1",
                    fire_params=fire_params,
                    queue_name="orch",
                    next_fire_time=None,  # 扫描器首次计算下次触发时间
                )
            )
        else:
            # 配置变更 → 更新参数 + 重置下次触发时间（让扫描器按新表达式重算）
            row.name = f"知识聚合-{label}"
            row.label = f"知识库「{kb_name}」{label}（每日自动执行）"
            row.cron_expression = expr
            row.task_class = TASK_CLASS_AGGREGATION
            row.state = "1"
            row.fire_params = fire_params
            row.queue_name = "orch"
            row.next_fire_time = None
        db.commit()
        return cron_id

    if cfg["topic_cluster_enabled"]:
        out["topic"] = {
            "id": _upsert(
                topic_id, TAPE_NAME_TOPIC_CLUSTER, "主题聚类",
                cfg["topic_cluster_topics"],
            ),
            "enabled": True,
        }
    else:
        row = db.execute(select(CronTask).where(CronTask.id == topic_id)).scalars().first()
        if row is not None:
            db.delete(row)
            db.commit()
        out["topic"] = {"id": None, "enabled": False}

    if cfg["version_cluster_enabled"]:
        out["version"] = {
            "id": _upsert(version_id, TAPE_NAME_VERSION_EVOLUTION, "版本聚类", None),
            "enabled": True,
        }
    else:
        row = db.execute(select(CronTask).where(CronTask.id == version_id)).scalars().first()
        if row is not None:
            db.delete(row)
            db.commit()
        out["version"] = {"id": None, "enabled": False}

    return out


def delete_aggregation_crons(db, kb_id: str) -> None:
    """删除知识库的聚合定时任务（delete_kb 级联调用）。"""
    from api.models.framework import CronTask

    topic_id, version_id = aggregation_cron_ids(kb_id)
    for cron_id in (topic_id, version_id):
        row = db.execute(select(CronTask).where(CronTask.id == cron_id)).scalars().first()
        if row is not None:
            db.delete(row)
    db.commit()


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
        raise ValueError(f"聚合编排「{tape_name}」未找到或未发布，请在服务编排中发布")
    return tape


def enqueue_aggregation_run(db, tape_name: str, kb_id: str, topics: list[str] | None = None) -> dict:
    """立即触发一次聚合编排（API「立即聚合」与 cron 任务共用入口）。

    链路（对齐 worker 上传链 _enqueue_build_engine_task 的顺序契约）：
      建 kb_tape_run（env=prod，与文档同库）→ 写 KbOrchestrationRunTask
      modo_job（任务监控可见）→ 投 orch 队列由调度中枢 DAG 执行。

    幂等防重（2026-10-14 execute_tape 同语义）：同一编排已有 queued/running
    的 run 时跳过，返回 skipped=True（不重复建 run、不重复投递）。
    """
    from api.db import env_scope
    from api.models.framework import Job
    from api.services.orchestration import _create_tape_run

    tape = _find_tape_by_name(db, tape_name)
    tape_id = tape.id

    with env_scope(AGGREGATION_RUN_ENV):
        from api.models.orchestration import TapeRun

        active = (
            db.query(TapeRun)
            .filter(TapeRun.tape_id == tape_id, TapeRun.status.in_(("queued", "running")))
            .first()
        )
    if active is not None:
        return {
            "success": True,
            "skipped": True,
            "run_id": active.id,
            "message": f"编排「{tape.tape_label or tape_name}」已有运行中的任务，跳过重复执行",
        }

    created = _create_tape_run(
        db, tape_id,
        inputs={"kb_id": kb_id, "topics": topics or []},
        env=AGGREGATION_RUN_ENV,
    )
    run_id, run_env = created["run_id"], created["run_env"]

    job_id = f"ORCH_RUN_{uuid.uuid4().hex[:12]}"
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
    except Exception as exc:  # noqa: BLE001 — broker 不可达时返回 False，不抛
        logger.warning("failed to enqueue aggregation run %s: %s", run_id, exc)
        return {
            "success": False,
            "error": f"编排任务投递失败: {exc}",
            "run_id": run_id,
        }

    logger.info("aggregation run enqueued: tape=%s kb=%s run=%s", tape_name, kb_id, run_id)
    return {"success": True, "skipped": False, "run_id": run_id, "job_id": job_id}
