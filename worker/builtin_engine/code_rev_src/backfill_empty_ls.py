"""
空原文关联对象补齐脚本 v2（2026-08-20）
对原文关联为空或缺失的实体，补全其来源文档中匹配的长句：
1. title 精确匹配：实体 title == 长句 title（最可靠）
2. 核心词降级匹配：去掉"规则/要求/职责/定义/原则"等通用后缀后，匹配长句 title 包含关系

用法：
  python3 backfill_empty_ls.py --kb <kb_id> [--type all/biz/rule] [--dry-run] [--mode title/core/both]
"""
import json, sys, os, re, argparse
import domain
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
GENERIC_SUFFIX = ['规则', '要求', '职责', '定义', '原则', '方式', '情形', '规定', '办法', '范围', '的处理', '的审批', '的确认', '的审核', '的决策', '的内容', '的流程', '的提交', '的依据']

def load_docs(wr, KB):
    doc_map = {}
    pw = 1
    while True:
        r = wr.list_docs(page=pw, page_size=20)
        data = r.get('data', [])
        if not data:
            break
        for d in data:
            doc_map[d['id']] = d.get('file_name', '')
        if len(data) < 20:
            break
        pw += 1
    return doc_map

def load_pages(wr, KB):
    pages = []
    pw = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': pw, 'page_size': 100})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        pages.extend(data)
        if len(data) < 100:
            break
        pw += 1
    return pages

def build_ls_by_doc(pages):
    ls = defaultdict(list)
    for p in pages:
        slug = p.get('slug', '')
        if not slug.startswith('longsentence/'):
            continue
        refs = p.get('source_refs') or []
        if refs:
            ls[refs[0]].append((p.get('title', ''), p.get('content') or '', slug))
    return ls

def core_kw(title):
    """提取核心词（去通用后缀）"""
    t = title
    for suf in GENERIC_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf) + 1:
            t = t[:-len(suf)]
    return t

def find_matches(ls_by_doc, kid, title, mode):
    """在文档 kid 中找匹配长句。返回 [(slug, ls_title)]
    mode: title=精确title；core=核心词包含；prefix=严格前缀（更可靠，降低误链）
    """
    hits = []
    core = core_kw(title)
    for lst, lc, lslug in ls_by_doc.get(kid, []):
        if mode in ('title', 'both') and lst == title:
            hits.append((lslug, lst))
            continue
        if mode == 'core' and core and (core in lst):
            hits.append((lslug, lst))
        if mode == 'prefix' and core and (len(core) >= 4) and lst.startswith(core):
            hits.append((lslug, lst))
    seen = set()
    uniq = []
    for s, t in hits:
        if t not in seen:
            seen.add(t)
            uniq.append((s, t))
    return uniq[:3]

def rebuild_ls(content, refs, ls_by_doc, doc_map, title, mode):
    """重建原文关联节（覆盖空 or 缺失）"""
    if '## 原文关联' not in content:
        return (content, False)
    pre, rest = content.split('## 原文关联', 1)
    if '## ' in rest:
        sec, post = rest.split('## ', 1)
        post = '## ' + post
    else:
        sec, post = (rest, '')
    existing = set(re.findall('\\[\\[(longsentence/[^\\]|]+)', sec))
    doc_groups = {}
    for kid in refs:
        hits = find_matches(ls_by_doc, kid, title, mode)
        if hits:
            doc_groups[kid] = hits
    if not doc_groups:
        return (content, False)
    out = ['## 原文关联', '']
    changed = False
    for kid, hits in doc_groups.items():
        out.append(f"**{wr.bookname(doc_map.get(kid, ''))}**")
        for slug, lst in hits:
            out.append(f'- [[{slug}|{lst}]]')
            if slug not in existing:
                changed = True
        out.append('')
    new_content = pre + '\n'.join(out).rstrip() + '\n\n' + post
    return (new_content, changed or bool(existing == set()))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--type', default='all', choices=['all', 'biz', 'rule'])
    ap.add_argument('--mode', default='both', choices=['title', 'core', 'prefix', 'both'])
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    wr.load_config()
    wr.mcp_init()
    KB = args.kb
    doc_map = load_docs(wr, KB)
    pages = load_pages(wr, KB)
    ls_by_doc = build_ls_by_doc(pages)

    def _ok(slug):
        if args.type == 'biz':
            return slug.startswith('entity-b-')
        if args.type == 'rule':
            return slug.startswith('entity-r-')
        return slug.startswith('entity-b-') or slug.startswith('entity-r-')
    ents = [p for p in pages if _ok(p.get('slug', ''))]
    print(f'实体: {len(ents)}, 匹配模式: {args.mode}')
    filled = 0
    for p in ents:
        content = p.get('content') or ''
        if '## 原文关联' not in content:
            continue
        sec = content.split('## 原文关联')[1]
        sec = sec.split('## ')[0] if '## ' in sec else sec
        if re.findall('\\[\\[(longsentence/[^\\]|]+)', sec):
            continue
        slug = p.get('slug', '')
        if args.dry_run:
            r2 = wr.read_page(**{'kb_id': KB, 'slug': slug})
            if 'error' in r2:
                continue
            refs = r2.get('source_refs') or []
            title = r2.get('title', '')
            nc, ch = rebuild_ls(refs and content, refs, ls_by_doc, doc_map, title, args.mode)
            if ch:
                filled += 1
            continue
        r2 = wr.read_page(**{'kb_id': KB, 'slug': slug})
        if 'error' in r2:
            continue
        full = r2.get('content') or ''
        full_sec = full.split('## 原文关联')[1] if '## 原文关联' in full else ''
        full_sec = full_sec.split('## ')[0] if '## ' in full_sec else full_sec
        if re.findall('\\[\\[(longsentence/[^\\]|]+)', full_sec):
            continue
        refs = r2.get('source_refs') or []
        title = r2.get('title', '')
        folder_id = r2.get('folder_id', '')
        nc, ch = rebuild_ls(full, refs, ls_by_doc, doc_map, title, args.mode)
        if ch:
            wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': title, 'content': nc, 'folder_id': folder_id, 'source_refs': refs})
            filled += 1
    print(f'\n=== 完成 ===')
    print(f'补齐原文关联: {filled}')
    print(f"{('DRY-RUN 未写库' if args.dry_run else '已写库')}")
if __name__ == '__main__':
    main()