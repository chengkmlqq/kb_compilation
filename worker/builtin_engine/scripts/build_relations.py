"""与其他制度的关系 + 制度血缘（跨文档）——单文件构建的步骤 6b

修订优先（2026-08-26 新增）：
  先找同家族修订类文档（通知/修订说明/主要修订一览表），从中提取权威关系声明，
  再以正文《XXX》引用兜底补充。修订来源的关系更权威（supersedes/based_on 明确）。

用法：
    python build_relations.py <target_kid> [--kb <kb_id>] [--max-depth 5]
输出（打印 + 保存 lineage_<kid前8>.json）：
    - explicit:  目标文档全文《XXX》明确引用（含库内匹配结果）
    - revision_explicit: 修订文档提取的关系（优先）
    - edges:     血缘有向图 {parent_kid: [child_kids]}
    - rel_type:  边关系类型 {'parent|child': based_on|specializes|supersedes}
    - sum_by_kid: 库内文档→摘要页 slug 映射

依赖：weknora_rpc.py（同目录），使用前 wr.KB 或 --kb 指定知识库。
规则细节见 references/relations.md（引用匹配/血缘BFS/去重/修订优先）。
"""
import sys
import json
import re
import os
import collections
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

def load_docs_kb():
    docs, page = ([], 1)
    while True:
        r = wr.list_docs(page=page, page_size=20)
        data = r.get('data') or []
        docs.extend(data)
        if len(data) < 20:
            break
        page += 1
    doc_by_kid = {d['id']: d['file_name'] for d in docs}
    kid_by_doc = {d['file_name']: d['id'] for d in docs}
    return (doc_by_kid, kid_by_doc)

def load_summaries(doc_by_kid, kid_by_doc):
    """全库 summary 页 → {kid: summary_slug}
    同名文档（多个 kid 同 file_name）每个都可能有自己的摘要页：
    用 title→slug 多值映射，逐个 kid 匹配自己的摘要。"""
    import hashlib
    sum_by_kid = {}
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': wr.KB, 'page': page, 'page_size': 100})
        ps = r.get('pages') or []
        pages.extend(ps)
        if len(ps) < 100:
            break
        page += 1
    title_slugs = {}
    for p in pages:
        if p.get('slug', '').startswith('summary/') and (not p.get('deleted_at')):
            t = p.get('title', '')
            if t.endswith(' - Summary'):
                t = t[:-9]
            title_slugs.setdefault(t, []).append(p['slug'])
    for kid, fname in doc_by_kid.items():
        s = 'summary/' + hashlib.md5(kid.encode()).hexdigest()[:16]
        if s in title_slugs.get(fname, []):
            sum_by_kid[kid] = s
            continue
        f2 = fname.replace(' ', '')
        hit = None
        for t, slugs in title_slugs.items():
            if t.replace(' ', '') == f2:
                for sl in slugs:
                    if sl.startswith('summary/' + hashlib.md5(kid.encode()).hexdigest()[:12]):
                        hit = sl
                        break
                if not hit:
                    hit = slugs[0]
                break
        if hit:
            sum_by_kid[kid] = hit
    return sum_by_kid

def is_noise(fname):
    """噪音文档：修订说明/通知/一览表/清单/记录表/附件/xlsx"""
    f = fname.lower()
    if any((k in fname for k in ['修订说明', '通知', '一览表', '清单', '记录表', '主要修订'])):
        return True
    if f.endswith(('.xlsx', '.xls')):
        return True
    if '附件' in fname:
        return True
    return False

def is_revision_type(fname):
    """判断是否为修订类文档（通知/修订说明/主要修订一览表/印发通知）。
    印发通知（如 关于印发《XX》的通知）也包含废止声明和依据声明。"""
    if any((k in fname for k in ['关于修订', '修订印发', '修订说明', '主要修订情况', '主要修订说明'])):
        return True
    if '关于印发' in fname and '通知' in fname:
        return True
    return False

def ref_core(name):
    n = name.replace('中国移动通信集团上海有限公司', '')
    n = n.replace('中国移动通信集团有限公司', '')
    n = n.replace('中国移动上海公司', '')
    n = n.replace('《', '').replace('》', '')
    n = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', n)
    return n.strip()

def is_group_ref(name):
    """引用是否指向集团（非上海）制度"""
    return '集团有限公司' in name and '上海' not in name or '集团公司' in name

def find_doc_by_ref(ref_name, doc_by_kid, exclude_kid=None):
    """引用名 → 库内文档 (kid, fname)｜None。主体一致性 + 核心词双向包含。
    exclude_kid: 排除目标文档自身（同名引用自匹配 bug，2026-08-13 f03/f04 实测）。"""
    core = ref_core(ref_name)
    if not core or len(core) < 4:
        return None
    g = is_group_ref(ref_name)
    for kid, fname in doc_by_kid.items():
        if exclude_kid and kid == exclude_kid:
            continue
        if is_noise(fname):
            continue
        fn = fname.replace('中国移动通信集团上海有限公司', '')
        fn = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', fn)
        if core and (core in fn or fn in core):
            if g and '集团有限公司' not in fname:
                return None
            return (kid, fname)
    return None

def extract_refs(text):
    refs = re.findall('《([^》]{4,60})》', text)
    seen, out = (set(), [])
    for name in refs:
        k = ref_core(name)
        if k and k not in seen:
            seen.add(k)
            out.append(name)
    return out

def fetch_full(kid):
    chunks = wr.fetch_all_chunks(kid)
    if not chunks:
        return ''
    return '\n'.join((wr.clean_chunk_text(c['content']) for c in chunks))

def same_core(kid_a_name, ref_name):
    """核心名相同（都是采购管理办法/细则）→ 旧版替代关系"""
    a = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', kid_a_name)
    a = a.replace('中国移动通信集团上海有限公司', '').strip()
    b = ref_name.replace('中国移动通信集团上海有限公司', '').replace('《', '').replace('》', '')
    return a == b or (a and b and (a in b or b in a))

def rel_type_of(kid_name, ref_name, full, target_kid, doc_by_kid):
    """判定边关系类型：优先 LLM 语义判断，失败时回退到关键词兜底。"""
    target_name = doc_by_kid.get(target_kid, '')
    ref_doc_name = doc_by_kid.get(kid_name, '') if kid_name else ''
    ctx = ''
    idx = full.find(f'《{ref_name}》')
    if idx >= 0:
        ctx = full[max(0, idx - 120):idx + 300]
    try:
        rel_type = _llm_judge_rel_type(ref_name, ctx, target_name, ref_doc_name)
        if rel_type in ('supersedes', 'based_on', 'specializes'):
            return rel_type
    except Exception:
        pass
    if same_core(ref_doc_name, ref_name) or '同时废止' in ctx or '废止' in ctx or ('取代' in ctx) or ('替代' in ctx):
        return 'supersedes'
    if '依据' in ctx or '制定' in ctx or is_group_ref(ref_name) or ref_name.startswith('《中国移动通信集团'):
        return 'based_on'
    return 'specializes'

def _llm_judge_rel_type(ref_name, ctx, target_name, ref_doc_name):
    """用 LLM 根据上下文判断两文档的关系类型。"""
    import weknora_rpc as _wr
    prompt = f'请判断以下两份制度文档之间的关系类型。\n\n被引用文档：{ref_name}\n引用上下文（来自引用方的正文）：{ctx}\n\n请仅输出 JSON，不要额外文字：{{"rel_type": "supersedes|based_on|specializes", "reason": "一句话说明"}}\n\n关系类型定义：\n- supersedes：替代、废止、取代（如"本办法替代/废止了 XX"）\n- based_on：依据、制定、根据（如"依据 XX 制定本办法"）\n- specializes：参照执行、衔接、配套、执行（如"参照 XX 执行"）\n'
    msgs = [{'role': 'user', 'content': prompt}]
    out = _wr.llm_call(msgs, max_tokens=4000, temperature=0.1)
    if not out or not out.strip():
        raise ValueError(f'LLM 返回空内容，可能被截断')
    text = out.strip()
    if text.startswith('```'):
        text = re.sub('^```(?:json)?\\s*', '', text)
        text = re.sub('\\s*```$', '', text)
    m = re.search('\\{.*\\}', text, re.S)
    if not m:
        raise ValueError(f'LLM 输出非 JSON: {text[:200]}')
    data = json.loads(m.group(0))
    return data.get('rel_type')
FAMILY_NOISE = ['修订说明', '主要修订情况', '一览表', '分层分级授权清单', '通知', '附件', '授权清单', '主要']

def family_core(fname):
    """家族核心名：去扩展名/编号前缀/版次/修饰词，并归一 实施细则/细则。"""
    fn = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', fname)
    fn = fn.replace('中国移动通信集团上海有限公司', '')
    fn = re.sub('^[^《]*?〔[^〕]*〕\\s*', '', fn)
    fn = re.sub('^[^《]*?号\\s*', '', fn)
    m = re.search('《([^》]+)》', fn)
    if m:
        fn = m.group(1)
    fn = re.sub('^[A-Z]\\d+\\s*[\\d.]*\\s*年?[，,]?\\s*', '', fn)
    fn = re.sub('^(附件\\d*[：.:]?)\\s*', '', fn)
    fn = re.sub('[（(]V[\\d.]+[)）]', '', fn)
    fn = re.sub('[\\d.]+年?$', '', fn)
    for nz in FAMILY_NOISE:
        fn = fn.replace(nz, '')
    fn = fn.strip(' ：《》')
    # 2026-08-29 坑 95：「XX的通知」类文件名去噪后残留「的」（「…事项的通知」→「…事项的」），
    # 家族根名带"的"（实测 72号通知 →「关于明确部分不纳入采购管理范围的事项的」）
    if fn.endswith('的') and len(fn) > 1:
        fn = fn[:-1]
    fn = fn.replace('实施细则', '细则')
    return fn

def build_family(target_kid, doc_by_kid, edges, rel_type):
    """版本家族归并：按核心名 + supersedes 关系聚类，输出家族根目录名/版本/家族成员。"""
    target_name = doc_by_kid.get(target_kid, '')
    target_core = family_core(target_name)
    members = {}
    for kid, fname in doc_by_kid.items():
        if is_noise(fname):
            continue
        core = family_core(fname)
        if core and core == target_core:
            members[kid] = {'file_name': fname, 'core': core, 'supersedes': [], 'superseded_by': []}
    for a, bl in edges.items():
        for b in bl:
            if rel_type.get(a, {}).get(b) == 'supersedes':
                for k in (a, b):
                    if k in doc_by_kid and k not in members:
                        members[k] = {'file_name': doc_by_kid[k], 'core': family_core(doc_by_kid[k]), 'supersedes': [], 'superseded_by': []}
    for a, bl in edges.items():
        for b in bl:
            if rel_type.get(a, {}).get(b) == 'supersedes' and a in members and (b in members):
                members[a]['supersedes'].append(b)
                members[b]['superseded_by'].append(a)
    family_name = target_core if target_core else family_core(target_name)
    return {'family_name': family_name, 'target_core': target_core, 'target_version_hint': target_name, 'members': members}

def find_sibling_revision_docs(target_kid, doc_by_kid):
    """找同家族内的修订类文档（通知/修订说明/主要修订一览表）。
    返回 [(kid, fname), ...]"""
    target_name = doc_by_kid.get(target_kid, '')
    target_core = family_core(target_name)
    if not target_core:
        return []
    siblings = []
    for kid, fname in doc_by_kid.items():
        if kid == target_kid:
            continue
        if not is_revision_type(fname):
            continue
        fn_core = family_core(fname)
        if fn_core and fn_core == target_core:
            siblings.append((kid, fname))
    return siblings

def extract_refs_from_revision_docs(revision_siblings, doc_by_kid, target_kid):
    """从修订类文档中提取权威关系声明。
    返回 revision_explicit: [{ref, kid, fname, group, source_kid, source_fname, rel_type}]
    """
    revision_explicit = []
    for r_kid, r_fname in revision_siblings:
        try:
            r_text = fetch_full(r_kid)
        except Exception:
            continue
        if not r_text:
            continue
        refs = extract_refs(r_text)
        for ref in refs:
            hit = find_doc_by_ref(ref, doc_by_kid, exclude_kid=target_kid)
            item = {'ref': ref, 'kid': hit[0] if hit else None, 'fname': hit[1] if hit else None, 'group': is_group_ref(ref), 'source_kid': r_kid, 'source_fname': r_fname, 'rel_type': rel_type_of(target_kid, ref, r_text, target_kid, doc_by_kid)}
            revision_explicit.append(item)
            if hit:
                print(f"  修订文档 《{r_fname[:20]}》 提取引用 《{ref}》 -> {hit[1]} ({item['rel_type']})")
            else:
                print(f'  修订文档 《{r_fname[:20]}》 提取引用 《{ref}》 (库内无)')
    return revision_explicit

def merge_relations(revision_explicit, body_explicit, target_kid):
    """合并修订优先和正文提取的关系。
    修订来源的边覆盖正文来源的同名边（修订更权威）。"""
    merged = {}
    seen_cores = set()
    for item in revision_explicit:
        core = ref_core(item['ref'])
        merged[core] = {**item, 'source': 'revision_doc'}
        seen_cores.add(core)
    for item in body_explicit:
        core = ref_core(item['ref'])
        if core not in seen_cores:
            merged[core] = {**item, 'source': 'body_text'}
    return list(merged.values())

def build_relations(target_kid, max_depth=5):
    doc_by_kid, kid_by_doc = load_docs_kb()
    sum_by_kid = load_summaries(doc_by_kid, kid_by_doc)
    print(f'库文档 {len(doc_by_kid)} | 有摘要页 {len(sum_by_kid)}')
    full = fetch_full(target_kid)
    refs = extract_refs(full)
    revision_siblings = find_sibling_revision_docs(target_kid, doc_by_kid)
    if revision_siblings:
        print(f'\n找到同家族修订类文档 {len(revision_siblings)} 篇：')
        for r_kid, r_fname in revision_siblings:
            print(f'  {r_kid[:8]} | {r_fname}')
        print()
    revision_explicit = extract_refs_from_revision_docs(revision_siblings, doc_by_kid, target_kid)
    body_explicit = []
    for ref in refs:
        hit = find_doc_by_ref(ref, doc_by_kid, exclude_kid=target_kid)
        body_explicit.append({'ref': ref, 'kid': hit[0] if hit else None, 'fname': hit[1] if hit else None, 'group': is_group_ref(ref), 'source': 'body_text'})
        if hit:
            print(f'  正文引用 《{ref}》 -> {hit[1]}')
        else:
            print(f'  正文引用 《{ref}》 (库内无)')
    merged_explicit = merge_relations(revision_explicit, body_explicit, target_kid)
    edges, rel_type = ({}, {})

    def add_edge(parent, child, rtype):
        edges.setdefault(parent, set()).add(child)
        rel_type[f'{parent}|{child}'] = rtype
    for item in merged_explicit:
        if item['kid']:
            rtype = item.get('rel_type') or rel_type_of(target_kid, item['ref'], full, target_kid, doc_by_kid)
            add_edge(target_kid, item['kid'], rtype)

    def dlink(kid):
        s = sum_by_kid.get(kid)
        fname = doc_by_kid.get(kid, kid)
        if not s:
            return f'{fname}（无摘要，待补建）'
        return f'[[{s}|{fname}]]'
    rendered = set()

    def tree_lines(kid, depth, path, is_last):
        lines = []
        prefix = ''
        if depth > 0:
            prefix = '  ' * (depth - 1) + ('└── ' if is_last else '├── ')
        fname = doc_by_kid.get(kid, kid)
        if fname not in rendered:
            rendered.add(fname)
            lines.append(prefix + dlink(kid))
        by_name = {}
        for c in edges.get(kid, []):
            if c in path:
                continue
            nm = doc_by_kid.get(c, c)
            if nm not in by_name or (sum_by_kid.get(c) and (not sum_by_kid.get(by_name[nm]))):
                by_name[nm] = c
        children = sorted(by_name.values(), key=lambda c: doc_by_kid.get(c, ''))
        for i, child in enumerate(children):
            lines.extend(tree_lines(child, depth + 1, path | {kid}, i == len(children) - 1))
        return lines
    print('\n=== 制度血缘（节点=文档摘要）===')
    print('\n'.join(tree_lines(target_kid, 0, {target_kid}, False)))
    need_summary = []
    seen_ns = set()

    def collect_no_summary(kid, path):
        for c in edges.get(kid, []):
            if c in path:
                continue
            if not sum_by_kid.get(c):
                nm = doc_by_kid.get(c, c)
                if nm not in seen_ns:
                    seen_ns.add(nm)
                    need_summary.append((c, nm))
            collect_no_summary(c, path | {kid})
    collect_no_summary(target_kid, {target_kid})
    if need_summary:
        print('\n=== 待补建摘要的关联文档（血缘节点必须是文档摘要）===')
        for kid, fname in need_summary:
            print(f'  {kid} | {fname}')
    family = build_family(target_kid, doc_by_kid, edges, rel_type)
    out = {'explicit': merged_explicit, 'revision_explicit': revision_explicit, 'body_explicit': body_explicit, 'edges': {k: sorted(v) for k, v in edges.items()}, 'rel_type': rel_type, 'sum_by_kid': sum_by_kid, 'family': family}
    path = f'lineage_{target_kid[:8]}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\nsaved {path}')
    return out
if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='与其他制度的关系 + 制度血缘（修订优先）')
    ap.add_argument('target_kid', help='目标文档 knowledge_id')
    ap.add_argument('--kb', default=None, help='知识库 id（默认用 wr.KB）')
    ap.add_argument('--max-depth', type=int, default=5, help='血缘最大层数（默认5，防循环）')
    args = ap.parse_args()
    if args.kb:
        wr.KB = args.kb
    if not wr.KB:
        print('请设置 wr.KB 或 --kb 指定知识库 id')
        sys.exit(1)
    wr.mcp_init()
    build_relations(args.target_kid, max_depth=args.max_depth)