"""
全量扫描：列出所有 page_type=concept 的页面及其目录归属。
"""
import weknora_rpc as wr
wr.load_config()
wr.mcp_init()
KB = wr.load_kb()
pages, page = ([], 1)
while True:
    r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 200})
    ps = r.get('pages') or []
    pages.extend(ps)
    if len(ps) < 200:
        break
    page += 1
print(f'Total pages: {len(pages)}')
concept_pages = [p for p in pages if p.get('page_type') == 'concept']
print(f'Total concept pages: {len(concept_pages)}')
print('---')
for p in concept_pages:
    slug = p.get('slug', '')
    titles = p.get('title', '')
    folder_id = p.get('folder_id', '')
    cat = p.get('category_path', [])
    content = p.get('content', '') or ''
    has_kw_table = '| 序号 | 关联原文 |' in content
    print(f"{slug[:52]:52s} | folder={folder_id[:8]:8s} | kw_table={has_kw_table} | cat={('/'.join(cat[-3:]) if cat else '?')}")