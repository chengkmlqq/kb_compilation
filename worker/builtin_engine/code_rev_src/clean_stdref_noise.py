#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理「规范性引用文件表行」噪音链（2026-10-11 新增，技术规范域）

背景：技术规范的「规范性引用文件」表行（`| 69 | QB-Y-045.2-2012 | 《…》 | 单位 |`）
在长句拆分阶段被切成 148 个单行知识单元，并进一步抽成 218 张「规范编号与发布机构」
关联规则卡（引用与版本要求）——纯清单噪音，污染关键词/实体/图谱。

本脚本：
  1. 识别噪音长句页（title 以 `|` 开头 或 内容匹配标准编号清单行）
  2. 识别只挂噪音长句的关联规则卡（其 longsentence 链接 ⊆ 噪音集）
  3. 扫描其它页面，移除指向上述两类页面的 wikilink（整行只有这些链接 → 删行；否则剥 token）
  4. 删除噪音页（先清引用再删页）
  5. rebuild_links

用法：
  python3 clean_stdref_noise.py --kb <kb_id> [--dry-run]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = None
# 注意：list_wiki_pages 返回的 content 可能被截断，且部分链接因显示名含 `|` 而畸形
# （如 `[[longsentence/x|| [72] | QB-Y-045.4-]]`）→ 检测用宽松正则（只锚 slug 前缀），
# 不做 `]]` 收尾要求（2026-10-11 实测：严格正则会漏掉 207/218 张噪音要求卡）
LS_RE = re.compile(r'\[\[(longsentence/[^\]|]+)')
ANY_RE = re.compile(r'\[\[([^\]|]+)')
STDREF_RE = re.compile(r'\|\s*\[?\d{1,4}\]?\s*\|\s*[A-Z]{1,4}[-\w.]*\d{2,6}')


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


def is_noise_ls(p):
    if p.get('page_type') != 'original_sentence':
        return False
    t = (p.get('title') or '').strip()
    c = p.get('content') or ''
    return t.startswith('|') or bool(STDREF_RE.search(c))


def strip_links(content, del_set):
    """移除指向 del_set 的 wikilink：整行只含这些链接 → 删行；否则剥 token。"""
    out, removed = [], 0
    for line in (content or '').split('\n'):
        links = ANY_RE.findall(line)
        if links and all(x in del_set for x in links):
            removed += len(links)
            continue
        new = line
        for s in list(del_set):
            if s in new:
                # 兼容畸形链接：[[slug|| 显示名含竖线]] / 截断未收尾
                new2 = re.sub(r'\[\[\s*' + re.escape(s) + r'[^\]]*\]?\]?', '', new)
                if new2 != new:
                    removed += 1
                    new = new2
        if new != line:
            new = re.sub(r'[、,，]\s*([、，]|$)', lambda m: m.group(1), new)
            new = re.sub(r'\|\s*\|', '| |', new)
        out.append(new)
    return '\n'.join(out), removed


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ns = ap.parse_args()
    KB = ns.kb or wr.load_kb()
    wr.load_config(); wr.KB = KB; wr.mcp_init()

    pages = fetch_all_pages(KB)
    print(f'页面总数: {len(pages)}')
    noise_ls = {p['slug'] for p in pages if is_noise_ls(p)}
    print(f'噪音长句页: {len(noise_ls)}')

    del_rules = set()
    for p in pages:
        if p.get('page_type') != 'rule_ontology':
            continue
        links = set(LS_RE.findall(p.get('content') or ''))
        if links and links <= noise_ls:
            del_rules.add(p['slug'])
    print(f'噪音关联规则卡（仅挂噪音长句）: {len(del_rules)}')

    del_set = noise_ls | del_rules
    affected = []
    for p in pages:
        if p['slug'] in del_set:
            continue
        c = p.get('content') or ''
        hit = [s for s in del_set if s in c]
        if hit:
            affected.append((p, len(hit)))
    print(f'需清理引用的页面: {len(affected)}')
    total_removed = 0
    for p, n in affected:
        newc, removed = strip_links(p.get('content') or '', del_set)
        total_removed += removed
        if ns.dry_run:
            continue
        r = wr.update_page(p['slug'], kb_id=KB, title=p.get('title'), content=newc,
                           folder_id=p.get('folder_id'),
                           folder_ids=p.get('folder_ids') or [p.get('folder_id')],
                           source_refs=p.get('source_refs') or [],
                           status=p.get('status') or 'published',
                           page_type=p.get('page_type'))
        if r.get('isError'):
            print('  ❌', p['slug'], str(r)[:100])
    print(f'清理链接: {total_removed} 处（{"dry-run" if ns.dry_run else "已写回"}）')

    if not ns.dry_run:
        ok = fail = 0
        for slug in sorted(del_set):
            r = wr.delete_page(slug, kb_id=KB)
            if r.get('isError'):
                fail += 1
                print('  ❌ 删除失败', slug, str(r)[:100])
            else:
                ok += 1
        print(f'删除页面: {ok} 成功 / {fail} 失败')
        print('rebuild_links:', wr.rebuild_links())


if __name__ == '__main__':
    main()
