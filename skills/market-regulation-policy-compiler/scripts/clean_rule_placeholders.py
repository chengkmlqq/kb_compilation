"""清理规则实体关联实体表中的旧占位反向行。
删除关系说明为“规则对象”/“实体关系”且来源为“-”的行；真实规则明细行保留。
"""
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
KB = wr.load_kb()

def all_pages():
    out = []
    page = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 100})
        b = r.get('pages') or []
        if not b:
            break
        out.extend(b)
        if len(b) < 100:
            break
        page += 1
    return out

def update(p, content):
    args = {'kb_id': KB, 'slug': p['slug'], 'title': p.get('title'), 'content': content, 'folder_id': p.get('folder_id'), 'folder_ids': p.get('folder_ids') or [p.get('folder_id')], 'source_refs': p.get('source_refs') or [], 'aliases': p.get('aliases') or []}
    return wr.update_page(**args)
wr.load_config()
wr.KB = KB
wr.mcp_init()
changed = 0
removed = 0
row_re = re.compile('^\\|.*\\|\\s*(?:反向关联)\\s*\\|\\s*(规则对象|实体关系)\\s*\\|\\s*-\\s*\\|\\s*$')
for p in all_pages():
    if p.get('deleted_at') or not (p.get('slug') or '').startswith('entity-r-'):
        continue
    c = p.get('content') or ''
    if '## 关联实体' not in c:
        continue
    lines = c.splitlines()
    kept = []
    local = 0
    in_assoc = False
    for line in lines:
        if line.startswith('## 关联实体'):
            in_assoc = True
        elif in_assoc and line.startswith('## '):
            in_assoc = False
        if in_assoc and row_re.match(line):
            local += 1
            continue
        kept.append(line)
    if local:
        resp = update(p, '\n'.join(kept) + '\n')
        if resp.get('isError'):
            print('ERROR', p['slug'], str(resp)[:120])
        else:
            changed += 1
            removed += local
print(f'占位反向行清理: pages={changed}, rows={removed}')