"""
供管库实体页「来源制度」列：截断 UUID -> 完整文档名 批量修复（2026-08-26）
问题：191 个 entity-b/entity-r 页的「来源制度」写成了《f760d6a3》这种 8 位 kid 前缀，
      应从 source_refs（kid|完整文件名）反查并替换为可读书名。

用法：
  python3 fix_entity_source_docname.py --kb <kb_id> [--dry-run] [--backup /tmp/fix_source_backup.json]
"""
import sys, os, re, json, argparse
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
DEFAULT_KB = wr.load_kb()

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_all_pages(wr, kb):
    pages = []
    pw = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': pw, 'page_size': 200})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        pages.extend(data)
        if len(data) < 200:
            break
        pw += 1
    return pages

def main():
    ap = argparse.ArgumentParser(description='实体页来源制度截断UUID修复')
    ap.add_argument('--kb', default=DEFAULT_KB)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--backup', default='', help='写库前快照 json')
    args = ap.parse_args()
    KB = args.kb
    wr.load_config()
    wr.mcp_init()
    wr.KB = KB
    log('拉取全库页面...')
    pages = load_all_pages(wr, KB)
    log(f'全库页面: {len(pages)}')
    ents = [p for p in pages if p.get('page_type') in ('business_ontology', 'rule_ontology')]
    log(f'实体/规则页: {len(ents)}')
    fixed = 0
    skipped = 0
    errors = 0
    backup = []
    for pg in ents:
        slug = pg.get('slug', '')
        title = pg.get('title', '')
        source_refs = pg.get('source_refs') or []
        folder_id = pg.get('folder_id', '') or (pg.get('folder_ids') or [''])[0]
        page_type = pg.get('page_type', '')
        status = pg.get('status', '') or 'published'
        r = wr.read_page(**{'kb_id': KB, 'slug': slug})
        if 'error' in r:
            log(f"⚠️ wiki_read_page 失败: {slug} -> {r.get('error')}")
            skipped += 1
            continue
        live_content = r.get('content') or ''
        live_title = r.get('title', title)
        live_folder_id = r.get('folder_id', folder_id)
        live_refs = r.get('source_refs') or source_refs
        live_folder_ids = r.get('folder_ids') or ([live_folder_id] if live_folder_id else [])
        truncated = re.findall('《([a-f0-9]{8})》', live_content)
        if not truncated:
            skipped += 1
            continue
        kid_to_docname = {}
        for ref in live_refs:
            if '|' in ref:
                kid, docname = ref.split('|', 1)
                kid_to_docname[kid] = docname
                if len(kid) > 8:
                    kid_to_docname[kid[:8]] = docname

        def replace_uuid(m):
            kid = m.group(1)
            docname = kid_to_docname.get(kid)
            if docname:
                return wr.bookname(docname)
            return m.group(0)
        new_content = re.sub('《([a-f0-9]{8})》', replace_uuid, live_content)
        if new_content == live_content:
            skipped += 1
            continue
        if args.dry_run:
            log(f'[DRY-RUN] 将修复来源制度: {slug} | {live_title}')
            fixed += 1
            continue
        if args.backup:
            backup.append({'slug': slug, 'title': live_title, 'content_before': live_content, 'content_after': new_content})
        upd = wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': live_title, 'content': new_content, 'folder_id': live_folder_id, 'source_refs': live_refs, 'page_type': page_type, 'status': status, **({'folder_ids': live_folder_ids} if live_folder_ids else {})})
        if 'error' in upd:
            log(f"❌ 更新失败: {slug} -> {upd.get('error')}")
            errors += 1
        else:
            log(f'✅ 已修复来源制度: {slug} | {live_title}')
            fixed += 1
    log(f'\n=== 完成 ===')
    log(f'  修复来源制度: {fixed}')
    log(f'  跳过: {skipped}')
    log(f'  错误: {errors}')
    log(f"  {('DRY-RUN 未写库' if args.dry_run else '已写库')}")
    if args.backup and backup:
        with open(args.backup, 'w', encoding='utf-8') as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        log(f'快照已保存: {args.backup}')
    if not args.dry_run and fixed > 0:
        log('\n执行 wiki_rebuild_links 重建索引...')
        rb = wr.rebuild_links(**{'kb_id': KB})
        if 'error' in rb:
            log(f"⚠️ wiki_rebuild_links 返回: {rb.get('error')}")
        else:
            log('wiki_rebuild_links 完成')
if __name__ == '__main__':
    main()