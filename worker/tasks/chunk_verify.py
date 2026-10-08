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
    """execute_modo_job 分发的 handler：核对/修复向量切片。"""
    from api.services.chunk_verify import fix_kb, verify_kb

    params = _params(task_params)
    kb_id = params.get("kb_id")
    if not kb_id:
        return {"success": False, "error": "missing kb_id in task_params"}

    action = params.get("action") or "check"
    if action == "fix":
        result = fix_kb(
            kb_id,
            fix_orphans=bool(params.get("fix_orphans", True)),
            fix_missing=bool(params.get("fix_missing", True)),
        )
    else:
        result = verify_kb(kb_id)
    return {"success": True, "output": f"chunk verify {action} kb={kb_id}: {result}"}


# 注册到 execute_modo_job 分发器（cron 表 / 手动投递均可触发）
from worker.tasks.scheduler import TASK_CLASS_REGISTRY  # noqa: E402

TASK_CLASS_REGISTRY[TASK_CLASS_CHUNK_VERIFY] = _handle_chunk_verify