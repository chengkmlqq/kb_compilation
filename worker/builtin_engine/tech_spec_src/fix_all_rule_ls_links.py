"""批量修复全库 entity-r 页原文关联（2026-08-19）。

扫描所有 entity-r 页面，若原文关联为（待补充），
按标题匹配策略查找同文档长句，填充原文关联。
"""
import re, sys, os
import domain
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

def fetch_all_pages(kb):
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        pages.extend(ps)
        if len(ps) < 200:
            break
        page += 1
    return pages

def update_page(kb, p, new_content):
    args = {'kb_id': kb, 'slug': p.get('slug'), 'title': p.get('title'), 'content': new_content, 'folder_id': p.get('folder_id'), 'folder_ids': p.get('folder_ids') or [p.get('folder_id')], 'source_refs': p.get('source_refs') or [], 'aliases': p.get('aliases') or [], 'status': p.get('status') or 'published', 'page_type': p.get('page_type') or p.get('slug', '').split('/')[0]}
    u = wr.update_page(**args)
    return (not u.get('isError'), u)

def main():
    wr.load_config()
    KB = wr.KB
    if not KB:
        print('请在 config.yaml 中设置 weknora.kb_id')
        sys.exit(1)
    wr.mcp_init()
    print('拉取全库页面...')
    pages = fetch_all_pages(KB)
    rule_by_kid = defaultdict(list)
    ls_by_kid = defaultdict(list)
    for p in pages:
        if p.get('deleted_at'):
            continue
        srefs = p.get('source_refs') or []
        slug = p.get('slug', '')
        if slug.startswith('entity-r-'):
            for kid in srefs:
                rule_by_kid[kid].append(p)
        elif slug.startswith('longsentence/'):
            for kid in srefs:
                ls_by_kid[kid].append(p)
    SUFFIXES = ['规则', '原则', '要求', '说明', '定义', '流程', '规定', '办法', '制度', '条件', '约束', '审批', '决策', '职责', '引用', '例外', '时间', '金钱', '版本', '模式', '情形', '事项', '工作']
    total_fixed = 0
    for kid, rules in rule_by_kid.items():
        lss = ls_by_kid.get(kid, [])
        if not lss:
            continue
        ls_title_map = {}
        for lsp in lss:
            ls_title_map[lsp.get('title', '')] = lsp['slug']
        for rp in rules:
            c = rp.get('content') or ''
            if '（待补充）' not in c:
                continue
            r_title = rp.get('title', '')
            matched = []
            if r_title in ls_title_map:
                matched.append((ls_title_map[r_title], r_title))
            if not matched:
                for ls_title, ls_slug in ls_title_map.items():
                    if r_title in ls_title or ls_title in r_title:
                        matched.append((ls_slug, ls_title))
                        break
            if not matched:
                core = r_title
                for suf in SUFFIXES:
                    if core.endswith(suf) and len(core) > len(suf) + 1:
                        core = core[:-len(suf)]
                        break
                if core != r_title and len(core) >= 3:
                    for ls_title, ls_slug in ls_title_map.items():
                        if core in ls_title:
                            matched.append((ls_slug, ls_title))
                            break
            if not matched:
                for lsp in lss:
                    ls_body = re.sub('^# .+\\n\\n', '', lsp.get('content', ''), count=1)
                    if r_title in ls_body or core in ls_body:
                        matched.append((lsp['slug'], lsp.get('title', '')))
                        break
            if matched:
                ls_section = '\n'.join((f'- [[{s}|{t}]]' for s, t in matched[:3]))
                new_c = re.sub('## 原文关联\\n\\n- （待补充）', f'## 原文关联\n\n{ls_section}', c)
                if new_c != c:
                    ok, u = update_page(KB, rp, new_c)
                    if ok:
                        total_fixed += 1
                        print(f"  ✓ {rp['slug']} ({r_title}) → {matched[0][1]}")
                    else:
                        print(f"  ❌ {rp['slug']}: {str(u)[:120]}")
            else:
                print(f"  ⚠ {rp['slug']} ({r_title}): 未找到匹配长句")
    print(f'\n完成：修复 {total_fixed} 个规范要求页')
if __name__ == '__main__':
    main()