#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分册索引页维护（2026-10-11，技术规范域）

目录模型：规范体系根 → 分册 → 版本 → 子目录。
索引页挂在「分册目录」下（不是体系根），因为分册才是文档族：
  slug        = index/<规范体系>-<分册>
  page_type   = concept（前端「原始文档 Wiki」Tab）
  folder_id   = 分册目录 id
内容（确定性生成，无 LLM，避免编造）：
  # <分册> 分册索引
  ## 版本清单          ← 该分册下各版本摘要页（表格：版本 / 摘要页双链 / 状态）
  ## 分册在规范体系中的位置   ← 从最新版本摘要的「## 分册定位」段摘录
  ## 与兄弟分册的关系        ← 汇总各版本摘要「与其他规范文档的关系」表行（去重）

用法：
  python3 update_index.py --kb <kb_id> --root <规范体系> --family <分册> [--dry-run]
  python3 update_index.py --kb <kb_id> --family-all      # 全库维护（按现有分册目录逐个重建）
"""
import argparse
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain
import weknora_rpc as wr

KB = None


def _norm(s):
    return re.sub(r'\s+', '', s or '')


def list_all_folders(kb_id):
    """递归拉全量目录（parent_id='' 逐层展开）。"""
    out, stack = [], ['']
    while stack:
        pid = stack.pop()
        try:
            r = wr.list_folders(kb_id=kb_id, parent_id=pid)
        except Exception:
            continue
        for f in (r.get('folders') or []):
            out.append({'id': f.get('id'), 'name': f.get('name'), 'parent_id': pid})
            stack.append(f.get('id'))
    return out


def list_all_pages(kb_id, page_size=100):
    pages, page = [], 1
    while True:
        r = wr.list_wiki_pages(kb_id=kb_id, page=page, page_size=page_size)
        items = r.get('pages') or r.get('data') or []
        if not items:
            break
        pages.extend(items)
        if len(items) < page_size:
            break
        page += 1
    return pages


def _subtree_ids(folders, root_id):
    ids, stack = {root_id}, [root_id]
    while stack:
        pid = stack.pop()
        for f in folders:
            if f.get('parent_id') == pid and f['id'] not in ids:
                ids.add(f['id'])
                stack.append(f['id'])
    return ids


def _section(content, header):
    m = re.search(r'##\s*' + re.escape(header) + r'\s*\n(.*?)(?=\n##\s|\Z)', content or '', re.S)
    return (m.group(1).strip() if m else '')


def _first_sentence(text, limit=200):
    t = re.sub(r'\s+', ' ', (text or '').strip())
    t = re.sub(r'^（[^）]*）', '', t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + '…'
    return t


def _ver_sort_key(name):
    m = re.search(r'(\d+(?:\.\d+)?)', name or '')
    return (0, -float(m.group(1))) if m else (1, 0)


def build_index(kb_id, root_name, family_name, dry_run=False):
    folders = list_all_folders(kb_id)
    root = next((f for f in folders if f.get('parent_id') == '' and _norm(f['name']) == _norm(root_name)), None)
    if not root:
        print(f"ERROR: 规范体系根不存在: {root_name}")
        return None
    fam = next((f for f in folders if f.get('parent_id') == root['id'] and _norm(f['name']) == _norm(family_name)), None)
    if not fam:
        print(f"ERROR: 分册目录不存在: {family_name}（体系 {root_name}）")
        return None
    sub_ids = _subtree_ids(folders, fam['id'])

    all_pages = list_all_pages(kb_id)
    summaries = []
    for p in all_pages:
        fids = p.get('folder_ids') or ([p.get('folder_id')] if p.get('folder_id') else [])
        if not any(f in sub_ids for f in fids if f):
            continue
        if (p.get('page_type') or '') == 'summary' or str(p.get('slug', '')).startswith('summary/'):
            summaries.append(p)
    if not summaries:
        print(f"WARN: 分册 {family_name} 下无摘要页，索引页跳过")
        return None

    # 每个版本目录取一份摘要（同版本多摘要取 content 最长者）
    by_folder = {}
    for p in summaries:
        fid = (p.get('folder_ids') or [p.get('folder_id')])[0]
        if fid not in by_folder or len(p.get('content') or '') > len(by_folder[fid].get('content') or ''):
            by_folder[fid] = p
    ver_rows = []
    for fid, p in by_folder.items():
        fname = next((f['name'] for f in folders if f['id'] == fid), '')
        ver_rows.append((fname, p))
    ver_rows.sort(key=lambda x: _ver_sort_key(x[0]))

    newest = ver_rows[0][1] if ver_rows else summaries[0]
    positioning = _first_sentence(_section(newest.get('content') or '', '分册定位'))

    # 兄弟分册关系：汇总各版本摘要「与其他规范文档的关系」表行（去重）
    rel_seen, rel_rows = set(), []
    for _fname, p in ver_rows:
        body = _section(p.get('content') or '', '与其他规范文档的关系')
        for line in (body or '').split('\n'):
            line = line.strip()
            if not line.startswith('|') or set(line) <= set('|-: '):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            if not cells or cells[0] in ('相关文档',):
                continue
            key = _norm(cells[0])[:60]
            if key in rel_seen:
                continue
            rel_seen.add(key)
            rel_rows.append(cells[:3])

    lines = [f"# {family_name} 分册索引", '',
             f"**规范体系**：{root_name}　·　**分册**：{family_name}", '',
             '## 版本清单', '',
             '| 版本 | 摘要页 | 说明 |', '|---|---|---|']
    for fname, p in ver_rows:
        lines.append(f"| {fname} | [[{p.get('slug')}|{p.get('title') or fname}]] | {_first_sentence(_section(p.get('content') or '', '分册定位'), 60)} |")
    lines += ['', '## 分册在规范体系中的位置', '', positioning or '（待补充）', '',
              '## 与兄弟分册的关系', '']
    if rel_rows:
        lines += ['| 相关文档 | 关系说明 | 原文依据 |', '|---|---|---|']
        for c in rel_rows:
            cells = (c + ['', ''])[:3]
            lines.append('| ' + ' | '.join(cells) + ' |')
    else:
        lines.append('（暂未从原文中发现与其他分册的明确引用关系）')
    content = '\n'.join(lines) + '\n'

    slug = f"index/{root_name}-{family_name}"
    title = f"{family_name} 分册索引"
    if dry_run:
        print(f"=== DRY RUN {slug} ===")
        print(content[:1500])
        return slug
    r = wr.read_page(slug, kb_id=kb_id)
    exists = not (isinstance(r, dict) and 'error' in r)
    if exists:
        wr.update_page(slug, kb_id=kb_id, title=title, content=content, folder_id=fam['id'],
                       page_type='concept', status=(r.get('status') or 'published'),
                       source_refs=(r.get('source_refs') or []))
        print(f"索引页已更新: {slug}")
    else:
        wr.create_page(slug=slug, title=title, content=content, folder_id=fam['id'],
                       kb_id=kb_id, page_type='concept', source_refs=[])
        wr.update_page(slug, kb_id=kb_id, title=title, content=content, folder_id=fam['id'],
                       page_type='concept', status='published', source_refs=[])
        print(f"索引页已创建: {slug}")
    return slug


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--root', default=None, help='规范体系名（家族根目录名）')
    ap.add_argument('--family', default=None, help='分册名')
    ap.add_argument('--family-all', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ns = ap.parse_args()
    KB = ns.kb or wr.load_kb()
    wr.KB = KB
    wr.load_config()
    wr.mcp_init()
    if ns.family_all:
        folders = [f for f in list_all_folders(KB) if f.get('parent_id')]
        roots = [f for f in list_all_folders(KB) if not f.get('parent_id')]
        root_names = {f['id']: f['name'] for f in roots}
        for f in folders:
            if f.get('parent_id') in root_names:
                build_index(KB, root_names[f['parent_id']], f['name'], dry_run=ns.dry_run)
        return
    if not (ns.root and ns.family):
        print('用法: update_index.py --kb <kb_id> --root <规范体系> --family <分册> [--dry-run]')
        sys.exit(1)
    build_index(KB, ns.root, ns.family, dry_run=ns.dry_run)


if __name__ == '__main__':
    main()
