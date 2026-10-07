"""
跨文档同名实体合并脚本（2026-08-19 新增）。

用法：
  python merge_duplicate_entities.py --kb <kb_id> [--dry-run]

流程：
1. 扫描全库实体页（entity, business_ontology, rule_ontology）
2. 按 title 分组，找出同名实体组
3. 对每组：
   a. 读取所有页面的完整内容
   b. 提取并合并 关联实体表、关键规则表、原文关联（去重）
   c. 合并 source_refs 和 folder_ids
   d. 更新主页（保留内容最长的页为主页）
   e. 更新 source_refs 去重
   f. 软删重复页
"""
import json, re, sys, os, argparse
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

def extract_table(content, section_title):
    """从 markdown 内容中提取表格，返回行列表。"""
    pattern = f'## {section_title}\\n\\n\\|[^\\n]+\\n\\|[-| ]+\\n(.*?)(?=\\n## |\\n$)'
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return []
    rows = []
    for line in m.group(1).strip().split('\n'):
        line = line.strip()
        if line.startswith('|') and line.endswith('|'):
            rows.append(line)
    return rows

def extract_text_refs(content, section_title):
    """从 markdown 内容中提取某节下的 wiki 链接列表。"""
    pattern = f'## {section_title}\\n\\n(.*?)(?=\\n## |\\n$)'
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return []
    links = re.findall('\\[\\[([^\\]]+)\\]\\]', m.group(1))
    return links

def deduplicate_rows(rows, key_col=0):
    """按指定列去重行。"""
    seen = set()
    result = []
    for row in rows:
        cells = [c.strip() for c in row.split('|')]
        key = cells[key_col] if key_col < len(cells) else row
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result

def merge_entities(kb_id, dry_run=False):
    """执行跨文档同名实体合并。"""
    wr.load_config()
    wr.mcp_init()
    wr.KB = kb_id
    all_pages = []
    page = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb_id, 'page': page, 'page_size': 100})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        all_pages.extend(data)
        if len(data) < 100:
            break
        page += 1
    all_docs = {}
    dp = 1
    while True:
        r = wr.list_docs(**{'kb_id': kb_id, 'page': dp, 'page_size': 20})
        data = r.get('data', [])
        if not data:
            break
        for d in data:
            all_docs[d['id']] = d.get('file_name', d.get('title', '?'))
        if len(data) < 20:
            break
        dp += 1
    entity_pages = [p for p in all_pages if p.get('page_type') in ('entity', 'business_ontology', 'rule_ontology')]
    # 2026-08-30 修复（坑 97）：规则卡（rule_ontology）不合并——坑 76 用户要求规则实体每篇文档独立建页，
    # 且合并用 entity-b 模板重组会丢失规则页「## 规则结构」节、误加「## 关联实体」节。
    # 只合并业务实体（entity/business_ontology）；同名规则卡保留（各自文档独立）。
    entity_pages = [p for p in entity_pages if p.get('page_type') != 'rule_ontology']
    print(f'全库实体页: {len(entity_pages)}')
    by_title = defaultdict(list)
    for p in entity_pages:
        by_title[p.get('title', '')].append(p)
    dups = {t: ps for t, ps in by_title.items() if len(ps) > 1}
    print(f'同名实体组: {len(dups)}')
    if dry_run:
        print(f'\n[DRY RUN] 以下 {len(dups)} 组将合并:')
        for title, pages in sorted(dups.items()):
            slugs = [p['slug'] for p in pages]
            refs = []
            for p in pages:
                refs.extend(p.get('source_refs', []) or [])
            print(f'  {title}: {len(slugs)}页 → 保留 {slugs[0]}, 合并 {slugs[1:]}')
            print(f'    source_refs (去重前): {len(set(refs))} 个')
        return
    merged = 0
    errors = 0
    skipped = 0
    for title, pages in sorted(dups.items()):
        pages.sort(key=lambda p: len(p.get('content', '') or ''), reverse=True)
        main = pages[0]
        dups_to_delete = pages[1:]
        main_slug = main['slug']
        main_content = main.get('content', '') or ''
        main_title = main.get('title', '') or ''
        main_type = main.get('page_type', 'entity')
        main_status = main.get('status', '') or 'published'
        main_fids = list(dict.fromkeys(main.get('folder_ids', []) or []))
        all_refs = []
        for p in pages:
            for r in p.get('source_refs', []) or []:
                if r not in all_refs:
                    all_refs.append(r)
        all_fids = list(main_fids)
        for p in dups_to_delete:
            for f in p.get('folder_ids', []) or []:
                if f not in all_fids:
                    all_fids.append(f)
        all_rel_entities = []
        all_key_rules = []
        all_text_refs = []
        all_source_names = []
        all_content = [main_content]
        for p in dups_to_delete:
            r = wr.read_page(**{'kb_id': kb_id, 'slug': p['slug']})
            if 'error' not in r:
                all_content.append(r.get('content', '') or '')
            else:
                pass
        for content in all_content:
            rel_rows = extract_table(content, '关联实体')
            all_rel_entities.extend(rel_rows)
            rule_rows = extract_table(content, '关键规则')
            all_key_rules.extend(rule_rows)
            text_refs = extract_text_refs(content, '原文关联')
            all_text_refs.extend(text_refs)
        uniq_rel = deduplicate_rows(all_rel_entities, key_col=1)
        uniq_rel.sort()
        uniq_rules = deduplicate_rows(all_key_rules, key_col=1)
        uniq_rules.sort()
        uniq_text = list(dict.fromkeys(all_text_refs))
        header_match = re.match('(# .*?)(?=\\n## 基本信息)', main_content, re.DOTALL)
        basic_info_match = re.search('(## 基本信息\\n\\n\\| 字段 \\| 内容 \\|.*?)(?=\\n## )', main_content, re.DOTALL)
        new_header = header_match.group(1) if header_match else f'# {main_title}'
        new_basic_info = basic_info_match.group(1) if basic_info_match else ''
        # 2026-08-29 修复：source_refs 格式为 "kid|文件名"，all_docs 的 key 是完整 kid。
        # 原写法 all_docs.get(r, r[:8]) 用整串查永远 miss，fallback r[:8] 截出 8 位 UUID 前缀
        # （实测合并 17 页 → 17 页来源制度全变《2ddafef1》）。正确：拆 | 取 kid 查表，查不到用 | 后文件名。
        doc_names = []
        for r in all_refs:
            if '|' in r:
                kid, fname = r.split('|', 1)
                name = all_docs.get(kid, fname)
            else:
                name = all_docs.get(r, r)
            if name not in doc_names:
                doc_names.append(name)
        new_source_line = '；'.join((wr.bookname(n) for n in doc_names))
        new_basic_info = re.sub('\\| 来源制度 \\| [^|]+ \\|', f'| 来源制度 | {new_source_line} |', new_basic_info)
        rel_table = ''
        if uniq_rel:
            rel_table = '| 关联实体 | 关系类型 | 关系说明 | 来源 |\n' + '|---|---|---|---|\n'
            rel_table += '\n'.join(uniq_rel)
        rules_table = ''
        if uniq_rules:
            rules_table = '| 规则类型 | 内容 | 来源 |\n' + '|---|---|---|\n'
            rules_table += '\n'.join(uniq_rules)
        text_refs_section = ''
        if uniq_text:
            text_refs_section = '\n'.join((f'- [[{ref}]]' for ref in uniq_text))
        new_content = f'{new_header}\n\n{new_basic_info}\n\n'
        if rules_table:
            new_content += f'## 关键规则\n\n{rules_table}\n\n'
        if rel_table:
            new_content += f'## 关联实体\n\n{rel_table}\n\n'
        else:
            new_content += '## 关联实体\n\n（暂无关联实体）\n\n'
        rest = re.search('(## 可回答问题.*?)$', main_content, re.DOTALL)
        if rest:
            new_content += rest.group(1)
        else:
            for section in ['原文关联', '可回答问题']:
                m = re.search(f'(## {section}.*?)$', main_content, re.DOTALL)
                if m:
                    new_content += m.group(1) + '\n\n'
            m = re.search('(## 可回答问题.*?)$', main_content, re.DOTALL)
            if m:
                new_content += m.group(1)
        if text_refs_section:
            new_content += f'\n## 原文关联\n\n{text_refs_section}\n\n'
        qa_match = re.search('(## 可回答问题\\n\\n.*?)$', main_content, re.DOTALL)
        if qa_match:
            new_content += qa_match.group(1)
        update_args = {'kb_id': kb_id, 'slug': main_slug, 'title': main_title, 'content': new_content, 'source_refs': all_refs, 'folder_ids': all_fids, 'folder_id': all_fids[0] if all_fids else main.get('folder_id', ''), 'status': main_status, 'page_type': main_type}
        r = wr.update_page(**update_args)
        if r.get('isError') or 'error' in r:
            print(f'  [ERR] 更新 {main_slug}: {str(r)[:100]}')
            errors += 1
            continue
        for p in dups_to_delete:
            slug = p['slug']
            if slug == main_slug:
                continue
            r = wr.delete_page(**{'kb_id': kb_id, 'slug': slug})
            if 'error' in r:
                print(f"  [ERR] 删除 {slug}: {str(r['error'])[:80]}")
                errors += 1
            else:
                merged += 1
                print(f'  ✅ {title}: {slug} → {main_slug} ({len(all_refs)}来源, {len(all_fids)}目录, 关联实体={len(uniq_rel)}, 关键规则={len(uniq_rules)}, 原文关联={len(uniq_text)})')
    print(f'\n=== 合并完成 ===')
    print(f'合并: {merged} 页')
    print(f'错误: {errors} 页')
if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='跨文档同名实体合并')
    ap.add_argument('--kb', default=None, help='知识库 id（默认用 wr.KB）')
    ap.add_argument('--dry-run', action='store_true', help='仅预览，不执行合并')
    args = ap.parse_args()
    merge_entities(args.kb or wr.load_kb(), dry_run=args.dry_run)