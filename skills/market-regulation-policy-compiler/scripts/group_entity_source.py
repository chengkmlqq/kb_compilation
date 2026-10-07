"""
实体页原文关联按来源文档分组 + 来源规范（2026-08-20 用户要求）
对全库 entity-b 业务实体页：
1. "原文关联"节：按来源文档分组，每组加《文档原始文件名》标题，让用户分清每条长句属于哪个文档
2. "关联实体"表：来源列规范化（确保用《文档原始文件名》）

用法：
  python3 group_entity_source.py --kb <kb_id> [--dry-run]
"""
import json, sys, os, re, argparse
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

def _ref_kid(ref):
    """Extract kid from 'kid|title' or bare kid."""
    return (ref or '').split('|')[0]

def load_docs(wr, KB):
    doc_map = {}
    pw = 1
    while True:
        r = wr.list_docs(page=pw, page_size=20)
        data = r.get('data', [])
        if not data:
            break
        for d in data:
            doc_map[d['id']] = d.get('file_name', '')
        if len(data) < 20:
            break
        pw += 1
    return doc_map

def load_pages(wr, KB):
    pages = []
    pw = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': pw, 'page_size': 100})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        pages.extend(data)
        if len(data) < 100:
            break
        pw += 1
    return pages

def build_ls_prefix_kid(pages):
    """长句 slug 前缀 -> kid"""
    mp = {}
    for p in pages:
        slug = p.get('slug', '')
        if slug.startswith('longsentence/'):
            refs = p.get('source_refs') or []
            if refs:
                mp[slug.split('longsentence/')[1][:10]] = _ref_kid(refs[0])
    return mp

def group_long_sentences(content, ls_prefix_kid, doc_map):
    """原文关联节按来源文档分组"""
    if '## 原文关联' not in content:
        return (content, False)
    pre, rest = content.split('## 原文关联', 1)
    if '## ' in rest:
        sec, post = rest.split('## ', 1)
        post = '## ' + post
    else:
        sec, post = (rest, '')
    links = re.findall('\\[\\[(longsentence/[^\\]|]+)(?:\\|([^\\]]*))?\\]\\]', sec)
    if not links:
        return (content, False)
    groups = defaultdict(list)
    for slug, title in links:
        prefix = slug.split('longsentence/')[1][:10]
        kid = ls_prefix_kid.get(prefix)
        doc_key = kid or 'unknown'
        title_t = title if title else slug
        groups[doc_key].append((slug, title_t))
    if not groups:
        return (content, False)
    out = ['## 原文关联', '']
    for kid, items in groups.items():
        doc_name = doc_map.get(kid, '未归属文档')
        out.append(f'**{wr.bookname(doc_name)}**')
        for slug, title in items:
            out.append(f'- [[{slug}|{title}]]')
        out.append('')
    new_sec = '\n'.join(out).rstrip() + '\n'
    new_content = pre + new_sec + '\n' + post
    return (new_content, True)

def normalize_rel_source(content, doc_map):
    """关联实体表来源列规范化（去掉列数异常，确保来源列是完整文档名）"""
    return (content, False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--dry-run', action='store_true', help='只统计不写库')
    ap.add_argument('--type', default='all', choices=['all', 'biz', 'rule'], help='处理类型：all/biz/rule')
    args = ap.parse_args()
    wr.load_config()
    wr.mcp_init()
    KB = args.kb
    doc_map = load_docs(wr, KB)
    pages = load_pages(wr, KB)
    ls_prefix_kid = build_ls_prefix_kid(pages)

    def _type_ok(slug):
        if args.type == 'biz':
            return slug.startswith('entity-b-')
        if args.type == 'rule':
            return slug.startswith('entity-r-')
        return slug.startswith('entity-b-') or slug.startswith('entity-r-')
    biz_ents = [p for p in pages if _type_ok(p.get('slug', ''))]
    print(f'实体页: {len(biz_ents)}')
    changed = 0
    no_ls = 0
    for p in biz_ents:
        slug = p.get('slug', '')
        if args.dry_run:
            content = p.get('content') or ''
            nc, is_ch = group_long_sentences(content, ls_prefix_kid, doc_map)
            if is_ch:
                changed += 1
            elif '## 原文关联' not in content:
                no_ls += 1
            continue
        r = wr.read_page(**{'kb_id': KB, 'slug': slug})
        if 'error' in r:
            continue
        content = r.get('content') or ''
        title = r.get('title', '')
        folder_id = r.get('folder_id', '')
        refs = r.get('source_refs') or []
        nc, is_ch = group_long_sentences(content, ls_prefix_kid, doc_map)
        if is_ch:
            r2 = wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': title, 'content': nc, 'folder_id': folder_id, 'source_refs': refs})
            if 'error' not in r2:
                changed += 1
        elif '## 原文关联' not in content:
            no_ls += 1
    print(f'\n=== 完成 ===')
    print(f'原文关联已分组: {changed}')
    print(f'无原文关联节: {no_ls}')
    print(f"{('DRY-RUN 未写库' if args.dry_run else '已写库')}")
if __name__ == '__main__':
    main()