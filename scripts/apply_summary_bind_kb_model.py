"""给三个摘要编排的 LLM 节点绑定知识库配置的摘要模型（幂等可重放）。

背景（2026-10-14）：直连 LLM 摘要链路移除后，摘要统一由编排生成，但编排
LLM 节点此前未绑定 model——实际用的是平台 chat 默认模型，知识库上配置的
summary_model_id 不生效（仅 kb_read_table 链路注入 bindings，摘要编排的
kb_read_doc 链路没有）。本轮：
  1. 引擎 KbReadDocStep 读 KB 的 summary_model_id 注入 bindings
     （api/services/orchestration_engine.py，随代码提交）
  2. 本脚本：三个摘要编排的 kb_llm_chat 节点 step_config 加
     model="{{summary_model_id}}"（三处同步：kb_tape_step / tape.nodes /
     tape_template.nodes，与 apply_summary_write_doc_summary.py 同模式）

绑定后：KB 配置了摘要模型（summary_model_id）就走它；未配置时 bindings 里
summary_model_id 为空串，KbLlmChatStep 回落系统默认模型（引擎已兜底
{{...}} 字面量残留）。

用法：docker cp scripts/apply_summary_bind_kb_model.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_summary_bind_kb_model.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep, TapeTemplate

# tape_id -> LLM 节点 id 列表（kb_llm_chat）
TAPES = {
    "ca5f228cffa44ee09a63e6bacfc674d8": ["n3"],                       # Stuff 全文直塞
    "b5954467d5344db0b87664477bcd1cac": ["n5", "n7"],                 # Map-Reduce 子摘要+合并
    "103e45f42fa14abfabb04735f298a50b": ["n5"],                       # Refine 增量修订
}
# 模板 label -> 同节点 id（模板 name 与 tape_name 不一致，按 label 匹配）
TPL_LABELS = {
    "ca5f228cffa44ee09a63e6bacfc674d8": "文档摘要（Stuff：全文一次直塞 LLM，适合中小文档）",
    "b5954467d5344db0b87664477bcd1cac": "文档摘要（Map-Reduce：分块子摘要再合并，适合大文档）",
    "103e45f42fa14abfabb04735f298a50b": "文档摘要（Refine：摘要随批次滚动更新，适合大文档）",
}


def _apply_model_to_nodes(nodes: list | None, llm_ids: list[str]) -> None:
    for n in nodes or []:
        if str(n.get("id")) in llm_ids:
            d = dict(n.get("data") or {})
            cfg = dict(d.get("config") or {})
            cfg["model"] = "{{summary_model_id}}"
            d["config"] = cfg
            n["data"] = d


def main() -> None:
    s = get_sessionmaker()()
    try:
        llm_id_set = set()
        for ids in TAPES.values():
            llm_id_set.update(ids)
        for tape_id, llm_ids in TAPES.items():
            # 1. kb_tape_step
            steps = s.execute(select(TapeStep).where(TapeStep.tape_id == tape_id)).scalars().all()
            changed = 0
            for st in steps:
                sid = st.id.rsplit("::", 1)[-1]
                if sid in llm_ids:
                    cfg = dict(st.step_config or {})
                    cfg["model"] = "{{summary_model_id}}"
                    st.step_config = cfg
                    changed += 1
            print(f"[{tape_id[:8]}] kb_tape_step +model {changed} nodes")

            # 2. tape.nodes
            tape = s.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
            if tape is not None:
                _apply_model_to_nodes(tape.nodes, llm_ids)
                tape.nodes = tape.nodes
                print(f"[{tape_id[:8]}] tape.nodes +model")

            # 3. tape_template.nodes（按 label 匹配）
            tpl = s.execute(
                select(TapeTemplate).where(TapeTemplate.label == TPL_LABELS[tape_id])
            ).scalars().first()
            if tpl is not None:
                _apply_model_to_nodes(tpl.nodes, llm_ids)
                tpl.nodes = tpl.nodes
                print(f"[{tape_id[:8]}] template.nodes +model")
        s.commit()
        print("DONE: 三个摘要编排 LLM 节点已绑定 KB 摘要模型")
    finally:
        s.close()


if __name__ == "__main__":
    main()