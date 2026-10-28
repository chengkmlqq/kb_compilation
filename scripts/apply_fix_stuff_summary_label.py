"""摘要-Stuff 编排末节点 label 修正（幂等可重放）：「写摘要页」→「写文档摘要」。

背景（2026-10-28）：kb_write_doc_summary 原子把摘要回写到 kb_document.summary /
summary_status（不是写 wiki 页面）。三个摘要编排中 Map-Reduce / Refine 的
kb_tape_step.step_label 已是「写文档摘要」，唯独 Stuff 残留历史文案「写摘要页」，
运行日志出现误导（让人以为在写 wiki 摘要页）。

改动：按 tape_name 定位「摘要-Stuff（全文直塞）」，把其最后一个节点
（step_inst=kb_write_doc_summary）的 step_label 改为「写文档摘要」；
kb_tape.nodes 画布 label 同步（已一致则不动）。模板层无「摘要」相关模板，跳过。

用法：docker cp scripts/apply_fix_stuff_summary_label.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_fix_stuff_summary_label.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

TAPE_NAME = "摘要-Stuff（全文直塞）"
OLD_LABEL = "写摘要页"
NEW_LABEL = "写文档摘要"


def main() -> None:
    s = get_sessionmaker()()
    try:
        tape = s.execute(
            select(Tape).where(Tape.tape_name == TAPE_NAME)
        ).scalars().first()
        if tape is None:
            print(f"[fix-label] 未找到编排「{TAPE_NAME}」，跳过")
            return

        changed = 0
        # 1. kb_tape_step（运行日志/步骤执行层）
        steps = s.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        for st in steps:
            if st.step_inst == "kb_write_doc_summary" and st.step_label == OLD_LABEL:
                st.step_label = NEW_LABEL
                changed += 1
                print(f"[fix-label] step {st.id} 「{OLD_LABEL}」→「{NEW_LABEL}」")

        # 2. kb_tape.nodes（画布展示层，幂等：已一致不动）
        if tape.nodes:
            for n in tape.nodes:
                d = dict(n.get("data") or {})
                if d.get("stepInst") == "kb_write_doc_summary" and d.get("label") == OLD_LABEL:
                    d["label"] = NEW_LABEL
                    n["data"] = d
                    changed += 1
                    print(f"[fix-label] node {n.get('id')} 「{OLD_LABEL}」→「{NEW_LABEL}」")

        if changed:
            s.commit()
            print(f"DONE: 修正 {changed} 处 label（{TAPE_NAME}）")
        else:
            print("DONE: 无待修正 label（已是「写文档摘要」或形态不匹配）")
    finally:
        s.close()


if __name__ == "__main__":
    main()