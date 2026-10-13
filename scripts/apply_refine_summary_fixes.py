"""应用「摘要-Refine（增量修订）」编排的质量/可靠性修复（幂等可重放）。

2026-10-13 实测：编排 LLM 输出思考草稿（inferai deepseek-v4-pro 默认 thinking
开启）+ 多轮循环撞限流（429 无重试）。修复分两层：
  1. 引擎代码（api/services/orchestration_engine.py KbLlmChatStep）：
     system 支持 / thinking 透传 / 429·5xx 退避重试 —— 已随代码提交。
  2. 编排数据（本脚本）：n2 batch_size 5、n4 prompt 结构化、n5 system +
     thinking=disabled、n6 逐行过滤思考特征词。

注意：用户在服务编排页「保存设计/发布」会覆盖式同步回库，冲掉本脚本的
库内修复（画布不显示这些键）。被覆盖后重跑本脚本即可恢复。

用法：docker cp scripts/apply_refine_summary_fixes.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_refine_summary_fixes.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep, TapeTemplate

TAPE = "103e45f42fa14abfabb04735f298a50b"
TPL = "0104d61d94464d93a05f65c3138a5880"

N5_SYSTEM = (
    "你是资深中文文档摘要专家。直接输出最终摘要正文，"
    "禁止输出任何思考过程、分析草稿、字数统计、修改计划、对指令的复述或任何解释性文字。"
)

N2_CODE = """chunks = bindings.get('chunks') or []
batch_size = int(bindings.get('batch_size') or 5)
cap = int(bindings.get('chunk_cap') or 3000)
batches = []
for i in range(0, len(chunks), batch_size):
    texts = []
    for c in chunks[i:i + batch_size]:
        t = (c.get('content') or '').strip()
        if len(t) > cap:
            t = t[:cap] + '…'
        if t:
            texts.append(t)
    if texts:
        batches.append({'idx': len(batches), 'text': '\\n\\n'.join(texts)})
bindings['batches'] = batches
bindings['_total'] = len(batches)
# 2026-10-13 增量语义：已有历史摘要（上传链注入 prev_summary）作为滚动摘要起点，
# 后续每批在其基础上增量融合；无历史摘要则从空开始（首轮直接生成）。
bindings['running_summary'] = str(bindings.get('prev_summary') or '').strip()
"""

N4_CODE = """batch = bindings.get('batch') or {}
idx = int(batch.get('idx') or 0)
total = int(bindings.get('_total') or 1)
bindings['is_last'] = (idx >= total - 1)
prev = str(bindings.get('running_summary') or '').strip()
cur = str(batch.get('text') or '')
if prev:
    bindings['refine_prompt'] = (
        '【已有摘要（必须完整保留其全部要点，不得遗漏、不得压缩为更短，'
        '逐点核对后全部保留）】\\n' + prev +
        '\\n\\n【新内容（第 %d/%d 片段）】\\n' % (idx + 1, total) + cur +
        '\\n\\n请基于「已有摘要 + 新内容」输出更新后的完整文档摘要。\\n'
        '要求：\\n'
        '1) 已有摘要的每一个要点都必须保留在结果中（只允许合并重复表述，'
        '不允许删减或过度压缩）；\\n'
        '2) 完整融入新内容的新要点，结果要点数 >= 已有摘要要点数；\\n'
        '3) 输出精炼连贯的中文摘要：先一句话概括主题，再分点列出核心要点；\\n'
        '4) 直接输出摘要正文，不要附加任何说明、复述或多余文字。')
else:
    bindings['refine_prompt'] = (
        '根据以下文档内容生成中文摘要。\\n'
        '要求：\\n'
        '1) 输出精炼连贯的中文摘要：先一句话概括主题，再分点列出核心要点；\\n'
        '2) 直接输出摘要正文，不要附加任何说明、复述或多余文字。\\n\\n'
        + cur)
"""

N6_CODE = """import hashlib, re as _re
s = str(bindings.get('new_summary') or '').strip()
# 2026-10-13 清洗：deepseek-v4-pro 在摘要任务上会输出思考/确认过程（复述指令、
# 字数统计、草稿、自我检查等）。逐行过滤含思考特征词的行，仅保留正文行。
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
bindings['running_summary'] = s
if bindings.get('is_last') and s:
    doc_id = str(bindings.get('document_id') or '')
    bindings['final_summary'] = s
    bindings['page_title'] = '文档摘要（Refine 增量修订）'
    bindings['page_slug'] = 'doc-' + hashlib.md5(doc_id.encode()).hexdigest()[:10] + '-refine'
    bindings['page_content'] = '# 文档摘要（Refine 增量修订）\\n\\n' + s
else:
    bindings['final_summary'] = s
    bindings['page_title'] = ''
    bindings['page_slug'] = ''
    bindings['page_content'] = ''
"""


def _apply_to_nodes(nodes: list | None, per_sid: dict) -> None:
    for n in nodes or []:
        sid = str(n.get("id"))
        if sid in per_sid:
            d = dict(n.get("data") or {})
            d["config"] = dict(per_sid[sid])
            n["data"] = d


def main() -> None:
    s = get_sessionmaker()()
    try:
        per_sid = {
            "n2": {"code": N2_CODE},
            "n4": {"code": N4_CODE},
            "n5": {"prompt": "{{refine_prompt}}", "max_tokens": "1024",
                   "output_var": "new_summary", "temperature": "0.3",
                   "system": N5_SYSTEM, "thinking": "disabled"},
            "n6": {"code": N6_CODE},
        }
        steps = s.execute(select(TapeStep).where(TapeStep.tape_id == TAPE)).scalars().all()
        for st in steps:
            sid = st.id.rsplit("::", 1)[-1]
            if sid in per_sid:
                st.step_config = dict(per_sid[sid])
                print("step updated:", st.id, "keys:", sorted(per_sid[sid].keys()))
        # tape 画布 + 模板画布同步
        for obj_id, label in ((TAPE, "tape"), (TPL, "template")):
            t = s.execute(select(Tape).where(Tape.id == obj_id)).scalars().first() if label == "tape" else None
            tpl = s.execute(select(TapeTemplate).where(TapeTemplate.id == obj_id)).scalars().first() if label == "template" else None
            obj = t if t else tpl
            if obj is None:
                print("skip", label, "not found")
                continue
            _apply_to_nodes(obj.nodes, per_sid)
            obj.nodes = obj.nodes
            print(label, "nodes synced")
        s.commit()
    finally:
        s.close()


if __name__ == "__main__":
    main()