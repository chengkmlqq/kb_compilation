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
    # 函数式替换：替换模板字符串会把 \s 等当转义解析（bad escape），函数不解析
    patched, n = META_LINE.subn(lambda _m: NEW_LINE, code)
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


def _sync_nodes_from_steps(nodes: list | None, steps_map: dict) -> int:
    """以 kb_tape_step（执行真源）反向同步画布/模板节点：config/stepInst/label。

    背景（2026-10-15 实测）：三个摘要编排的 tape.nodes / tape_template.nodes 长期
    与 kb_tape_step 脱节（nodes 里 n8 还是「写 wiki 摘要页」远古版：含 hashlib/
    page_*、无 META 清洗）。用户在服务编排页点「保存设计」会用画布 nodes 覆盖式
    重建 kb_tape_step（save_tape_design 删子表重建）→ 执行层修复被冲掉。此处把
    step 层当前状态回写画布与模板，使保存不再回退。
    """
    changed = 0
    for n in nodes or []:
        sid = str(n.get("id") or "")
        if sid not in steps_map:
            continue
        inst, label, cfg = steps_map[sid]
        d = dict(n.get("data") or {})
        if d.get("config") != cfg or d.get("stepInst") != inst or d.get("label") != label:
            d["config"] = dict(cfg)
            d["stepInst"] = inst
            d["label"] = label
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
            steps_map: dict[str, tuple] = {}
            for st in steps:
                sid = st.id.rsplit("::", 1)[-1]
                cfg = dict(st.step_config or {})
                code = cfg.get("code")
                if isinstance(code, str):
                    new_code, ok = _patch_code(code)
                    if ok:
                        cfg["code"] = new_code
                        st.step_config = cfg
                        step_changed += 1
                steps_map[sid] = (st.step_inst or "", st.step_label or "", cfg)
            print(f"  kb_tape_step: {step_changed} 个步骤的 code 已更新")

            # 2. kb_tape.nodes（画布）
            tape = s.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
            if tape is None:
                print("  ! kb_tape 不存在，跳过 nodes 同步")
                continue
            n1 = _patch_nodes(tape.nodes)
            n1s = _sync_nodes_from_steps(tape.nodes, steps_map)
            print(f"  kb_tape.nodes: {n1} 个节点 META 已更新, {n1s} 个节点已对齐 step")

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
                    n2s = _sync_nodes_from_steps(tmpl.nodes, steps_map)
                    if n2 or n2s:
                        tpl_n += n2 + n2s
                        print(f"  模板[{tmpl.id[:8]} {tmpl.name[:30]}]: META {n2} + 对齐step {n2s}")
            else:
                print("  ! tape_label 为空，模板节点未匹配（请手动核对）")
            print(f"  kb_tape_template.nodes: 共 {tpl_n} 个节点已更新")
        s.commit()
        print("\n完成。已同步三处；幂等可重放（重复执行为 0 changed）。")
    finally:
        s.close()


if __name__ == "__main__":
    main()