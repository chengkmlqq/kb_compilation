"""收敛三个摘要编排的思考特征词清洗正则（幂等可重放，2026-10-15）。

背景（用户反馈「摘要任务都成功了，但两个文档摘要还是空的」）：
Map-Reduce 编排（b5954467d5344db0b87664477bcd1cac）最后 script「落页准备」的
META 正则把整行含「需要|要求|无?需|符合|检查|注意|控制|确保|大概|大约」等
词的行全部删除——这些词在正式管理制度文本里几乎每行都有（尤其 `无?需` 会命中
「需求」里的「需」）。实测：B1 文档合并稿 5244 字 9 行全部被删光（final_summary
空 → n9 skip_empty 跳过 → kb_document.summary 空）；正常文档也被误砍 80-95%
（5321→246 字）。该正则由 apply_summary_write_doc_summary.py 引入三个摘要编排
（Stuff/MapReduce/Refine）共用，故同类修类、三处同步。

修复：META 只保留真正的思考/指令特征与结构特征，删除正文高频词：
- 保留：我们/计数/草稿/数一下/内心/复述/只输出/字数/字符/chars/chinese/we /need/bullets/theme
- 指令复述（先一[句.]/再分点/输出最终）只在行首出现时删除
- 保留：markdown 标题行（^#{1,6}\s）与「《xxx》文档摘要」标题行
- 删除误伤词：需要/要求/无?需/大概/大约/确保/注意/检查/控制/符合

同步三处（与既有 apply_*_summary_fixes.py 同模式）：
  1. kb_tape_step.step_config.code（执行用）
  2. kb_tape.nodes JSON data.config.code（画布展示）
  3. kb_tape_template.nodes JSON data.config.code（模板）

幂等：code 中已含新 META 则跳过该步；重复执行 print 0 changed。

用法：docker cp scripts/apply_fix_summary_cleanup_meta.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_fix_summary_cleanup_meta.py
"""
import re
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep, TapeTemplate

# 三个摘要编排（SUMMARY_TAPE_STUFF/MAPREDUCE/REFINE 参数指向的线上 tape）
TAPES = [
    "ca5f228cffa44ee09a63e6bacfc674d8",   # Stuff
    "b5954467d5344db0b87664477bcd1cac",   # Map-Reduce
    "103e45f42fa14abfabb04735f298a50b",   # Refine
]

# 新 META（收敛后）：只删思考/指令特征与标题行，不删正文高频词
NEW_META = (
    r"(我们|计数|草稿|数一下|内心|复述|只输出|字数|字符|chars|chinese|we\s|need|"
    r"bullets?|theme|^\s*(先一[句.]|再分点|输出最终)|^#{1,6}\s|"
    r"《[^》]{1,80}》\s*文档摘要)"
)

# 匹配 code 中的 META 定义行（旧/新皆可，sub 后幂等）
META_LINE = re.compile(r"META = _re\.compile\(r'[^']*', _re\.I\)")

NEW_LINE = "META = _re.compile(r'%s', _re.I)" % NEW_META


def _patch_code(code: str) -> tuple[str, bool]:
    if not code or "META = _re.compile" not in code:
        return code, False
    patched, n = META_LINE.subn(NEW_LINE, code)
    return patched, n > 0 and patched != code


def _patch_nodes(nodes: list | None) -> int:
    changed = 0
    for n in nodes or []:
        d = dict(n.get("data") or {})
        cfg = dict(d.get("config") or {})
        code = cfg.get("code")
        if isinstance(code, str):
            new_code, ok = _patch_code(code)
            if ok:
                cfg["code"] = new_code
                d["config"] = cfg
                n["data"] = d
                changed += 1
    return changed


def main() -> None:
    s = get_sessionmaker()()
    try:
        for tape_id in TAPES:
            print(f"== tape {tape_id[:8]} ==")
            # 1. kb_tape_step
            steps = s.execute(
                select(TapeStep).where(TapeStep.tape_id == tape_id)
            ).scalars().all()
            step_changed = 0
            for st in steps:
                cfg = dict(st.step_config or {})
                code = cfg.get("code")
                if isinstance(code, str):
                    new_code, ok = _patch_code(code)
                    if ok:
                        cfg["code"] = new_code
                        st.step_config = cfg
                        step_changed += 1
            print(f"  kb_tape_step: {step_changed} 个步骤的 code 已更新")

            # 2. kb_tape.nodes（画布）
            tape = s.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
            if tape is None:
                print("  ! kb_tape 不存在，跳过 nodes 同步")
                continue
            n1 = _patch_nodes(tape.nodes)
            print(f"  kb_tape.nodes: {n1} 个节点已更新")

            # 3. kb_tape_template.nodes（模板）
            # 模板与 tape 的关联：模板 name 是 label 格式（如「文档摘要（Map-Reduce：…）」），
            # 与 tape.tape_name 不一致，按 tape.tape_label 模糊匹配（skill 2026-10-14 实踩）
            tpl_n = 0
            tlabel = (tape.tape_label or "").strip()
            if tlabel:
                tmpls = s.execute(
                    select(TapeTemplate).where(TapeTemplate.name.like(f"%{tlabel}%"))
                ).scalars().all()
                for tmpl in tmpls:
                    n2 = _patch_nodes(tmpl.nodes)
                    if n2:
                        tpl_n += n2
                        print(f"  模板[{tmpl.id[:8]} {tmpl.name[:30]}]: {n2} 个节点已更新")
            else:
                print("  ! tape_label 为空，模板节点未匹配（请手动核对）")
            print(f"  kb_tape_template.nodes: 共 {tpl_n} 个节点已更新")
        s.commit()
        print("\n完成。已同步三处；幂等可重放（重复执行为 0 changed）。")
    finally:
        s.close()


if __name__ == "__main__":
    main()