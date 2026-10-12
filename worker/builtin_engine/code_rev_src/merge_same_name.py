"""同名不同本体合并（存量 10 组）：
- 选 folder_ids 最多（共享度最高）的为主页
- 另一页 folder_ids/source_refs 并入主页
- 另一页软删 + slug 写主页 aliases
"""
import sys, json, re
import domain
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
import entity_registry as er
from slugger import canonicalize
wr.load_config()
KB = wr.load_kb()
wr.KB = KB
wr.mcp_init()
pages = []
pn = 1
while True:
    r = wr.list_wiki_pages(**{'kb_id': KB, 'page': pn, 'page_size': 100})
    batch = r.get('pages') or []
    if not batch:
        break
    pages.extend(batch)
    if len(batch) < 100:
        break
    pn += 1
ents = {p['slug']: p for p in pages if er.SLUG_RE.match(p.get('slug') or '') and (not p.get('deleted_at'))}
from collections import defaultdict
groups = defaultdict(list)
for slug, p in ents.items():
    groups[canonicalize(p.get('title') or '')].append(slug)

def get_ont(slug):
    m = re.search('\\*\\*(.+?)\\*\\*\u3000', ents[slug].get('content') or '')
    return m.group(1) if m else '?'

def update_page(slug, args):
    u = wr.update_page(**args)
    return (not u.get('isError'), u)
merged_groups = 0
merged_pages = 0
for cname, slugs in groups.items():
    if len(slugs) < 2:
        continue
    main = max(slugs, key=lambda s: len(ents[s].get('folder_ids') or []))
    slaves = [s for s in slugs if s != main]
    print(f'=== {cname} ===')
    print(f"  主页: {main[:40]} (本体={get_ont(main)}, srcs={len(ents[main].get('source_refs') or [])})")
    for s in slaves:
        mp = ents[main]
        sp = ents[s]
        new_fids = list(dict.fromkeys((mp.get('folder_ids') or [mp.get('folder_id')]) + (sp.get('folder_ids') or [sp.get('folder_id')])))
        new_refs = list(dict.fromkeys((mp.get('source_refs') or []) + (sp.get('source_refs') or [])))
        mc = mp.get('content') or ''
        sc = sp.get('content') or ''
        extras = re.findall('\\n\\*\\*来源文档补充\\*\\*：[^\\n]*\\n\\*\\*涉及文档\\*\\*：\\[\\[[^\\]]+\\]\\]', sc)
        merged_content = mc
        added = 0
        for ex in extras:
            if ex not in merged_content:
                merged_content = merged_content.rstrip() + '\n' + ex.strip() + '\n'
                added += 1
        sp_ont = get_ont(s)
        if sp_ont != get_ont(main):
            merged_content += f'\n> 本体别名：{sp_ont}（{cname}）\n'
        args = {'kb_id': KB, 'slug': main, 'title': mp.get('title'), 'content': merged_content, 'folder_id': new_fids[0], 'folder_ids': new_fids, 'source_refs': new_refs, 'aliases': list(dict.fromkeys((mp.get('aliases') or []) + [sp.get('slug')]))}
        ok, u = update_page(main, args)
        if not ok:
            print(f'  ❌ 主页更新 {main}: {str(u)[:100]}')
            continue
        d = wr.delete_page(**{'kb_id': KB, 'slug': s})
        if d.get('isError'):
            print(f'  ⚠️ 从页删除失败 {s}: {str(d)[:80]}')
        else:
            print(f"  🔀 {s[:40]} (本体={sp_ont}, srcs={len(sp.get('source_refs') or [])}) -> {main[:40]} | fids={len(new_fids)} refs={len(new_refs)} +{added}内容块")
            ents[main]['content'] = merged_content
            ents[main]['folder_ids'] = new_fids
            ents[main]['source_refs'] = new_refs
            ents.pop(s, None)
            merged_pages += 1
    merged_groups += 1
print(f'\n合并完成: {merged_groups} 组, {merged_pages} 从页并入')