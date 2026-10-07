#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量修复 wiki broken_link（2026-08-29 新增）

背景：全量构建后 wiki_lint 报 broken_link（实体/规则页被坑 76 拒建或跨文档分类不一致导致
引用残留）。本脚本对每个 broken 引用：
  1. entity-b-xxx 目标：若库内有同名实体（不同 slug）→ 替换为真实 slug；
     无同名实体 → 降级纯文本（显示名，若有）
  2. entity-r-xxx 目标：规则页不存在 → 降级纯文本（显示名）
  3. summary/xxx 目标：md5 不匹配 → 查真实摘要 slug；查不到 → 降级纯文本
用法：
    python3 fix_broken_links.py --kb <kb_id> [--dry-run] [--lint-file <wiki_lint输出文件>]
"""
import json, os, re, sys, argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import weknora_rpc as wr


def load_broken(lint_file):
    """从 wiki_lint 输出文件解析 broken_link 列表"""
    with open(lint_file, encoding='utf-8') as f:
        raw = f.read()
    m = re.search(r'"result":\s*"(.*)"\s*}$', raw, re.S)
    data = eval(json.loads('"' + m.group(1) + '"'))
    return [i for i in data.get('issues', []) if i.get('type') == 'broken_link']


def build_title_index(KB):
    """全库实体页 title→slug 索引（跨分类同名合并）"""
    idx = {}
    page = 1
    while True:
        r = wr.list_wiki_pages(kb_id=KB, page=page, page_size=500)
        ps = r.get('pages') or r.get('data') or []
        for p in ps:
            t = p.get('title') or ''
            if p.get('page_type') in ('business_ontology', 'rule_ontology') and t:
                idx.setdefault(t, []).append(p['slug'])
        if len(ps) < 500:
            break
        page += 1
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', required=True)
    ap.add_argument('--lint-file', required=True, help='wiki_lint 输出文件（persisted-output 原始文件）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    wr.load_config()
    wr.KB = args.kb
    wr.mcp_init()

    broken = load_broken(args.lint_file)
    print(f"broken_link 总数: {len(broken)}")
    title_idx = build_title_index(args.kb)
    print(f"实体页 title 索引: {len(title_idx)} 个")

    # 汇总每个引用方页面的修复动作（需读页面 content 取真实链接及显示名）
    fixes = {}  # slug -> list of (old_link, new_link)
    page_links = {}  # 页面 -> set(目标 slug)
    for b in broken:
        ps = b.get('page_slug')
        desc = b.get('description', '')
        mm = re.search(r'\[\[([^\]]+)\]\]', desc)
        if not mm:
            continue
        page_links.setdefault(ps, set()).add(mm.group(1))

    for ps, targets in page_links.items():
        r = wr.read_page(ps, kb_id=args.kb)
        if 'error' in r:
            continue
        content = r.get('content', '')
        for tgt in targets:
            # 在 content 中找该目标的带显示名链接
            pat = re.compile(rf'\[\[{re.escape(tgt)}(\|[^\]]+)?\]\]')
            for mm in pat.finditer(content):
                link = mm.group(0)
                disp = mm.group(1)[1:] if mm.group(1) else ''
                new_link = None
                if tgt.startswith('entity-b-'):
                    if disp and disp in title_idx:
                        cands = title_idx[disp]
                        if cands and cands[0] != tgt:
                            new_link = f'[[{cands[0]}|{disp}]]'
                    if not new_link:
                        # 无同名实体 → 降级纯文本（显示名优先）
                        new_link = disp if disp else tgt
                elif tgt.startswith('entity-r-'):
                    new_link = disp if disp else tgt
                elif tgt.startswith('summary/'):
                    # 找标题匹配的真实摘要（disp 为文档名时）
                    if disp:
                        new_link = f'[[{tgt}|{disp}]]'  # 保持，后续单独处理
                    else:
                        new_link = tgt
                if new_link and new_link != link:
                    fixes.setdefault(ps, []).append((link, new_link))

    print(f"引用方页面: {len(fixes)}")
    total_fix = sum(len(v) for v in fixes.values())
    print(f"待修复引用: {total_fix}")

    if args.dry_run:
        for ps, rs in list(fixes.items())[:10]:
            print(f"  {ps}: {len(rs)} 处")
        return 0

    # 执行修复
    done = 0
    for ps, rs in fixes.items():
        r = wr.read_page(ps, kb_id=args.kb)
        if 'error' in r:
            continue
        content = r.get('content', '')
        new_content = content
        for old, new in rs:
            # old 是完整 wikilink（含显示名），直接替换为 new（真实链接或纯文本）
            new_content = new_content.replace(old, new)
        if new_content != content:
            wr.update_page(**{'kb_id': args.kb, 'slug': ps, 'title': r.get('title'),
                              'content': new_content, 'page_type': r.get('page_type'),
                              'status': 'published', 'folder_id': r.get('folder_id'),
                              'folder_ids': r.get('folder_ids'),
                              'source_refs': r.get('source_refs')})
            done += 1
    print(f"修复完成: {done} 页")

    print("\n=== rebuild links ===")
    print(wr.rebuild_links(kb_id=args.kb))
    return 0


if __name__ == '__main__':
    sys.exit(main())
