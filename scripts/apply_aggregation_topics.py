"""应用「聚类知识-主题聚合」编排的 n1 主题注入增强（幂等可重放，2026-10-25）。

背景：知识库表单「知识聚合」配置允许用户填写主题列表。主题聚合编排的
n1（收集语料 + 构造聚类 prompt）原为固定「自动归纳 6~12 簇」；本次把
seed_tape_data/聚类知识-主题聚合.json 的 n1 脚本增强为支持 bindings.topics
——用户指定主题时按主题各成一簇（兜底保留自动聚类，不传 topics 行为不变）。

本脚本从 seed JSON（执行层真源）覆盖式重建该编排的 kb_tape_step（沿用
seed_framework_db._restore_tape_steps 的 id 前缀重映射逻辑），不经过画布
推导——与 _seed_tapes_from_exports 的 steps 路径同语义，只是对已存在编排
也强制刷新（seed 对已存在编排 skip，无法覆盖线上执行层）。

注意：用户在服务编排页「保存设计」会把画布 nodes 覆盖回执行层，冲掉本次
增强（画布 nodes 的 n1 脚本也已同步增强，因此保存设计后功能不丢，仅画布
version 覆盖执行层同源内容）。

用法（容器内，遵循仓库 apply 脚本惯例）：
    docker cp scripts/apply_aggregation_topics.py kb-api-server:/tmp/ && \
    docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_aggregation_topics.py
环境：DATABASE_URL（compose 已注入）。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

TAPE_NAME = "聚类知识-主题聚合"
SEED_FILE = Path(__file__).resolve().parent / "seed_tape_data" / "聚类知识-主题聚合.json"


def _restore_tape_steps(db, tape_id: str, steps: list) -> None:
    """按导出的执行层 steps 原样重建 kb_tape_step（seed_framework_db 同款）。"""
    prefix = tape_id[:12]

    def _sid(nid) -> str:
        bare = str(nid or "").rsplit("::", 1)[-1]
        return f"{prefix}::{bare}" if bare else ""

    db.query(TapeStep).filter(TapeStep.tape_id == tape_id).delete()
    db.commit()
    for st in sorted(steps, key=lambda s: int(s.get("step_seq") or 0)):
        sid = _sid(st.get("step_id") or "")
        db.add(
            TapeStep(
                id=sid or uuid.uuid4().hex[:64],
                tape_id=tape_id,
                step_inst=str(st.get("step_inst") or "def"),
                step_label=str(st.get("step_label") or "")[:128],
                step_config=st.get("step_config") or {},
                step_seq=int(st.get("step_seq") or 0),
                pre_step_ids=[x for x in (_sid(i) for i in (st.get("pre_step_ids") or [])) if x],
                next_step_ids=[x for x in (_sid(i) for i in (st.get("next_step_ids") or [])) if x],
                queue_name=str(st.get("queue_name") or ""),
            )
        )
    db.commit()


def main() -> None:
    if not SEED_FILE.is_file():
        print(f"[apply-agg-topics] 种子文件不存在: {SEED_FILE}")
        return
    doc = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    steps = d.get("steps") or []
    if not steps:
        print("[apply-agg-topics] 种子无执行层 steps，无法覆盖式重建（需先导出）")
        return

    db = get_sessionmaker()()
    try:
        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            print(f"[apply-agg-topics] 编排「{TAPE_NAME}」不存在，跳过（种子播种时会按新脚本建）")
            return
        # 校验 n1 已含主题注入增强
        n1 = next((s for s in steps if str(s.get("step_inst") or "") == "script"), None)
        has_topics = False
        if n1 is not None:
            code = str((n1.get("step_config") or {}).get("code") or "")
            has_topics = "用户指定主题" in code and "bindings.get('topics')" in code
        if not has_topics:
            print("[apply-agg-topics] 种子 n1 不含主题注入增强，请先更新种子文件再执行")
            return
        _restore_tape_steps(db, tape.id, steps)
        steps_now = db.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        print(f"[apply-agg-topics] 编排「{TAPE_NAME}」执行层已覆盖刷新: {len(steps_now)} 个步骤")
        n1_now = next((s for s in steps_now if s.step_inst == "script"), None)
        if n1_now and "用户指定主题" in str(n1_now.step_config.get("code") or ""):
            print("[apply-agg-topics] DONE：n1 已支持用户主题注入（bindings.topics），兜底自动聚类保留")
        else:
            print("[apply-agg-topics] WARN：刷新后 n1 未检测到主题注入，请检查")
    finally:
        db.close()


if __name__ == "__main__":
    main()
