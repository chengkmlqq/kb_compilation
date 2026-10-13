"""应用「摘要-Stuff（全文直塞）」编排的质量修复（幂等可重放）。

2026-10-13：Refine 摘要编排实测证明 inferai deepseek-v4-pro 摘要任务默认
thinking 开启、思考草稿混入 content 的风险（行为不稳定，4/6 轮复现）。Stuff
本轮输出虽干净，但 n3 无 system/thinking 配置属同款隐患，预防性对齐：
  - n3 kb_llm_chat: + system（禁思考/复述）+ thinking=disabled（引擎已支持透传）
  - n2 script: prompt 去字数数字（150~250 触发数数焦虑）→ 精炼连贯表述
  - n4 script: 逐行过滤思考特征词兜底

与 Refine 同样注意：用户在服务编排页「保存设计/发布」会覆盖式同步回库冲掉
本脚本的库内修复，被覆盖后重跑本脚本即可恢复。

用法：docker cp scripts/apply_stuff_summary_fixes.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_stuff_summary_fixes.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep, TapeTemplate

TAPE = "ca5f228cffa44ee09a63e6bacfc674d8"

N3_SYSTEM = (
    "你是资深中文文档摘要专家。直接输出最终摘要正文，"
    "禁止输出任何思考过程、分析草稿、字数统计、修改计划、对指令的复述或任何解释性文字。"
)

N2_CODE = """chunks = bindings.get('chunks') or []
parts = []
for c in chunks:
    text = (c.get('content') or '').strip()
    if len(text) > 4000:
        text = text[:4000] + '…'
    if text:
        parts.append(text)
limit = int(bindings.get('max_chars') or 60000)
content = '\\n\\n'.join(parts)[:limit]
bindings['prompt'] = ('请阅读以下文档内容，生成中文摘要：先一句话概括主题，'
                      '再分点列出核心要点与关键结论。要求精炼连贯、要点完整；'
                      '直接输出摘要正文，不要附加任何说明、复述或多余文字。\\n\\n'
                      + content)
"""

N4_CODE = """import hashlib, re as _re
s = str(bindings.get('summary_text') or '').strip()
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
bindings['page_title'] = '文档摘要（Stuff 全文直塞）' if s else ''
bindings['page_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10] + '-stuff'
bindings['page_content'] = ('# 文档摘要（Stuff 全文直塞）\\n\\n' + s) if s else ''
"""


def main() -> None:
    s = get_sessionmaker()()
    try:
        steps = {st.id.rsplit("::", 1)[-1]: st
                 for st in s.execute(select(TapeStep).where(TapeStep.tape_id == TAPE)).scalars().all()}
        # n3：保留原配置，追加 system/thinking
        st3 = steps["n3"]
        cfg3 = dict(st3.step_config or {})
        cfg3["system"] = N3_SYSTEM
        cfg3["thinking"] = "disabled"
        st3.step_config = cfg3
        print("step updated: n3 keys:", sorted(cfg3.keys()))
        for sid, code in (("n2", N2_CODE), ("n4", N4_CODE)):
            st = steps[sid]
            cfg = dict(st.step_config or {})
            cfg["code"] = code
            st.step_config = cfg
            print("step updated:", sid)
        # 画布 nodes 同步（tape）
        per_sid = {
            "n2": {"code": N2_CODE},
            "n3": cfg3,
            "n4": {"code": N4_CODE},
        }
        t = s.execute(select(Tape).where(Tape.id == TAPE)).scalars().first()
        if t is not None:
            for n in (t.nodes or []):
                if str(n.get("id")) in per_sid:
                    d = dict(n.get("data") or {})
                    d["config"] = dict(per_sid[str(n.get("id"))])
                    n["data"] = d
            t.nodes = t.nodes
            print("tape nodes synced")
        else:
            print("tape not found, skip nodes sync")
        s.commit()
    finally:
        s.close()


if __name__ == "__main__":
    main()