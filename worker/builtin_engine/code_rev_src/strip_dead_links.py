#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 wiki_lint 报出的断链：把指向不存在页面的 [[slug|文本]] 降级为纯文本。

用法：python3 strip_dead_links.py --kb <kb> --lint-file <wiki_lint 结果 JSON>
"""
import argparse
import json
import re
import sys
from collections import defaultdict

sys.path.insert(0, '/home/ctyun/.hermes/skills/devops/code-reverse-wiki-compiler/scripts')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', required=True)
    ap.add_argument('--lint-file', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ns = ap.parse_args()
    import weknora_rpc as wr
    wr.load_config()
    wr.KB = ns.kb
    wr.mcp_init()
    data = json.load(open(ns.lint_file, encoding='utf-8'))
    issues = data.get('issues') if isinstance(data, dict) else data
    bl = [i for i in (issues or []) if i.get('type') == 'broken_link']
    by_page = defaultdict(list)
    for i in bl:
        by_page[i['page_slug']].append(i['target_slug'])
    print(f'待清理页面 {len(by_page)} 个 / 断链 {len(bl)} 条')
    fixed = 0
    for slug, targets in by_page.items():
        page = wr.read_page(slug, kb_id=ns.kb) or {}
        content = page.get('content') or ''
        new = content
        for t in set(targets):
            new = re.sub(r'\[\[' + re.escape(t) + r'\|([^\]]*)\]\]', r'\1', new)
            new = re.sub(r'\[\[' + re.escape(t) + r'\]\]', '', new)
        if new == content:
            continue
        if ns.dry_run:
            print('  [dry]', slug, len(set(targets)))
            continue
        r = wr.update_page(slug, kb_id=ns.kb, content=new)
        ok = not (isinstance(r, dict) and r.get('error'))
        fixed += 1 if ok else 0
        print(('  ✅ ' if ok else '  ❌ ') + slug + f' ({len(set(targets))} 处)' + ('' if ok else str(r)[:120]))
    print(f'清理完成：{fixed} 页')


if __name__ == '__main__':
    main()
