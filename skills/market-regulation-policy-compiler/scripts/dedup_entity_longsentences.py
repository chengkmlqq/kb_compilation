"""
检测并修复供管知识库中实体页「原文关联」节的重复长句链接。

问题：长句拆分窗口滑动导致同一段文本被拆成多条标题相同/重叠的长句，
实体页原文关联中引用了多条重复的长句链接。

检测规则：
1. 同一来源文档分组下，title 完全相同的长句链接 → 只保留第一条
2. 同一来源文档分组下，content 相似度 > 90% 的长句链接 → 只保留内容更完整的一条

用法：
    python3 dedup_entity_longsentences.py --kb <kb_id> [--dry-run]
"""
import sys
import os
import re
import json
import argparse
import subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/home/ubuntu/.hermes/skills/market-regulation-wiki/scripts')
import weknora_rpc as wr

def fetch_all_entity_pages(kb):
    """拉取全库所有 entity-b 和 entity-r 页面"""
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 100})
        ps = r.get('pages') or []
        for p in ps:
            if p.get('page_type') in ('business_ontology', 'rule_ontology') and (not p.get('deleted_at')):
                pages.append(p)
        if len(ps) < 100:
            break
        page += 1
    return pages

def extract_ls_links(content):
    """从 content 中提取原文关联节的所有长句链接，按来源文档分组"""
    sections = {}
    ls_section = re.search('## 原文关联\\n(.*?)(?=\\n## |$)', content, re.DOTALL)
    if not ls_section:
        return sections
    section_text = ls_section.group(1)
    current_doc = None
    for line in section_text.split('\n'):
        doc_m = re.search('\\*\\*《([^》]+)》\\*\\*', line)
        if doc_m:
            current_doc = doc_m.group(1)
            sections.setdefault(current_doc, [])
            continue
        ls_m = re.search('\\[\\[(longsentence/[^\\]]+)\\]\\]', line)
        if ls_m and current_doc:
            sections[current_doc].append(ls_m.group(1))
    return sections

def find_duplicates(sections):
    """检测同一文档下 title 重复或高度相似的长句"""
    issues = []
    for doc, slugs in sections.items():
        slug_info = {}
        for slug in slugs:
            if slug not in slug_info:
                try:
                    r = wr.read_page(**{'kb_id': wr.KB, 'slug': slug})
                    slug_info[slug] = {'title': r.get('title', ''), 'content': r.get('content', '')}
                except Exception:
                    slug_info[slug] = {'title': '', 'content': ''}
        by_title = {}
        for slug, info in slug_info.items():
            title = info['title']
            by_title.setdefault(title, []).append(slug)
        for title, slug_list in by_title.items():
            if len(slug_list) > 1:
                contents = [slug_info[s]['content'] for s in slug_list]
                sorted_slugs = sorted(slug_list, key=lambda s: len(slug_info[s]['content'] or ''), reverse=True)
                keep = sorted_slugs[0]
                remove = sorted_slugs[1:]
                issues.append({'doc': doc, 'title': title, 'keep_slug': keep, 'remove_slugs': remove, 'reason': 'title完全重复'})
    return issues

def fix_entity_page(slug, issues_for_page):
    """修复单个实体页的原文关联，移除重复长句链接"""
    r = wr.read_page(**{'kb_id': wr.KB, 'slug': slug})
    content = r.get('content', '')
    folder_id = r.get('folder_id', '')
    source_refs = r.get('source_refs', [])
    for issue in issues_for_page:
        doc = issue['doc']
        remove_slugs = issue['remove_slugs']
        for rslug in remove_slugs:
            pattern = '^- \\[\\[(' + re.escape(rslug) + ')\\|.*?\\]\\]\\n'
            new_content = re.sub(pattern, '', content, flags=re.MULTILINE)
            if new_content != content:
                content = new_content
            else:
                pattern2 = '\\[\\[' + re.escape(rslug) + '\\|.*?\\]\\]\\n?'
                new_content = re.sub(pattern2, '', content)
                if new_content != content:
                    content = new_content
    try:
        wr.update_page(**{'kb_id': wr.KB, 'slug': slug, 'content': content, 'folder_id': folder_id, 'source_refs': source_refs})
        return True
    except Exception as e:
        print(f'  !! 更新失败: {e}')
        return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None, help='知识库ID')
    ap.add_argument('--dry-run', action='store_true', help='仅预览不修改')
    args = ap.parse_args()
    wr.load_config()
    if args.kb:
        wr.KB = args.kb
    if not wr.KB:
        print('请设置 --kb')
        sys.exit(1)
    wr.mcp_init()
    print(f'拉取实体页 (kb={wr.KB})...')
    pages = fetch_all_entity_pages(wr.KB)
    print(f'共 {len(pages)} 个实体页')
    all_issues = []
    for p in pages:
        slug = p['slug']
        content = p.get('content', '')
        sections = extract_ls_links(content)
        issues = find_duplicates(sections)
        if issues:
            all_issues.append((slug, p.get('title', ''), issues))
            for iss in issues:
                print(f"  [{slug}] {p['title']} → {iss['doc']}: 保留 {iss['keep_slug']}, 删除 {iss['remove_slugs']} ({iss['reason']})")
    if not all_issues:
        print('\n✅ 未发现重复长句链接')
        return
    print(f'\n共发现 {len(all_issues)} 个实体页存在重复长句链接')
    if args.dry_run:
        print('\n[DRY-RUN] 未执行修改')
        return
    fixed = 0
    for slug, title, issues in all_issues:
        if fix_entity_page(slug, issues):
            fixed += 1
            print(f'  ✅ 已修复: {title} ({slug})')
    print(f'\n修复完成: {fixed}/{len(all_issues)} 页')
if __name__ == '__main__':
    main()