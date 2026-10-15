"""应用「题材分析-领域判定」编排并启用上传链自动领域分发（幂等可重放）。

2026-10-16 用户需求：知识库创建时未选构建引擎、上传文件也未选时，
构建任务等摘要结束后由 LLM 基于摘要 + 平台已有领域构建编排的划分，
自动判定使用哪个编排继续构建。

本脚本做两件事：
1) 建「题材分析-领域判定」编排（Tape + kb_tape_step 经 save_tape_design），
   节点/边/脚本从 seed_framework_db.DOMAIN_RESOLVE_TAPE 取，模板更新后重跑
   即同步（tape 已存在则复用其 id 并覆盖式刷新步骤，比 seed 的 skip 更适合线上修补）；
2) 把编排 id 写入 modo_dim(SYSTEM_CONFIG).DOMAIN_RESOLVE_TAPE —— 上传链
   doc_process._enqueue_domain_guided_build 读该参数决定是否启用；
   --disable 参数可清空参数（停用自动领域分发，恢复回退 skill 链）。

注意：用户在服务编排页「保存设计/发布」会覆盖式同步回 kb_tape_step，冲掉本脚本
刷入的节点增强键（如 system/thinking），重跑本脚本即恢复。

用法：
    docker cp scripts/apply_domain_resolve_tape.py kb-api-server:/tmp/ && \\
    docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_domain_resolve_tape.py
    # 停用：
    docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_domain_resolve_tape.py --disable
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.framework import Dim
from api.models.orchestration import Tape

TAPE_NAME = "题材分析-领域判定"
DIM_CODE = "DOMAIN_RESOLVE_TAPE"
DIM_GROUP = "SYSTEM_CONFIG"


def _load_template() -> dict:
    """从仓库/镜像内 seed_framework_db.py 导入编排模板（与种子同源）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seed_framework_db import DOMAIN_RESOLVE_TAPE  # noqa: PLC0415

    return DOMAIN_RESOLVE_TAPE


def _disable(db) -> None:
    dim = db.execute(
        select(Dim).where(Dim.dim_group == DIM_GROUP, Dim.dim_code == DIM_CODE)
    ).scalars().first()
    if dim is None:
        print(f"[apply-resolve] 参数 {DIM_CODE} 不存在（未启用过），无需停用")
        return
    dim.dim_value = ""
    db.commit()
    print(f"[apply-resolve] 参数 {DIM_CODE} 已清空 → 上传链回退 skill 链构建")


def main() -> None:
    if "--disable" in sys.argv:
        db = get_sessionmaker()()
        try:
            _disable(db)
        finally:
            db.close()
        return

    import uuid as _uuid

    from api.services.orchestration import ensure_tape_category, save_tape_design

    tpl = _load_template()
    nodes = list(tpl["nodes"])
    edges = list(tpl["edges"])
    exec_params = tpl.get("exec_params") or {}

    db = get_sessionmaker()()
    try:
        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            ensure_tape_category(db, "通用", cat_type="tape")
            tape = Tape(
                id=_uuid.uuid4().hex[:32],
                tape_name=TAPE_NAME,
                tape_label="题材分析（自动选择领域构建编排）",
                tape_descr=(
                    "根据文档摘要与平台已有领域构建编排的划分，自动判定该文档应使用"
                    "哪个编排进行后续 wiki 构建。产物：resolved_tape_id。"
                ),
                tape_type="general",
                category="通用",
                status="draft",
                nodes=nodes,
                edges=edges,
                exec_params=exec_params,
                create_user=None,
            )
            db.add(tape)
            db.commit()
            print(f"[apply-resolve] 编排「{TAPE_NAME}」已创建 id={tape.id[:12]}…")
        else:
            print(f"[apply-resolve] 编排「{TAPE_NAME}」已存在 id={tape.id[:12]}…，覆盖式刷新步骤")
        save_tape_design(db, tape.id, nodes, edges, exec_params)

        # 写参数
        dim = db.execute(
            select(Dim).where(Dim.dim_group == DIM_GROUP, Dim.dim_code == DIM_CODE)
        ).scalars().first()
        if dim is None:
            db.add(Dim(
                id=_uuid.uuid4().hex[:32],
                dim_group=DIM_GROUP, dim_code=DIM_CODE, dim_value=tape.id,
                dim_desc="题材分析编排-自动选择领域构建（kb_tape.id，留空=不启用自动领域分发）",
                parent_dim_code=None, seq=41, state="1",
            ))
            print(f"[apply-resolve] 参数 {DIM_CODE} 新建 = {tape.id[:12]}…")
        else:
            dim.dim_value = tape.id
            print(f"[apply-resolve] 参数 {DIM_CODE} 更新 = {tape.id[:12]}…")
        db.commit()

        # 校验
        from api.models.orchestration import TapeStep

        steps = db.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        print(f"[apply-resolve] 步骤 {len(steps)} 个: "
              + ", ".join(f"{s.step_label}({s.step_inst})" for s in steps))
        print("[apply-resolve] DONE：上传链未选构建引擎的文档将走「摘要 → 题材分析 → 领域编排构建」链路")
    finally:
        db.close()


if __name__ == "__main__":
    main()