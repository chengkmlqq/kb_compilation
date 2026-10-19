"""向量切片核对/修复任务（KbChunkVerifyTask）。

方案 C（含自动修复）：
- 核对：MySQL doc_chunk 切片 ↔ VectorStore 向量，输出缺失/孤儿报告
- 修复：清孤儿向量 + 重嵌入缺失切片（embedding 调用在 worker 内，避免阻塞 API）

fire_params（cron 任务参数 / 手动投递 JSON）::

    {"kb_id": "xxx", "action": "check"|"fix", "fix_orphans": true, "fix_missing": true}

- action=check（默认）只核对并落报告；action=fix 执行修复后复检。
- kb_id 必填。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TASK_CLASS_CHUNK_VERIFY = "KbChunkVerifyTask"


def _params(task_params: str | None) -> dict:
    try:
        return json.loads(task_params) if task_params else {}
    except (TypeError, ValueError):
        return {}


def _handle_chunk_verify(job_id: str, task_params: str | None = None) -> dict:
    """execute_modo_job 分发的 handler：核对/修复向量切片。

    单个 KB：task_params 带 kb_id；缺省 kb_id = 全部可见知识库
    （state='1'，供 cron 每日核对修复用，避免孤儿向量长期累积）。
    """
    from api.services.chunk_verify import fix_kb, verify_kb

    params = _params(task_params)
    action = params.get("action") or "check"
    fix_orphans = bool(params.get("fix_orphans", True))
    fix_missing = bool(params.get("fix_missing", True))

    kb_id = params.get("kb_id")
    if kb_id:
        targets = [str(kb_id).strip()]
    else:
        # 全库模式：遍历 state=1 的知识库（对齐 qa 全库问答的可见范围口径）
        from api.db import get_sessionmaker
        from api.models.knowledge import KbDatasource
        from sqlalchemy import select

        db = get_sessionmaker()()
        try:
            targets = [
                str(r[0])
                for r in db.execute(
                    select(KbDatasource.id).where(KbDatasource.state == "1")
                ).all()
            ]
        finally:
            db.close()

    results: dict = {}
    for kid in targets:
        try:
            if action == "fix":
                results[kid] = fix_kb(
                    kid, fix_orphans=fix_orphans, fix_missing=fix_missing
                )
            else:
                results[kid] = verify_kb(kid)
        except Exception as exc:  # noqa: BLE001 - 单库失败不阻断全库批跑
            logger.exception("chunk verify failed for kb=%s", kid)
            results[kid] = {"error": str(exc)}
    return {
        "success": True,
        "output": f"chunk verify {action} kb={','.join(targets) or '无'}: {results}",
    }


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_CHUNK_VERIFY] = _handle_chunk_verify