"""把三个摘要编排的产物从「写 wiki 摘要页」改为「写 kb_document.summary 字段」（幂等可重放）。

背景（2026-10-14，commit 待定）：直连 LLM 摘要链路已移除（be65902），摘要统一
由编排生成；本轮再把编排产物落点从 wiki 摘要页改为 kb_document.summary /
summary_status（直连 LLM 时代摘要的存储位置），配合引擎新增基础原子
kb_write_doc_summary（orchestration_engine.py）。

三个编排（SUMMARY_TAPE_STUFF/MAPREDUCE/REFINE 参数指向的线上 tape）：
  - Stuff   ca5f228cffa44ee09a63e6bacfc674d8   n4 script 落页准备 → n5 wiki_upsert_page
  - MapRed   b5954467d5344db0b87664477bcd1cac   n8 script 落页准备 → n9 wiki_upsert_page
  - Refine   103e45f42fa14abfabb04735f298a50b   n6 script 回写+落页准备 → n7 wiki_upsert_page

改动（三处同步，与既有 apply_*_summary_fixes.py 同模式）：
  1. kb_tape_step：最后 script 节点 code 去掉 page_*/page_slug/page_content/hashlib，
     只留 final_summary（保留 2026-10-13 思考特征词清洗）；最后 wiki_upsert_page
     节点改 step_inst=kb_write_doc_summary + config{document_id, summary_var, skip_empty}
  2. kb_tape.nodes JSON：同上（画布展示同步）
  3. kb_tape_template.nodes JSON：同上（模板同步；结构同构才同步）

注意：用户在服务编排页「保存设计/发布」会覆盖式同步回库冲掉本脚本改动
（画布不显示这些键）——被覆盖后重跑本脚本即可恢复。

用法：docker cp scripts/apply_summary_write_doc_summary.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_summary_write_doc_summary.py
"""
import re
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep, TapeTemplate

# tape_id -> (最后 script 节点 id, 原 wiki_upsert_page 节点 id)
TAPES = {
    "ca5f228cffa44ee09a63e6bacfc674d8": ("n4", "n5"),   # Stuff
    "b5954467d5344db0b87664477bcd1cac": ("n8", "n9"),   # Map-Reduce
    "103e45f42fa14abfabb04735f298a50b": ("n6", "n7"),   # Refine
}

# 清洗逻辑（2026-10-13 思考特征词过滤，与 apply_*_summary_fixes.py 同款）
_META_RE = (
    r"(需要|要求|我们|计数|草稿|数一下|大概|大约|内心|确保|复述|无?需|"
    r"先一[句.]|再分点|只输出|注意|检查|控制|符合|输出.{0,8}(摘要|正文)|"
    r"字数|字符|chars|chinese|we |need|bullets?|theme)"
)
_CLEAN = (
    "import re as _re\n"
    "s = str(bindings.get('{src}') or '').strip()\n"
    "META = _re.compile(r'{meta}', _re.I)\n"
    "lines = []\n"
    "for _ln in s.split('\\n'):\n"
    "    t = _ln.strip().lstrip('。').strip()\n"
    "    if not t:\n"
    "        continue\n"
    "    if META.search(t):\n"
    "        continue\n"
    "    lines.append(t)\n"
    "s = '\\n'.join(lines).strip()\n"
)

# 各编排最后 script 的新 code（src=摘要来源变量；refine 需回写 running_summary）
SCRIPTS = {
    "ca5f228cffa44ee09a63e6bacfc674d8": _CLEAN.format(src="summary_text", meta=_META_RE) + "bindings['final_summary'] = s\n",
    "b5954467d5344db0b87664477bcd1cac": (
        "import re as _re\n"
        "if bindings.get('merge_ready'):\n"
        "    s = str(bindings.get('merged_summary') or '').strip()\n"
        "    META = _re.compile(r'{meta}', _re.I)\n"
        "    lines = []\n"
        "    for _ln in s.split('\\n'):\n"
        "        t = _ln.strip().lstrip('。').strip()\n"
        "        if not t:\n"
        "            continue\n"
        "        if META.search(t):\n"
        "            continue\n"
        "        lines.append(t)\n"
        "    bindings['final_summary'] = '\\n'.join(lines).strip()\n"
        "else:\n"
        "    bindings['final_summary'] = ''\n"
    ).format(meta=_META_RE),
    "103e45f42fa14abfabb04735f298a50b": (
        "import re as _re\n"
        "s = str(bindings.get('new_summary') or '').strip()\n"
        "META = _re.compile(r'{meta}', _re.I)\n"
        "lines = []\n"
        "for _ln in s.split('\\n'):\n"
        "    t = _ln.strip().lstrip('。').strip()\n"
        "    if not t:\n"
        "        continue\n"
        "    if META.search(t):\n"
        "        continue\n"
        "    lines.append(t)\n"
        "s = '\\n'.join(lines).strip()\n"
        "bindings['running_summary'] = s\n"
        "if bindings.get('is_last'):\n"
        "    bindings['final_summary'] = s\n"
        "else:\n"
        "    bindings['final_summary'] = ''\n"
    ).format(meta=_META_RE),
}

WRITER_CONFIG = {
    "document_id": "{{document_id}}",
    "summary_var": "final_summary",
    "skip_empty": True,
}


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
        for tape_id, (script_sid, writer_sid) in TAPES.items():
            per_sid = {
                script_sid: {"code": SCRIPTS[tape_id]},
                writer_sid: WRITER_CONFIG,
            }
            # 1. kb_tape_step
            steps = s.execute(select(TapeStep).where(TapeStep.tape_id == tape_id)).scalars().all()
            changed = 0
            for st in steps:
                sid = st.id.rsplit("::", 1)[-1]
                if sid == script_sid:
                    st.step_config = dict(per_sid[sid])
                    changed += 1
                elif sid == writer_sid:
                    st.step_inst = "kb_write_doc_summary"
                    st.step_label = "写文档摘要"
                    st.step_config = dict(per_sid[sid])
                    changed += 1
            print(f"[{tape_id[:8]}] kb_tape_step updated {changed} nodes")

            # 2. kb_tape.nodes（画布）+ 3. kb_tape_template.nodes（模板）
            tape = s.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
            if tape is not None:
                _apply_to_nodes(tape.nodes, per_sid)
                # wiki_upsert_page 节点 stepInst 也要换
                for n in tape.nodes or []:
                    if str(n.get("id")) == writer_sid:
                        d = dict(n.get("data") or {})
                        d["stepInst"] = "kb_write_doc_summary"
                        d["label"] = "写文档摘要"
                        n["data"] = d
                tape.nodes = tape.nodes
                print(f"[{tape_id[:8]}] tape.nodes synced")
            tpl = s.execute(
                select(TapeTemplate).where(
                    TapeTemplate.name == (tape.tape_name if tape else None)
                )
            ).scalars().first()
            if tpl is not None:
                _apply_to_nodes(tpl.nodes, per_sid)
                for n in tpl.nodes or []:
                    if str(n.get("id")) == writer_sid:
                        d = dict(n.get("data") or {})
                        d["stepInst"] = "kb_write_doc_summary"
                        d["label"] = "写文档摘要"
                        n["data"] = d
                tpl.nodes = tpl.nodes
                print(f"[{tape_id[:8]}] template.nodes synced")
        s.commit()
        print("DONE: 三个摘要编排已改为写 kb_document.summary")
    finally:
        s.close()


if __name__ == "__main__":
    main()
