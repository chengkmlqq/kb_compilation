#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
业务实体页「来源制度」行存量回填（2026-09-02，坑 105 配套）

背景：创建即合并（merge_page）历史版本只追加 folder_ids/source_refs/原文关联分组/
关键规则，漏维护基本信息表「来源制度」行——跨文档合并实体（source_refs>1）该行
停留在首创建者文档名，与原文关联分组（多组 **《文档名》**）不一致。数据层 source_refs
始终全量（真值），本脚本按 source_refs 并集重写展示行（复用 wr.rewrite_source_line）。

范围：page_type=business_ontology 且 source_refs>1 的页面（规则卡不合并、单来源，跳过）。
幂等：行已是全量并集 → 无变化不 update。

用法：
    python3 backfill_source_line.py --kb <kb_id> [--dry-run] [--limit N] [--slugs s1,s2]
执行后需 wiki_rebuild_links（本脚本只改基本信息表行，无新链接——可跳过，但跑无妨）。
"""
import sys, os, json, argparse, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import weknora_rpc as wr


def load_entity_pages(kb, page_size=200, max_pages=300):
    pages = {}
    page = 1
    while page <= max_pages:
        r = wr.list_wiki_pages(kb_id=kb, page=page, page_size=page_size)
        data = r.get('pages') or []
        for p in data:
            if p.get('page_type') == 'business_ontology':
                pages[p['slug']] = p
        total = r.get('total') or 0
        if not data or page * page_size >= total:
            break
        page += 1
    return pages


def run(kb=None, dry_run=False, limit=None, slugs=None):
    KB = kb or wr.load_kb()
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()
    print(f'[source-line] kb={KB} 拉取全库业务实体页...')
    pages = load_entity_pages(KB)
    targets = sorted(pages) if not slugs else [s for s in slugs if s in pages]
    print(f'[source-line] 业务实体页 {len(pages)} 个，目标 {len(targets)} 个')
    stats = {'checked': 0, 'updated': 0, 'already_full': 0, 'no_line': 0, 'errors': 0}
    for idx, slug in enumerate(targets, 1):
        if limit and idx > limit:
            print(f'[source-line] 达到 --limit {limit}，停止')
            break
        p = pages[slug]
        refs = p.get('source_refs') or []
        if len(refs) <= 1:
            stats['already_full'] += 1  # 单来源本就一致
            continue
        try:
            content = p.get('content') or ''
            new_content = wr.rewrite_source_line(content, refs)
            if new_content == content:
                stats['already_full'] += 1
                continue
            stats['checked'] += 1
            if dry_run:
                print(f"  [DRY] {slug} ({p.get('title')}): 来源 {len(refs)} 篇 → 将重写来源制度行")
                continue
            r = wr.update_page(slug=slug, kb_id=KB, content=new_content, title=p.get('title'),
                               folder_id=p.get('folder_id') or '',
                               folder_ids=p.get('folder_ids') or ([p.get('folder_id')] if p.get('folder_id') else []),
                               source_refs=refs, page_type='business_ontology',
                               status=p.get('status') or 'published')
            if 'error' in r and r.get('isError'):
                stats['errors'] += 1
                print(f"  [ERR] {slug}: {str(r.get('error'))[:120]}")
            else:
                stats['updated'] += 1
                pages[slug]['content'] = new_content
        except Exception as e:
            stats['errors'] += 1
            print(f"  [ERR] {slug}: {e}")
    print(f"[source-line] 完成: 检查 {stats['checked']} | 更新 {stats['updated']}"
          f" | 已一致/单源 {stats['already_full']} | 错误 {stats['errors']}")
    if stats['updated'] and not dry_run:
        print('[source-line] 提示：如后续要做链接重建可执行 wiki_rebuild_links')
    return stats


def main():
    ap = argparse.ArgumentParser(description='业务实体「来源制度」行存量回填（坑 105）')
    ap.add_argument('--kb', default=None, help='知识库 ID（默认 config.yaml）')
    ap.add_argument('--dry-run', action='store_true', help='只打印待更新，不写库')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--slugs', default=None, help='逗号分隔的目标 slug（默认全库 entity-b source_refs>1）')
    args = ap.parse_args()
    slugs = [s.strip() for s in args.slugs.split(',')] if args.slugs else None
    run(kb=args.kb, dry_run=args.dry_run, limit=args.limit, slugs=slugs)


if __name__ == '__main__':
    main()
