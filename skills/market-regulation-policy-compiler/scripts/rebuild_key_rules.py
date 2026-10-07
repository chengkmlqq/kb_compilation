"""关键规则表重构（2026-08-16 用户修正）：
关键规则表由规则实体规则明细驱动，规则类型列 = 规则实体名（链接，即规则本体）。
- 对每个业务实体：找"规则明细"中 规则对象==实体名 的行
- 重构关键规则表：| [[rule_slug|规则名]] | 规则内容 | 来源章节 |
  规则名相同的内容行合并为一行（规则名+多条内容）
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
print(f'规则实体: {len(rules)}, 业务实体: {len(bizs)}')
DETAIL_RE = re.compile('^| ([^|]+) | ([^|]+) | 《([^》]+)》([^|]*) |$', re.M)

def parse_details(c):
    m = re.search('## 规则明细\\n+(\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', c, re.S)
    if not m:
        return []
    rows = []
    for line in m.group(2).strip().split('\n'):
        cells = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells) >= 3 and cells[0] not in ('规则对象', '-', ''):
            rows.append((cells[0], cells[1], cells[2]))
    return rows
rule_details = {}
for r_slug, rp in rules.items():
    d = parse_details(rp.get('content') or '')
    if d:
        rule_details[r_slug] = d
print(f'有规则明细的规则实体: {len(rule_details)}')
biz_canon = {}
for b_slug, bp in bizs.items():
    biz_canon.setdefault(canonicalize(bp.get('title') or ''), []).append(b_slug)

def update_page(slug, new_content):
    p = ents[slug]
    args = {'kb_id': KB, 'slug': slug, 'title': p.get('title'), 'content': new_content, 'folder_id': p.get('folder_id'), 'folder_ids': p.get('folder_ids') or [p.get('folder_id')], 'source_refs': p.get('source_refs') or [], 'aliases': p.get('aliases') or []}
    u = wr.update_page(**args)
    return (not u.get('isError'), u)
KEYRULE_RE = re.compile('## 关键规则\\n+(\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', re.S)
rebuilt = 0
no_rule = 0
for b_slug, bp in bizs.items():
    b_cname = canonicalize(bp.get('title') or '')
    rows = []
    for r_slug, details in rule_details.items():
        r_title = rules[r_slug].get('title', '')
        for obj, content, src in details:
            if canonicalize(obj) == b_cname:
                rows.append((r_slug, r_title, content, src))
    if not rows:
        no_rule += 1
        continue
    from collections import OrderedDict
    grouped = OrderedDict()
    for r_slug, r_title, content, src in rows:
        grouped.setdefault((r_slug, r_title), []).append((content, src))
    lines = ['## 关键规则', '', '| 规则类型 | 内容 | 来源 |', '|---|---|---|']
    for (r_slug, r_title), items in grouped.items():
        link = '[[%s|%s]]' % (r_slug, r_title)
        contents = '；'.join((c for c, s in items))
        srcs = '；'.join(dict.fromkeys((s for c, s in items)))
        lines.append('| %s | %s | %s |' % (link, contents, srcs))
    new_table = '\n'.join(lines) + '\n'
    c = bp.get('content') or ''
    m = KEYRULE_RE.search(c)
    if m:
        new_content = c[:m.start()] + new_table + c[m.end():]
    else:
        idx = c.find('## 关联实体')
        if idx >= 0:
            new_content = c[:idx] + new_table + '\n' + c[idx:]
        else:
            new_content = c.rstrip() + '\n\n' + new_table
    ok, u = update_page(b_slug, new_content)
    if not ok:
        print(f'  ❌ {b_slug}: {str(u)[:100]}')
    else:
        bizs[b_slug]['content'] = new_content
        rebuilt += 1
print(f'重构关键规则表: {rebuilt} 页 (无匹配规则 {no_rule})')