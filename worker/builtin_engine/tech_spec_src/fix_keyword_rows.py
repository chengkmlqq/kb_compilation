#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关键词页行修复（2026-10-11）

场景：清理噪音长句页时移除了部分 wikilink → 关键词页残留空表格/序号断号
（门禁报「表格无数据行」「序号不连续」）。

处理：按 keyword_registry 的口径重新解析分组 → 丢弃无链接的空组 → 重新渲染
（自动重排序号、补「暂无关联长句」），保留原释义行。

用法：python3 fix_keyword_rows.py --kb <kb_id> [--dry-run] [--purge-empty]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keyword_registry as kr
import weknora_rpc as wr

KB = None


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


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--purge-empty', action='store_true', help='所有分组都无链接时删除该页')
    ns = ap.parse_args()
    KB = ns.kb or wr.load_kb()
    wr.load_config(); wr.KB = KB; wr.mcp_init()

    pages = [p for p in fetch_all_pages(KB) if p.get('page_type') == 'frequent_keyword']
    fixed = empty = 0
    for p in pages:
        c = p.get('content') or ''
        groups = kr.parse_groups_v2(c)
        if not groups:
            continue
        links_total = sum(len(v[1]) for v in groups.values())
        nonempty = {k: v for k, v in groups.items() if v[1]}
        # 判断是否「行数/序号」异常：按渲染口径重算后与原文比对
        meaning = ''
        m = re.search(r'\*\*释义\*\*[：:]\s*(.+)', c)
        if m:
            meaning = m.group(1).strip()
        if not nonempty:
            if not ns.dry_run:
                wr.delete_page(p['slug'], kb_id=KB)
                empty += 1
                continue
            wr.delete_page(p['slug'], kb_id=KB)
            empty += 1
            continue
        else:
            new = kr.render_page_v2(p.get('title'), nonempty, len(nonempty), bookname_fn=wr.bookname, meaning=meaning)
        if new.strip() == c.strip():
            continue
        # 只在行数/序号真异常时写回：检查原文是否有空表或断号
        bad = bool(re.search(r'\|\s*---\s*\|\s*---\s*\|\s*\n\s*\n', c)) or bool(re.search(r'本关键词', c)) and links_total == 0
        seq_bad = False
        nums = [int(x) for x in re.findall(r'^\|\s*(\d+)\s*\|', c, re.M)]
        if nums and nums != list(range(1, len(nums) + 1)):
            seq_bad = True
        if not (bad or seq_bad):
            continue
        if ns.dry_run:
            print('  [dry]', p['slug'], p.get('title'), f'groups={len(groups)}→{len(nonempty)} links={links_total}')
            fixed += 1
            continue
        r = wr.update_page(p['slug'], kb_id=KB, title=p.get('title'), content=new,
                           folder_id=p.get('folder_id'),
                           folder_ids=p.get('folder_ids') or [p.get('folder_id')],
                           source_refs=p.get('source_refs') or [],
                           status=p.get('status') or 'published', page_type='frequent_keyword')
        if r.get('isError'):
            print('  ❌', p['slug'], str(r)[:100])
        else:
            fixed += 1
    print(f'关键词页修复: {fixed} 页；全空删除: {empty}')
    if not ns.dry_run:
        print('rebuild_links:', wr.rebuild_links())


if __name__ == '__main__':
    main()
