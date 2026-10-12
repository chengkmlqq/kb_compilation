#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量长句页标题重刷（2026-10-11，技术规范域）

背景：长句拆分 `clean_title` 原先只去引号/书名号，不去句读标点 → 试点 2 篇 201/383 页
title 触发门禁 warning（含标点/超 30 字）。技能侧已修 clean_title；本脚本对**存量**长句页
重刷标题（slug = md5(原文)，与 title 无关 → 改标题不动链接）。

注意：**不动业务对象/关联规则页标题**——实体 slug = md5(类别|canonical(名))，改名会让
后续同名实体合并失配（重复建页），故实体侧只放宽门禁阈值，不改数据。

用法：python3 refresh_titles.py --kb <kb_id> [--dry-run]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = None


def clean_ls_title(t):
    t = re.sub(r'[\n\r"\'《》【】#*“”‘’]', '', t or '').strip()
    t = re.sub(r'\s+', ' ', t)
    t = t.replace('|', '').replace('｜', '').strip().strip('，。；：、！？')
    if len(t) > 30:
        for i in range(30, min(40, len(t)) + 1):
            if t[i - 1] in ' ':
                t = t[:i].rstrip()
                break
        else:
            t = t[:30]
    return t


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
    ns = ap.parse_args()
    KB = ns.kb or wr.load_kb()
    wr.load_config(); wr.KB = KB; wr.mcp_init()

    pages = [p for p in fetch_all_pages(KB) if p.get('page_type') == 'original_sentence']
    todo = []
    for p in pages:
        old = p.get('title') or ''
        new = clean_ls_title(old)
        if new and new != old and len(new) >= 2:
            todo.append((p, old, new))
    print(f'长句页 {len(pages)}，需重刷标题 {len(todo)}')
    for p, old, new in todo[:8]:
        print(f'  {old[:40]}  →  {new[:40]}')
    if ns.dry_run:
        return
    ok = fail = 0
    for p, old, new in todo:
        r = wr.update_page(p['slug'], kb_id=KB, title=new, content=p.get('content'),
                           folder_id=p.get('folder_id'),
                           folder_ids=p.get('folder_ids') or [p.get('folder_id')],
                           source_refs=p.get('source_refs') or [],
                           status=p.get('status') or 'published',
                           page_type='original_sentence')
        if r.get('isError'):
            fail += 1
            print('  ❌', p['slug'], str(r)[:100])
        else:
            ok += 1
    print(f'标题重刷: {ok} 成功 / {fail} 失败')
    print('rebuild_links:', wr.rebuild_links())


if __name__ == '__main__':
    main()
