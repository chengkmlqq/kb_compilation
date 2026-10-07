"""按规则明细重建规则实体的关联实体行。

规则实体的每个规则对象对应一个业务实体；关联实体行必须使用该对象的真实规则内容和来源，
不能保留“规则对象”“实体关系”“-”等占位符。脚本幂等：重复执行不会重复追加。
"""
import re
import sys
from collections import OrderedDict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
from slugger import canonicalize
KB = wr.load_kb()

def list_all_pages():
    pages = []
    page = 1
    while True:
        resp = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 100})
        batch = resp.get('pages') or []
        if not batch:
            break
        pages.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return pages

def parse_details(content):
    m = re.search('## 规则明细\\n+(\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', content, re.S)
    if not m:
        return []
    rows = []
    row_re = re.compile('^\\|\\s*(\\[\\[([^\\]|]+)\\|([^\\]]+)\\]\\]|[^|]+?)\\s*\\|\\s*(.*?)\\s*\\|\\s*(.*?)\\s*\\|\\s*$')
    for line in m.group(2).strip().split('\n'):
        row = row_re.match(line)
        if not row:
            continue
        obj_cell, linked_slug, linked_title, body, source = row.groups()
        obj_cell = obj_cell.strip()
        if obj_cell in ('规则对象', '-', ''):
            continue
        if linked_slug:
            slug, title = (linked_slug, linked_title)
        else:
            slug, title = ('', obj_cell)
        rows.append((slug, title, body.strip(), source.strip()))
    return rows

def update_page(page, content):
    args = {'kb_id': KB, 'slug': page['slug'], 'title': page.get('title'), 'content': content, 'folder_id': page.get('folder_id'), 'folder_ids': page.get('folder_ids') or [page.get('folder_id')], 'source_refs': page.get('source_refs') or [], 'aliases': page.get('aliases') or []}
    resp = wr.update_page(**args)
    return (not resp.get('isError'), resp)

def main():
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()
    pages = list_all_pages()
    entities = {p['slug']: p for p in pages if not p.get('deleted_at') and (p.get('slug') or '').startswith('entity-')}
    biz_by_canon = {}
    for slug, page in entities.items():
        if slug.startswith('entity-b-'):
            biz_by_canon.setdefault(canonicalize(page.get('title') or ''), []).append(slug)
    fixed_pages = 0
    changed_rows = 0
    unmatched = 0
    for rule_slug, page in list(entities.items()):
        if not rule_slug.startswith('entity-r-'):
            continue
        content = page.get('content') or ''
        details = parse_details(content)
        if not details:
            continue
        grouped = OrderedDict()
        for obj_slug, obj_title, body, source in details:
            if not obj_slug:
                candidates = biz_by_canon.get(canonicalize(obj_title), [])
                if not candidates:
                    unmatched += 1
                    continue
                obj_slug = max(candidates, key=lambda s: len(entities[s].get('folder_ids') or []))
            key = (obj_slug, obj_title)
            grouped.setdefault(key, []).append((body, source))
        if not grouped:
            continue
        rows = []
        for (obj_slug, obj_title), items in grouped.items():
            bodies = '；'.join(dict.fromkeys((body for body, _ in items)))
            sources = '；'.join(dict.fromkeys((source for _, source in items)))
            rows.append(f'| [[{obj_slug}|{obj_title}]] | 反向关联 | {bodies} | {sources} |')
        assoc_re = re.compile('(## 关联实体\\n+\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', re.S)
        match = assoc_re.search(content)
        if match:
            old_body = match.group(2).strip()
            target_slugs = {key[0] for key in grouped}
            kept = []
            for line in old_body.split('\n') if old_body else []:
                linked = re.search('\\[\\[([^\\]|]+)\\|', line)
                if linked and linked.group(1) in target_slugs:
                    continue
                kept.append(line)
            new_body = '\n'.join([line for line in kept if line.strip()] + rows)
            new_content = content[:match.start(2)] + new_body + content[match.end(2):]
        else:
            block = '\n\n## 关联实体\n\n| 关联实体 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n' + '\n'.join(rows) + '\n'
            new_content = content.rstrip() + block
        if new_content == content:
            continue
        ok, resp = update_page(page, new_content)
        if not ok:
            print(f'ERROR {rule_slug}: {str(resp)[:120]}')
            continue
        fixed_pages += 1
        changed_rows += len(rows)
    print(f'规则实体关联实体重建: pages={fixed_pages}, rows={changed_rows}, unmatched={unmatched}')
if __name__ == '__main__':
    main()