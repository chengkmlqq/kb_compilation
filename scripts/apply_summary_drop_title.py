"""三个摘要编排去标题化（幂等可重放）：LLM 输出常以 '# 《文档名》文档摘要' 开头
（整理 script 的 META 特征词正则漏过该标题行），最终写进 kb_document.summary。
2026-10-14 实测：kb_document.summary 内容为 `# 《湖北政企短彩业务本体 v1.0》文档摘要\n本文档…`。

三处同步（与既有 apply_*_summary*.py 同模式）：
1. kb_tape_step
2. kb_tape.nodes（画布展示）
3. kb_tape_template.nodes（模板）

改动：
A. 三个编排的「整理/落页准备/回写」script：META 正则追加标题行分支
   `^#{1,6}\s`（markdown 标题行）与 `《文档名》文档摘要`（书名号摘要标题行）
   → `# 《湖北政企短彩业务本体 v1.0》文档摘要` 这类行被剔除，只留摘要正文。
B. 三个编排的拼 prompt script：prompt 追加「不要输出任何标题」指令（双保险）。
C. Refine：n7 wiki_upsert_page → kb_write_doc_summary（补 f813cea 未生效部分），
   n6 去掉 page_title/page_slug/page_content/hashlib 拼装（对齐种子新形态）。

线上三个 tape（tape_id 与 apply_summary_write_doc_summary.py 的 TAPES 一致）：
- Stuff     ca5f228cffa44ee09a63e6bacfc674d8   n2 拼全文Prompt / n4 落页准备 / n5 写文档摘要
- MapReduce b5954467d5344db0b87664477bcd1cac  n6 累积+拼合并Prompt / n8 落页准备 / n9 写文档摘要
- Refine    103e45f42fa14abfabb04735f298a50b   n4 拼增量Prompt / n6 回写+落页准备 / n7 写摘要页

用法：docker cp scripts/apply_summary_drop_title.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_summary_drop_title.py
"""
from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import Tape, TapeStep, TapeTemplate

STUFF_ID = "ca5f228cffa44ee09a63e6bacfc674d8"
MR_ID = "b5954467d5344db0b87664477bcd1cac"
REFINE_ID = "103e45f42fa14abfabb04735f298a50b"

# META 正则标题行分支：`bullets?|theme)', _re.I)` → 追加 `^#{1,6}\s|《…》文档摘要`
_META_OLD = r"bullets?|theme)', _re.I)"
_META_NEW = r"bullets?|theme|^#{1,6}\s|《[^》]{1,80}》\s*文档摘要)', _re.I)"

WRITER_CONFIG = {
    "document_id": "{{document_id}}",
    "summary_var": "final_summary",
    "skip_empty": True,
}

# Refine n6 新 code：清洗 new_summary → running_summary / final_summary，不再拼 page_*
# （对齐 seeds 的 SUMMARY_REFINE_TAPE n6；META 已含标题行分支）
_REFINE_N6_CLEAN = (
    "import re as _re\n"
    "s = str(bindings.get('new_summary') or '').strip()\n"
    "META = _re.compile(r'(需要|要求|我们|计数|草稿|数一下|大概|大约|内心|确保|复述|无?需|"
    "先一[句.]|再分点|只输出|注意|检查|控制|符合|输出.{0,8}(摘要|正文)|"
    "字数|字符|chars|chinese|we |need|bullets?|theme|^#{1,6}\\s|《[^》]{1,80}》\\s*文档摘要)', _re.I)\n"
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
)


def _patch_meta(code):
    """整理 script 的 META 正则追加标题行分支（已含则不动）。"""
    if _META_OLD in code and "#{1,6}" not in code:
        return code.replace(_META_OLD, _META_NEW), True
    return code, False


def _append_stuff_no_title(code):
    """Stuff n2 拼全文Prompt 末尾补标题禁令（已含则不动）。

    线上 code 形态：...'3) 精炼连贯，直接输出摘要正文，不要附加任何说明、复述或多余文字。\\n\\n'
    """
    if "不要输出任何标题" in code:
        return code, False
    anchor = "3) 精炼连贯，直接输出摘要正文，不要附加任何说明、复述或多余文字。"
    if anchor in code:
        repl = (
            "3) 精炼连贯，直接输出摘要正文，不要附加任何说明、复述或多余文字；\\n"
            "4) 不要输出任何标题（如『# 文档摘要』或『《文档名》文档摘要』等），直接输出摘要正文。"
        )
        return code.replace(anchor, repl, 1), True
    return code, False


def _append_mr_no_title(code):
    """Map-Reduce n6 合并 prompt 补标题禁令（已含则不动）。"""
    if "不要输出任何标题" in code:
        return code, False
    anchor = "3) 直接输出摘要正文，不要附加任何说明、复述或多余文字。"
    if anchor in code:
        repl = (
            "3) 不要输出任何标题（如『# 文档摘要』或『《文档名》文档摘要』等），"
            "直接输出摘要正文，不要附加任何说明、复述或多余文字。"
        )
        return code.replace(anchor, repl, 1), True
    return code, False


def _append_refine_no_title(code):
    """Refine n4 两分支 refine_prompt 补标题禁令（已含则不动）。"""
    if "不要输出任何标题" in code:
        return code, False
    changed = False
    a1 = "保留已有要点并融入新要点，只输出摘要正文"
    if a1 in code:
        code = code.replace(
            a1,
            "保留已有要点并融入新要点，不要输出任何标题（如『# 文档摘要』等），只输出摘要正文",
            1,
        )
        changed = True
    a2 = "再分点列出核心要点，只输出摘要正文"
    if a2 in code:
        code = code.replace(
            a2,
            "再分点列出核心要点，不要输出任何标题（如『# 文档摘要』等），只输出摘要正文",
            1,
        )
        changed = True
    return code, changed


def _is_refine_old_writer(cfg):
    cfg = cfg or {}
    return bool(cfg.get("content")) and ("page_content" in str(cfg.get("content", "")))


def _apply_to_nodes(nodes, fn_map):
    """tape.nodes / template.nodes 逐节点应用补丁函数（fn_map: sid -> fn(code)->(code,hit)）。"""
    changed = 0
    for n in nodes or []:
        sid = str(n.get("id"))
        if sid not in fn_map:
            continue
        d = dict(n.get("data") or {})
        cfg = dict(d.get("config") or {})
        code = cfg.get("code")
        if code is not None:
            new_code, hit = fn_map[sid](code)
            if hit:
                cfg["code"] = new_code
                d["config"] = cfg
                n["data"] = d
                changed += 1
    return changed


def main():
    s = get_sessionmaker()()
    try:
        # ── 1. kb_tape_step ──────────────────────────────────────
        for tape_id in (STUFF_ID, MR_ID, REFINE_ID):
            steps = s.execute(
                select(TapeStep).where(TapeStep.tape_id == tape_id)
            ).scalars().all()
            for st in steps:
                sid = st.id.rsplit("::", 1)[-1]
                cfg = dict(st.step_config or {})
                code = cfg.get("code") or ""
                is_script = st.step_inst == "script"
                if not is_script:
                    continue
                # 整理 script（Stuff n4 / MapReduce n8 / Refine n6）→ META 标题过滤
                if sid in ("n4", "n8", "n6") and "META = _re.compile" in code:
                    new_code, hit = _patch_meta(code)
                    if hit:
                        cfg["code"] = new_code
                        st.step_config = cfg
                        print(f"[{tape_id[:8]}::{sid}] step META +标题过滤")
                # Stuff n2 拼全文Prompt
                if tape_id == STUFF_ID and sid == "n2":
                    new_code, hit = _append_stuff_no_title(code)
                    if hit:
                        cfg["code"] = new_code
                        st.step_config = cfg
                        print(f"[{tape_id[:8]}::n2] prompt +标题禁令")
                # MapReduce n6 累积+拼合并Prompt
                if tape_id == MR_ID and sid == "n6":
                    new_code, hit = _append_mr_no_title(code)
                    if hit:
                        cfg["code"] = new_code
                        st.step_config = cfg
                        print(f"[{tape_id[:8]}::n6] prompt +标题禁令")
                # Refine n4 拼增量Prompt
                if tape_id == REFINE_ID and sid == "n4":
                    new_code, hit = _append_refine_no_title(code)
                    if hit:
                        cfg["code"] = new_code
                        st.step_config = cfg
                        print(f"[{tape_id[:8]}::n4] prompt +标题禁令")
                # Refine n6 旧形态（拼 page_title）→ 新 code
                if tape_id == REFINE_ID and sid == "n6" and "page_title" in code:
                    cfg["code"] = _REFINE_N6_CLEAN
                    st.step_config = cfg
                    print(f"[{tape_id[:8]}::n6] 去 page_*（升级新形态）")
            # Refine n7 旧 wiki_upsert_page → kb_write_doc_summary
            if tape_id == REFINE_ID:
                for st in steps:
                    sid = st.id.rsplit("::", 1)[-1]
                    if sid == "n7" and _is_refine_old_writer(st.step_config):
                        st.step_inst = "kb_write_doc_summary"
                        st.step_label = "写文档摘要"
                        st.step_config = dict(WRITER_CONFIG)
                        print(f"[{tape_id[:8]}::n7] wiki_upsert_page → kb_write_doc_summary")

        # ── 2+3. kb_tape.nodes + kb_tape_template.nodes ──────────
        fn_stuff = {"n2": _append_stuff_no_title, "n4": _patch_meta}
        fn_mr = {"n6": _append_mr_no_title, "n8": _patch_meta}

        def _fn_refine_n6(code):
            if "page_title" in code:
                return _REFINE_N6_CLEAN, True
            return _patch_meta(code)

        fn_refine = {"n4": _append_refine_no_title, "n6": _fn_refine_n6}
        fn_by_tape = {STUFF_ID: fn_stuff, MR_ID: fn_mr, REFINE_ID: fn_refine}

        for tape_id, fn_map in fn_by_tape.items():
            tape = s.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
            if tape is not None and tape.nodes:
                c = _apply_to_nodes(tape.nodes, fn_map)
                # Refine n7 writer 节点也要换（画布）
                if tape_id == REFINE_ID:
                    for n in tape.nodes or []:
                        if str(n.get("id")) == "n7":
                            d = dict(n.get("data") or {})
                            if _is_refine_old_writer(d.get("config")):
                                d["stepInst"] = "kb_write_doc_summary"
                                d["label"] = "写文档摘要"
                                d["config"] = dict(WRITER_CONFIG)
                                n["data"] = d
                                c += 1
                if c:
                    print(f"[{tape_id[:8]}] tape.nodes synced ({c})")
            tpl = s.execute(
                select(TapeTemplate).where(
                    TapeTemplate.name == (tape.tape_name if tape else None)
                )
            ).scalars().first()
            if tpl is not None and tpl.nodes:
                c = _apply_to_nodes(tpl.nodes, fn_map)
                if tape_id == REFINE_ID:
                    for n in tpl.nodes or []:
                        if str(n.get("id")) == "n7":
                            d = dict(n.get("data") or {})
                            if _is_refine_old_writer(d.get("config")):
                                d["stepInst"] = "kb_write_doc_summary"
                                d["label"] = "写文档摘要"
                                d["config"] = dict(WRITER_CONFIG)
                                n["data"] = d
                                c += 1
                if c:
                    print(f"[{tape_id[:8]}] template.nodes synced ({c})")
        s.commit()
        print("DONE: 三个摘要编排已去标题化（整理 script 滤标题行 + prompt 标题禁令）")
    finally:
        s.close()


if __name__ == "__main__":
    main()