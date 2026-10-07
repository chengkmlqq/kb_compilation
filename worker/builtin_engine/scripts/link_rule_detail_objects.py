"""规则明细表"规则对象"列加链接（2026-08-16 用户要求）：
规则实体页规则明细表 | 规则对象 | 规则内容 | 来源 | 中
规则对象列 纯文本 → [[entity-b-xxx|对象名]]（canonical 匹配业务实体）
"""
import sys, re
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
ents = {p['slug']: p for p in pages if (p.get('slug', '').startswith('entity-b-') or p.get('slug', '').startswith('entity-r-')) and (not p.get('deleted_at'))}
rules = {s: p for s, p in ents.items() if s.startswith('entity-r-')}
bizs = {s: p for s, p in ents.items() if s.startswith('entity-b-')}
biz_canon = {}
for b_slug, bp in bizs.items():
    biz_canon.setdefault(canonicalize(bp.get('title') or ''), []).append(b_slug)

def update_page(slug, new_content):
    p = ents[slug]
    args = {'kb_id': KB, 'slug': slug, 'title': p.get('title'), 'content': new_content, 'folder_id': p.get('folder_id'), 'folder_ids': p.get('folder_ids') or [p.get('folder_id')], 'source_refs': p.get('source_refs') or [], 'aliases': p.get('aliases') or []}
    u = wr.update_page(**args)
    return (not u.get('isError'), u)
DETAIL_LINE_RE = re.compile('^\\| ([^|\\[\\]]+) \\| ([^|]+) \\| 《([^》]+)》([^|]*) \\|$', re.M)
fixed_rules = 0
linked_rows = 0
no_match_rows = 0
for r_slug, rp in rules.items():
    c = rp.get('content') or ''
    m = re.search('## 规则明细\\n+(\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', c, re.S)
    if not m:
        continue
    new_block = []
    changed = False
    for line in m.group(2).strip().split('\n'):
        mm = DETAIL_LINE_RE.match(line)
        if not mm:
            new_block.append(line)
            continue
        obj, content, src_file, src_ch = mm.groups()
        obj = obj.strip()
        if '[[' in obj:
            new_block.append(line)
            continue
        cname = canonicalize(obj)
        cands = biz_canon.get(cname, [])
        if cands:
            b_slug = max(cands, key=lambda s: len(bizs[s].get('folder_ids') or []))
            new_line = '| [[%s|%s]] | %s | %s%s |' % (b_slug, obj, content.strip(), wr.bookname(src_file), src_ch)
            new_block.append(new_line)
            changed = True
            linked_rows += 1
        else:
            new_block.append(line)
            no_match_rows += 1
    if changed:
        new_content = c[:m.start(2)] + '\n'.join(new_block) + c[m.end(2):]
        ok, u = update_page(r_slug, new_content)
        if not ok:
            print(f'  ❌ {r_slug}: {str(u)[:100]}')
        else:
            rules[r_slug]['content'] = new_content
            fixed_rules += 1
print(f'规则实体页处理: {fixed_rules}, 规则对象链接: {linked_rows}, 未匹配(保持原样): {no_match_rows}')