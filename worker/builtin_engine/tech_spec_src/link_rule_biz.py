"""规范要求 -> 知识对象反向关联补齐。

正确语义（2026-08-16）：
- 知识对象的“规范要求”由 rebuild_key_rules.py 根据规则明细生成。
- 本脚本只处理规范要求页的“关联对象”表：
  规则明细中的规则对象若能匹配知识对象，则在规范要求页关联对象表中加入
  | [[entity-b-xxx|对象名]] | 反向关联 | <该对象对应的真实规则内容> | <来源> |
- 不修改知识对象规范要求表，不添加“关联规则”列。
"""
import re
import domain
import sys
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

def parse_rule_details(content):
    m = re.search('## 规则明细\\n+(\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', content, re.S)
    if not m:
        return []
    rows = []
    for line in m.group(2).strip().split('\n'):
        cells = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells) < 3:
            continue
        obj, body, src = cells[:3]
        if obj in ('规则对象', '-', ''):
            continue
        link = re.match('\\[\\[([^\\]|]+)\\|([^\\]]+)\\]\\]', obj)
        if link:
            obj_slug, obj_title = (link.group(1), link.group(2))
        else:
            obj_slug, obj_title = ('', obj)
        rows.append((obj_slug, obj_title, body, src))
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
        if not slug.startswith('entity-b-'):
            continue
        biz_by_canon.setdefault(canonicalize(page.get('title') or ''), []).append(slug)
    fixed_pages = 0
    added_rows = 0
    skipped_no_match = 0
    for slug, page in list(entities.items()):
        if not slug.startswith('entity-r-'):
            continue
        content = page.get('content') or ''
        details = parse_rule_details(content)
        if not details:
            continue
        assoc_match = re.search('## 关联对象\\n+(.*?)(?=\\n## |\\Z)', content, re.S)
        assoc_content = assoc_match.group(1) if assoc_match else ''
        rows_to_add = []
        for obj_slug, obj_title, body, src in details:
            if not obj_slug:
                candidates = biz_by_canon.get(canonicalize(obj_title), [])
                if not candidates:
                    skipped_no_match += 1
                    continue
                obj_slug = max(candidates, key=lambda s: len(entities[s].get('folder_ids') or []))
            if f'[[{obj_slug}|' in assoc_content:
                continue
            rows_to_add.append(f'| [[{obj_slug}|{obj_title}]] | 反向关联 | {body} | {src} |')
        if not rows_to_add:
            continue
        m = re.search('(## 关联对象\\n+\\|[^\\n]*\\n\\|[-| ]+\\n)(.*?)(?=\\n## |\\Z)', content, re.S)
        if m:
            new_content = content[:m.end(2)] + '\n' + '\n'.join(rows_to_add) + content[m.end(2):]
        else:
            new_content = content.rstrip() + '\n\n## 关联对象\n\n| 关联对象 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n' + '\n'.join(rows_to_add) + '\n'
        ok, resp = update_page(page, new_content)
        if not ok:
            print(f'ERROR {slug}: {str(resp)[:120]}')
            continue
        fixed_pages += 1
        added_rows += len(rows_to_add)
    print(f'规范要求反向关联补齐: pages={fixed_pages}, rows={added_rows}, skipped_no_match={skipped_no_match}')
if __name__ == '__main__':
    main()