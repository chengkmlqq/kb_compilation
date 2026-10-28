#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量高频词页释义回填（2026-10-28）

背景：2026-10-28 前市场域构建链路（filter_terms_llm 无 meaning 字段、模板不含释义行）
生成的高频词页没有 `**释义**` 行 → 前端「含义」列恒空。生成链路已补齐 meaning，
本脚本对**存量**页补释义：无释义行的页面 → LLM 批量生成 meaning → 在开头句前
插入 `**释义**：xxx` 行（页面其余结构不动）。

幂等：
- 已有释义行的页面跳过
- LLM 未给 meaning 的词跳过（不写空释义）
- 重复运行只处理仍缺释义的页面

用法（api-server / worker 容器内直连模式跑，PYTHONPATH=/srv/kb）：
    python3 worker/builtin_engine/scripts/backfill_keyword_meanings.py --kb <kb_id> [--dry-run] [--batch-size 15]
    # 全部有高频词页的 KB：
    python3 worker/builtin_engine/scripts/backfill_keyword_meanings.py [--dry-run]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

# 释义行正则（与 keyword_registry / kb_admin._MEANING_RE 同口径）
_MEANING_LINE_RE = re.compile(r"\*\*释义\*\*[：:]")

MEANING_PROMPT = """你是市场监管法规知识库的术语释义专家。以下高频词已确认具有业务含义（制度术语/监管对象/业务概念），请为每个词生成**一句话业务释义**。

【要求】
- meaning：一句话，≤30 字，说明该词在制度中的业务含义（如"行政处罚：对违反行政法律法规的相对人给予的制裁行为"）
- 结合附上的原文片段判断语境；无法给出确切含义的词 meaning 留空字符串
- 只输出JSON数组，格式：[{{"word":"词","meaning":"一句话业务释义（≤30字）"}}]，不要输出任何其他文字

候选词及上下文：
{items}
"""


def fetch_frequent_pages(kb):
    """分页拉取某 KB 的全部 frequent_keyword 页面（content 已补齐）。"""
    pages, page = [], 1
    while True:
        r = wr.list_wiki_pages(kb_id=kb, page=page, page_size=200)
        ps = r.get('pages') or []
        if not ps:
            break
        pages.extend(ps)
        if len(ps) < 200:
            break
        page += 1
    return [p for p in pages if p.get('page_type') == 'frequent_keyword']


def parse_snippets(content):
    """从页面 content 解析上下文片段（分组表格里的 [[slug|片段]]），最多 3 条。"""
    snips = []
    for m in re.finditer(r"\[\[[^\]|]+\|([^\]]*)\]\]", content or ""):
        s = m.group(1).strip()
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)  # 去加粗
        if s and s not in ('暂无关联长句',):
            snips.append(s[:60])
        if len(snips) >= 3:
            break
    return snips


def insert_meaning(content, meaning):
    """在开头句前插入释义行；无开头句则在标题行后。页面其余结构不动。"""
    if not meaning:
        return content
    marker = "本关键词在"
    idx = (content or "").find(marker)
    if idx > 0:
        return content[:idx] + f"**释义**：{meaning}\n\n" + content[idx:]
    # 无开头句的旧版页面：插在首个 # 标题行后
    lines = (content or "").split("\n")
    for i, ln in enumerate(lines):
        if ln.strip().startswith("# "):
            return "\n".join(lines[: i + 1] + ["", f"**释义**：{meaning}", ""] + lines[i + 1:])
    return f"# {meaning}\n" if not content else (content + f"\n**释义**：{meaning}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=None, help="知识库 ID；缺省扫描全部有高频词页的 KB")
    ap.add_argument("--batch-size", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不写库")
    ns = ap.parse_args()

    wr.load_config()
    wr.mcp_init()

    kbs = [ns.kb] if ns.kb else []
    if not kbs:
        # 直连枚举有高频词页的 KB（list_knowledge_bases tool_call 会撞 _kb() 前置检查）
        from api.db import get_sessionmaker
        from api.models.knowledge import WikiPage
        from sqlalchemy import select
        _db = get_sessionmaker()()
        try:
            rows = _db.execute(
                select(WikiPage.kb_id)
                .where(WikiPage.page_type == "frequent_keyword", WikiPage.status == "active")
                .distinct()
            ).scalars().all()
            kbs = list(rows)
        finally:
            _db.close()
    print(f"待扫描 KB: {len(kbs)} 个")

    total_scanned = total_filled = total_skipped = 0
    for kb in kbs:
        wr.KB = kb
        try:
            pages = fetch_frequent_pages(kb)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ {kb} 拉取失败: {e}")
            continue
        todo = []
        for p in pages:
            c = p.get('content') or ''
            if _MEANING_LINE_RE.search(c):
                total_skipped += 1
                continue
            todo.append(p)
        if not todo:
            print(f"[{kb}] 共 {len(pages)} 页，缺释义 {len(todo)} 页 → 跳过")
            continue
        print(f"[{kb}] 共 {len(pages)} 页，缺释义 {len(todo)} 页")
        total_scanned += len(todo)

        # 分批 LLM 生成 meaning
        for i in range(0, len(todo), ns.batch_size):
            batch = todo[i:i + ns.batch_size]
            items = []
            for p in batch:
                w = (p.get('title') or '').strip() or (p.get('slug') or '').strip()
                snips = parse_snippets(p.get('content'))
                ctx = '；'.join(snips) if snips else '（无上下文）'
                items.append(f"{len(items)+1}. 词：{w} | 原文片段：{ctx[:150]}")
            try:
                result = wr.llm_call(
                    [{'role': 'system', 'content': '你是术语释义专家，只输出JSON。'},
                     {'role': 'user', 'content': MEANING_PROMPT.format(items='\n'.join(items))}],
                    temperature=0.0)
                result = wr.ensure_full_json(result)
                parsed = json.loads(result)
                meanings = {}
                def _collect(x):
                    if isinstance(x, dict):
                        w = (x.get('word') or '').strip()
                        if w:
                            meanings[w] = (x.get('meaning') or '').strip()
                    elif isinstance(x, list):
                        for y in x:
                            _collect(y)
                _collect(parsed)
            except Exception as e:  # noqa: BLE001
                print(f"    ⚠️ 批 {i//ns.batch_size+1} LLM 失败（{e}），跳过该批")
                continue
            for p in batch:
                w = (p.get('title') or '').strip() or (p.get('slug') or '').strip()
                meaning = meanings.get(w, '')
                if not meaning:
                    continue
                new_content = insert_meaning(p.get('content') or '', meaning)
                if new_content == p.get('content'):
                    continue
                if ns.dry_run:
                    print(f"    [dry] {p.get('slug')} {w} → {meaning}")
                    total_filled += 1
                    continue
                r = wr.update_page(
                    p['slug'], kb_id=kb, content=new_content,
                    title=p.get('title'),
                    folder_id=p.get('folder_id') or '',
                    folder_ids=p.get('folder_ids') or [p.get('folder_id') or ''],
                    source_refs=p.get('source_refs') or [],
                    status=p.get('status') or 'active',
                    page_type='frequent_keyword')
                if r.get('isError'):
                    print(f"    ❌ {p.get('slug')} {str(r)[:120]}")
                else:
                    total_filled += 1
                    print(f"    ✅ {p.get('slug')} {w} → {meaning}")
    print(f"\n回填完成: 处理缺释义页 {total_scanned}，写入/计划 {total_filled}，跳过(已有释义) {total_skipped}")
    if not ns.dry_run and total_filled:
        print("rebuild_links:", wr.rebuild_links())


if __name__ == '__main__':
    main()