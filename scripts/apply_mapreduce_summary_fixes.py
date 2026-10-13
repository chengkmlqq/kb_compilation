"""应用「摘要-Map-Reduce（分块合并）」编排的质量修复（幂等可重放）。

2026-10-13 实测：原配置下合并 LLM 输出"需要合并成150-250字…数一下字数…估计约230字"
+ 草稿/终版混排（inferai deepseek-v4-pro 摘要任务默认 thinking 开启，与 Refine/
Stuff 同款坑）。修复：
  - n5 子摘要 LLM / n7 合并 LLM: + system（禁思考/复述）+ thinking=disabled
    （n7 保留 skip_empty：loop 每轮跑，非最后一轮空 prompt 跳过）
  - n4 script: map prompt 去字数数字（80~150 字触发数数焦虑）→ 精炼子摘要
  - n6 script: reduce prompt 去字数数字（150~250 字）→ 精炼连贯
  - n8 script: 逐行过滤思考特征词兜底

注意：用户在服务编排页「保存设计/发布」会覆盖式同步回库冲掉本脚本的库内
修复，被覆盖后重跑本脚本即可恢复。

用法：docker cp scripts/apply_mapreduce_summary_fixes.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_mapreduce_summary_fixes.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep

TAPE = "b5954467d5344db0b87664477bcd1cac"

SYSTEM = (
    "你是资深中文文档摘要专家。直接输出最终摘要正文，"
    "禁止输出任何思考过程、分析草稿、字数统计、修改计划、对指令的复述或任何解释性文字。"
)

N4_CODE = """batch = bindings.get('batch') or {}
idx = int(batch.get('idx') or 0)
total = int(bindings.get('_total') or 1)
bindings['is_last'] = (idx >= total - 1)
text = str(batch.get('text') or '')
bindings['map_prompt'] = (
    '以下是文档的第 %d/%d 个片段，请生成精炼的中文子摘要（概括本片段的主题与要点，'
    '保留关键数字与结论）：\\n\\n' % (idx + 1, total)) + text
"""

N6_CODE = """subs = list(bindings.get('sub_summaries') or [])
s = str(bindings.get('sub_summary') or '').strip()
if s:
    subs.append(s)
bindings['sub_summaries'] = subs
if bindings.get('is_last') and subs:
    body = '\\n\\n'.join('[子摘要 %d] %s' % (i + 1, t) for i, t in enumerate(subs))
    bindings['reduce_prompt'] = (
        '以下是同一文档全部 %d 个子摘要：\\n\\n' % len(subs) + body +
        '\\n\\n请将它们合并为一份完整连贯的中文文档摘要。要求：\\n'
        '1) 先一句话概括主题；\\n'
        '2) 再分点列出核心要点，必须完整覆盖上述所有子摘要的全部要点，'
        '逐条合并、不得遗漏任何子摘要的内容；\\n'
        '3) 直接输出摘要正文，不要附加任何说明、复述或多余文字。')
    bindings['merge_ready'] = True
else:
    bindings['reduce_prompt'] = ''
    bindings['merge_ready'] = False
"""

N8_CODE = """import hashlib, re as _re
if bindings.get('merge_ready'):
    s = str(bindings.get('merged_summary') or '').strip()
    # 2026-10-13 清洗：剥离模型思考/确认过程行（复述指令、字数统计、草稿、自我检查等）
    _META = _re.compile(
        r'(需要|要求|我们|计数|草稿|数一下|大概|大约|内心|确保|复述|无?需|'
        r'先一[句.]|再分点|只输出|注意|检查|控制|符合|输出.{0,8}(摘要|正文)|'
        r'字数|字符|chars|chinese|we |need|bullets?|theme)',
        _re.I,
    )
    _lines = []
    for _ln in s.split('\\n'):
        _t = _ln.strip().lstrip('。').strip()
        if not _t:
            continue
        if _META.search(_t):
            continue
        _lines.append(_t)
    s = '\\n'.join(_lines).strip()
    doc_id = str(bindings.get('document_id') or '')
    bindings['final_summary'] = s
    bindings['page_title'] = '文档摘要（Map-Reduce 分块合并）' if s else ''
    bindings['page_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10] + '-mapreduce'
    bindings['page_content'] = ('# 文档摘要（Map-Reduce 分块合并）\\n\\n' + s) if s else ''
else:
    bindings['page_title'] = ''
    bindings['page_slug'] = ''
    bindings['page_content'] = ''
"""


def main() -> None:
    s = get_sessionmaker()()
    try:
        steps = {st.id.rsplit("::", 1)[-1]: st
                 for st in s.execute(select(TapeStep).where(TapeStep.tape_id == TAPE)).scalars().all()}
        # n5 / n7: 保留原配置追加 system/thinking（n7 保留 skip_empty）
        per_sid = {}
        for sid in ("n5", "n7"):
            cfg = dict(steps[sid].step_config or {})
            cfg["system"] = SYSTEM
            cfg["thinking"] = "disabled"
            steps[sid].step_config = cfg
            per_sid[sid] = cfg
            print("step updated:", sid, "keys:", sorted(cfg.keys()))
        for sid, code in (("n4", N4_CODE), ("n6", N6_CODE), ("n8", N8_CODE)):
            cfg = dict(steps[sid].step_config or {})
            cfg["code"] = code
            steps[sid].step_config = cfg
            per_sid[sid] = {"code": code}
            print("step updated:", sid)
        t = s.execute(select(Tape).where(Tape.id == TAPE)).scalars().first()
        if t is not None:
            for n in (t.nodes or []):
                if str(n.get("id")) in per_sid:
                    d = dict(n.get("data") or {})
                    d["config"] = dict(per_sid[str(n.get("id"))])
                    n["data"] = d
            t.nodes = t.nodes
            print("tape nodes synced")
        s.commit()
    finally:
        s.close()


if __name__ == "__main__":
    main()