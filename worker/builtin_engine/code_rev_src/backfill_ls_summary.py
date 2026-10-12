#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长句原文页「汇总描述」回填（2026-10-11 新增，技术规范域）

背景：长句拆分阶段 LLM 对表格驱动的多句知识单元经常不输出 summary → 长句页只有
「标题 + 表格」，门禁 `original_sentence` 报「多句单元缺汇总描述段」（试点 2 篇 201/383）。

本脚本对缺汇总描述的多句单元（表格行数 > 1）批量调 LLM 生成一句话概括（≤80字），
写回页面（保留原文表格逐字、folder/source_refs/page_type/status）。

用法：
  python3 backfill_ls_summary.py --kb <kb_id> --kid <kid>     # 单文档
  python3 backfill_ls_summary.py --kb <kb_id> --all           # 全库
  [--dry-run] [--batch 8] [--max-units 0]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = None
TABLE_HEAD = '| 序号 | 原文 |'
BATCH = 8


def fetch_all_pages(kb):
    pages, page = [], 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 100})
        ps = r.get('pages') or []
        if not ps:
            break
        pages.extend(ps)
        if len(ps) < 100:
            break
        page += 1
    return pages


def split_page(content):
    """返回 (title, summary, rows[str])。rows = 表格数据行原文列表（未转义）。"""
    c = content or ''
    m = re.match(r'#\s*(.+?)\n', c)
    title = m.group(1).strip() if m else ''
    i = c.find(TABLE_HEAD)
    if i < 0:
        return title, '', []
    body = c[len(title) + 3:i] if title else c[:i]
    summary = ''
    for line in body.split('\n'):
        line = line.strip()
        if line and not line.startswith('|') and not line.startswith('#'):
            summary = line
            break
    rows = []
    for line in c[i:].split('\n')[2:]:
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [x.strip() for x in line.strip('|').split('|')]
        if len(cells) >= 2 and cells[1]:
            rows.append(cells[1].replace('\\|', '|'))
    return title, summary, rows


def unit_key(kid, rows):
    import hashlib
    return f"{hashlib.md5(kid.encode()).hexdigest()[:10]}-{hashlib.md5(chr(10).join(rows).encode()).hexdigest()[:8]}"


def refs_have_kid(refs, kid):
    for r in (refs or []):
        if str(r).split('|', 1)[0] == kid:
            return True
    return False


def gen_summaries(units):
    """units: [(idx, text)] → {idx: summary}。批量送 LLM。"""
    out = {}
    for s in range(0, len(units), BATCH):
        chunk = units[s:s + BATCH]
        blocks = []
        for idx, text in chunk:
            t = re.sub(r'\s+', ' ', text)[:800]
            blocks.append(f'[{idx}] {t}')
        prompt = ('你是中国移动技术规范解析专家。下面是从技术规范中切出的若干原文片段（表格行已拼接为文字），'
                  '请为每一段写一句话概括（≤80字，说明这段原文讲的是什么要求/对象/接口/定义，直接陈述，不要"这段"字样）。\n'
                  '只输出 JSON 数组，格式：\n'
                  '[{"i": 1, "s": "概括"}]\n\n片段：\n' + '\n'.join(blocks))
        try:
            raw = wr.llm_call([{'role': 'user', 'content': prompt}], max_tokens=4000, temperature=0.2)
            raw = wr.ensure_full_json(raw) if hasattr(wr, 'ensure_full_json') else raw
            arr = json.loads(raw)
            for item in arr:
                if isinstance(item, dict) and 'i' in item and (item.get('s') or '').strip():
                    out[int(item['i'])] = item['s'].strip()
        except Exception as e:
            print(f'  批量概括失败（{len(chunk)} 段）: {e}')
    return out


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--kid', default=None)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--batch', type=int, default=8)
    ap.add_argument('--max-units', type=int, default=0)
    ns = ap.parse_args()
    global BATCH
    BATCH = ns.batch
    KB = ns.kb or wr.load_kb()
    wr.load_config(); wr.KB = KB; wr.mcp_init()

    pages = [p for p in fetch_all_pages(KB) if p.get('page_type') == 'original_sentence']
    targets = []
    for p in pages:
        if ns.kid and not refs_have_kid(p.get('source_refs'), ns.kid):
            continue
        title, summary, rows = split_page(p.get('content') or '')
        if len(rows) > 1 and not summary:
            targets.append((p, title, rows))
    if ns.max_units:
        targets = targets[:ns.max_units]
    print(f'缺汇总描述的多句单元: {len(targets)} / 长句页 {len(pages)}')
    if not targets or ns.dry_run:
        for p, title, rows in targets[:5]:
            print('  -', p['slug'], title[:40], f'rows={len(rows)}')
        return

    units = [(i, '\n'.join(rows)) for i, (_p, _t, rows) in enumerate(targets)]
    sums = gen_summaries(units)
    print(f'LLM 返回概括: {len(sums)}/{len(targets)}')
    ok = fail = 0
    for i, (p, title, rows) in enumerate(targets):
        s = sums.get(i)
        if not s:
            fail += 1
            continue
        s = re.sub(r'\\\|', '|', s).replace('\n', ' ')
        body = '\n'.join(f"| {n} | {r.replace('|', chr(92) + '|')} |" for n, r in enumerate(rows, 1))
        content = f'# {title}\n\n{s}\n\n{TABLE_HEAD}\n|---|---|\n{body}\n'
        args = {'kb_id': KB, 'slug': p['slug'], 'title': title, 'content': content,
                'folder_id': p.get('folder_id'),
                'folder_ids': p.get('folder_ids') or [p.get('folder_id')],
                'source_refs': p.get('source_refs') or [],
                'status': p.get('status') or 'published', 'page_type': 'original_sentence'}
        r = wr.update_page(**args)
        if r.get('isError'):
            fail += 1
            print('  ❌', p['slug'], str(r)[:120])
        else:
            ok += 1
    print(f'完成：更新 {ok}，失败 {fail}')


if __name__ == '__main__':
    main()
